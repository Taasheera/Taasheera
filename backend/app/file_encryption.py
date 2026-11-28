"""Application-level encryption of traveler files at rest (CMPS271-114), on top of the encrypted EBS volume.

Passport images and their confirmed details, supporting documents and the text read from them, and
the agent's screenshots are sealed with AES-256-GCM under DOCUMENT_ENCRYPTION_KEY (32 random bytes,
base64). The associated data is the file's place below the storage root (`<owner id>/.../<file id>.<type>`),
so a file copied into another traveler's folder, renamed or altered fails to decrypt.

The key is the switch. Without one, files are written as before and the process logs one warning at
startup. With one, every new file is encrypted and reads accept both formats, told apart by a versioned
header (`\\x89TSENC` and a version byte; `\\x89` can't start UTF-8 text, JSON, JPEG or PDF, and the next
byte differs from PNG's), so existing files keep working until `python manage.py encrypt-documents`
encrypts them in place. Losing the key loses every encrypted file: keep a copy apart from the data backups.
"""

import base64
import binascii
import logging
import os
import re
import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

MAGIC = b"\x89TSENC"
VERSION_1 = b"\x01"
NONCE_BYTES = 12

_STORED_FILE = re.compile(r"[0-9a-f]{32}\.(?:jpg|json)|documents/[0-9a-f]{32}\.(?:pdf|jpg|txt)"
                          r"|agent/[0-9a-f]{32}/[0-9a-f]{32}\.png")


class FileDecryptionError(RuntimeError):
    """A stored file can't be read: the key is missing or wrong, or the file was moved or altered."""


@dataclass(frozen=True)
class FileCipher:
    key: bytes = field(repr=False)

    @classmethod
    def from_environment(cls) -> "FileCipher | None":
        raw = os.environ.get("DOCUMENT_ENCRYPTION_KEY", "").strip()
        if not raw:
            return None
        try:
            key = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            key = b""
        if len(key) != 32:
            raise RuntimeError("DOCUMENT_ENCRYPTION_KEY must be 32 random bytes, base64-encoded "
                               "(for example from: openssl rand -base64 32).")
        return cls(key)

    def encrypt(self, data: bytes, name: str) -> bytes:
        header = MAGIC + VERSION_1
        nonce = secrets.token_bytes(NONCE_BYTES)
        return header + nonce + AESGCM(self.key).encrypt(nonce, data, header + name.encode("utf-8"))

    def decrypt(self, data: bytes, name: str) -> bytes:
        header = data[:len(MAGIC) + 1]
        if header != MAGIC + VERSION_1:
            raise FileDecryptionError("Unknown file encryption version.")
        nonce = data[len(header):len(header) + NONCE_BYTES]
        try:
            return AESGCM(self.key).decrypt(nonce, data[len(header) + NONCE_BYTES:], header + name.encode("utf-8"))
        except (InvalidTag, ValueError):
            raise FileDecryptionError("A stored file could not be decrypted.") from None


def cipher_from_environment() -> FileCipher | None:
    cipher = FileCipher.from_environment()
    if cipher is None:
        logger.warning("DOCUMENT_ENCRYPTION_KEY is not set: traveler files are stored without "
                       "application-level encryption.")
    return cipher


def is_encrypted(data: bytes) -> bool:
    return data.startswith(MAGIC)


def stored_name(root: Path, path: Path) -> str:
    """The associated data: where the file sits below the storage root, starting with the owner's id."""
    return path.relative_to(root).as_posix()


def seal(cipher: FileCipher | None, root: Path, path: Path, data: bytes) -> bytes:
    return data if cipher is None else cipher.encrypt(data, stored_name(root, path))


def read_sealed(cipher: FileCipher | None, root: Path, path: Path) -> bytes:
    """The file's content, decrypted if it was stored encrypted. Logs never name the file."""
    data = path.read_bytes()
    if not is_encrypted(data):
        return data
    if cipher is None:
        logger.error("An encrypted traveler file was read without DOCUMENT_ENCRYPTION_KEY")
        raise FileDecryptionError("DOCUMENT_ENCRYPTION_KEY is needed to read this file.")
    try:
        return cipher.decrypt(data, stored_name(root, path))
    except FileDecryptionError:
        logger.error("A traveler file failed to decrypt (wrong key, or moved or altered)")
        raise


def _replace_encrypted(path: Path, data: bytes) -> None:
    """Swap in the encrypted copy atomically, keeping the file's times (the newest review wins) and owner."""
    status = path.stat()
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.utime(temporary, ns=(status.st_atime_ns, status.st_mtime_ns))
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(temporary, status.st_uid, status.st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def encrypt_existing_files(root: Path, cipher: FileCipher, owner_lock) -> dict[str, int]:
    """Encrypt every plaintext traveler file below root in place; running it again changes nothing.

    `owner_lock(owner_id)` is the owner's account lock, which every signed-in request and the agent
    worker hold while they use that owner's files; it raises TimeoutError when the owner stays busy.
    Already encrypted files are checked against the key. Returns counts only, never names.
    """
    counts = {"encrypted": 0, "already_encrypted": 0, "unreadable": 0, "busy_owners": 0}
    owners = sorted((folder for folder in root.iterdir() if folder.name.isascii() and folder.name.isdigit()),
                    key=lambda folder: int(folder.name))
    for folder in owners:
        if folder.is_symlink() or not folder.is_dir():
            continue
        try:
            with owner_lock(int(folder.name)):
                for directory, subdirectories, files in os.walk(folder):
                    subdirectories.sort()
                    for filename in sorted(files):
                        path = Path(directory) / filename
                        if path.is_symlink() or not _STORED_FILE.fullmatch(path.relative_to(folder).as_posix()):
                            continue
                        data = path.read_bytes()
                        if not is_encrypted(data):
                            _replace_encrypted(path, cipher.encrypt(data, stored_name(root, path)))
                            counts["encrypted"] += 1
                            continue
                        try:
                            cipher.decrypt(data, stored_name(root, path))
                            counts["already_encrypted"] += 1
                        except FileDecryptionError:
                            counts["unreadable"] += 1
        except TimeoutError:
            counts["busy_owners"] += 1
    return counts
