"""Scan every uploaded file before anything parses or stores it (CMPS271-31), behind `malware_scan`.

The built-in scanner always runs: the EICAR test signature, executable headers (Windows MZ/PE, Linux ELF,
macOS Mach-O), PDFs with active content (JavaScript, launch actions, embedded files, additional actions,
including inside compressed object streams and hex-escaped names), and content whose leading bytes are not
the accepted type the upload declared. Unsupported or undeclared types are left to the type checks that
follow, which answer as before. An /OpenAction that runs JavaScript is caught by its /JavaScript action.

ClamAV also runs when CLAMD_ADDRESS=host:port names a clamd (TCP INSTREAM, standard library only). If it is
configured but doesn't give a clear answer, the upload fails closed with 503: an unscanned file is never
stored. clamd needs about 1 GB of memory, so the t3.micro host runs the built-in scanner only.

A rejected upload gets one fixed message (422), nothing is written, an `upload_rejected` activity event
records the reason code, and the log line holds metadata only: never the file name, content or owner.
"""

import base64
import bisect
import logging
import os
import re
import socket
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException

log = logging.getLogger(__name__)

REJECTED_MESSAGE = ("This file didn't pass our security check, so it wasn't saved. "
                    "Upload another copy of the document, such as a new scan or photo.")
UNAVAILABLE_MESSAGE = "Upload scanner unavailable. Please try again in a few minutes."

PASSPORT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
DOCUMENT_TYPES = PASSPORT_TYPES | {"application/pdf"}

EICAR_SIGNATURE = base64.b64decode(
    "WDVPIVAlQEFQWzRcUFpYNTQoUF4pN0NDKTd9JEVJQ0FSLVNUQU5EQVJELUFOVElWSVJVUy1URVNULUZJTEUhJEgrSCo=")
EXECUTABLE_HEADERS = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe",
                      b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "application/x-pdf": "application/pdf"}

_PDF_NAME = re.compile(rb"/([^\x00\t\n\x0c\r ()<>\[\]{}/%]+)")
_PDF_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")
_PDF_STREAM_HEAD = re.compile(rb">>\s*stream(?:\r\n|\n|\r)")
_PDF_STREAM_START = re.compile(rb"(?<!end)stream(?:\r\n|\n|\r)")
PDF_ACTIVE_NAMES = frozenset({b"JavaScript", b"Launch", b"EmbeddedFile", b"EmbeddedFiles", b"FileAttachment"})
PDF_ACTIVE_KEYS = frozenset({b"JS", b"AA", b"EF"})
_PDF_FILTERS = frozenset({b"FlateDecode", b"Fl", b"ASCIIHexDecode", b"AHx", b"ASCII85Decode", b"A85",
                          b"LZWDecode", b"LZW", b"RunLengthDecode", b"RL", b"CCITTFaxDecode", b"CCF",
                          b"JBIG2Decode", b"DCTDecode", b"DCT", b"JPXDecode", b"Crypt"})
MAX_OBJECT_STREAMS = 1000
MAX_INFLATED_BYTES = 16 * 1024 * 1024
_HEADER_WINDOW = 4096

CLAMD_TIMEOUT_SECONDS = 30
CLAMD_CHUNK_BYTES = 64 * 1024


class Infected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ScannerUnavailable(Exception):
    pass


class Scanner(Protocol):
    def scan(self, data: bytes, declared_type: str | None, allowed: frozenset[str]) -> None:
        """Return when the file is clean; raise Infected, or ScannerUnavailable when it can't tell."""


def detected_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _declared(content_type: str | None) -> str:
    value = (content_type or "").split(";", 1)[0].strip().lower()
    return _ALIASES.get(value, value)


def _names(data: bytes) -> list[tuple[int, bytes]]:
    return [(match.start(), _PDF_ESCAPE.sub(lambda escape: bytes([int(escape.group(1), 16)]), match.group(1)))
            for match in _PDF_NAME.finditer(data)]


def _outside_streams(data: bytes) -> bytes:
    parts, position = [], 0
    while (head := _PDF_STREAM_HEAD.search(data, position)) is not None:
        end = data.find(b"endstream", head.end())
        if end < 0:
            break
        parts.append(data[position:head.end()])
        position = end + len(b"endstream")
    parts.append(data[position:])
    return b" ".join(parts)


def _object_stream_text(data: bytes, position: int, stream_starts: list[int], budget: int) -> bytes | None:
    index = bisect.bisect_left(stream_starts, position)
    if index == len(stream_starts) or stream_starts[index] - position > _HEADER_WINDOW:
        return None
    start, window = stream_starts[index], max(0, position - _HEADER_WINDOW)
    opening = data.rfind(b"obj", window, position)
    filters = {name for _, name in _names(data[opening if opening >= 0 else window:start])} & _PDF_FILTERS
    body = data[_PDF_STREAM_START.match(data, start).end():]
    if not filters:
        end = body.find(b"endstream")
        return body[:end] if end >= 0 else body
    if not filters <= {b"FlateDecode", b"Fl"}:
        return None
    try:
        return zlib.decompressobj().decompress(body, budget + 1)
    except zlib.error:
        return None


def pdf_has_active_content(data: bytes) -> bool:
    names = _names(data)
    if any(name in PDF_ACTIVE_NAMES for _, name in names):
        return True
    if any(name in PDF_ACTIVE_KEYS for _, name in _names(_outside_streams(data))):
        return True
    object_streams = [position for position, name in names if name == b"ObjStm"]
    if len(object_streams) > MAX_OBJECT_STREAMS:
        return True
    stream_starts = [match.start() for match in _PDF_STREAM_START.finditer(data)]
    budget = MAX_INFLATED_BYTES
    for position in object_streams:
        text = _object_stream_text(data, position, stream_starts, budget)
        if text is None or len(text) > budget:
            return True
        budget -= len(text)
        if any(name in PDF_ACTIVE_NAMES | PDF_ACTIVE_KEYS for _, name in _names(text)):
            return True
    return False


class BuiltinScanner:
    def scan(self, data: bytes, declared_type: str | None, allowed: frozenset[str]) -> None:
        if EICAR_SIGNATURE in data:
            raise Infected("eicar")
        if data.startswith(EXECUTABLE_HEADERS):
            raise Infected("executable")
        content_type = detected_type(data)
        declared = _declared(declared_type)
        if declared in allowed and content_type != declared:
            raise Infected("type_mismatch")
        if content_type == "application/pdf" and pdf_has_active_content(data):
            raise Infected("pdf_active_content")


class ClamdScanner:
    """clamd's INSTREAM command over TCP: `zINSTREAM\\0`, length-prefixed chunks, then a zero length."""

    def __init__(self, host: str, port: int, *, timeout: float = CLAMD_TIMEOUT_SECONDS):
        self.host, self.port, self.timeout = host, port, timeout

    def scan(self, data: bytes, declared_type: str | None, allowed: frozenset[str]) -> None:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as connection:
                connection.sendall(b"zINSTREAM\0")
                for offset in range(0, len(data), CLAMD_CHUNK_BYTES):
                    chunk = data[offset:offset + CLAMD_CHUNK_BYTES]
                    connection.sendall(struct.pack(">I", len(chunk)) + chunk)
                connection.sendall(struct.pack(">I", 0))
                reply = b""
                while not reply.endswith(b"\0") and len(reply) < 4096:
                    received = connection.recv(4096)
                    if not received:
                        break
                    reply += received
        except OSError:
            raise ScannerUnavailable() from None
        answer = reply.rstrip(b"\0").strip()
        if answer.endswith(b" FOUND"):
            raise Infected("clamav")
        if answer != b"stream: OK":
            raise ScannerUnavailable()


def clamd_address(raw: str) -> tuple[str, int]:
    host, separator, port = raw.rpartition(":")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if (not separator or not host or any(char.isspace() for char in host)
            or not port.isascii() or not port.isdigit() or not 0 < int(port) < 65536):
        raise RuntimeError("CLAMD_ADDRESS must be host:port, for example clamav:3310.")
    return host, int(port)


@dataclass(frozen=True)
class UploadScanner:
    scanners: tuple[Scanner, ...]

    @classmethod
    def from_environment(cls) -> "UploadScanner":
        raw = os.environ.get("CLAMD_ADDRESS", "").strip()
        scanners: list[Scanner] = [BuiltinScanner()]
        if raw:
            scanners.append(ClamdScanner(*clamd_address(raw)))
        return cls(tuple(scanners))

    def scan(self, data: bytes, declared_type: str | None, allowed: frozenset[str]) -> None:
        for scanner in self.scanners:
            scanner.scan(data, declared_type, allowed)


def create_upload_check(scanner: Scanner, allowed: frozenset[str], *, source: str,
                        enabled: Callable[[], bool], record_rejection: Callable[[int, str, str], None]):
    """The check an upload route runs on the bytes it received, before checking or storing them.

    `record_rejection(traveler_id, identifier, reason)` saves the activity event; the identifier is
    "passport" or the questionnaire's requirement id, never anything from the file.
    """

    def check(traveler_id: int, identifier: str, data: bytes, declared_type: str | None) -> None:
        if not enabled():
            return
        try:
            scanner.scan(data, declared_type, allowed)
        except Infected as found:
            log.warning("Upload rejected by the malware scan (source=%s, reason=%s, bytes=%d)",
                        source, found.reason, len(data))
            record_rejection(traveler_id, identifier, found.reason)
            raise HTTPException(status_code=422, detail=REJECTED_MESSAGE) from None
        except ScannerUnavailable:
            log.error("Upload refused: the malware scanner is unavailable (source=%s)", source)
            raise HTTPException(status_code=503, detail=UNAVAILABLE_MESSAGE, headers={"Retry-After": "60"}) from None

    return check
