"""Private files for supporting documents, inside the traveler's passport storage folder.

`<root>/<traveler_id>/documents/<document_id>.<pdf|jpg>`, with the text read from it next to
it as `<document_id>.txt`. Keeping them under the traveler's folder means account erasure
removes them with the passport photos. Ids are our own random hex, so client file names
never reach a path, and every path goes through the owner's own folder.
"""

import os
import re
import uuid
from pathlib import Path

_DOCUMENT_ID = re.compile(r"[0-9a-f]{32}")
_EXTENSIONS = ("pdf", "jpg")


class DocumentStorage:
    def __init__(self, root: Path | str, *, on_access=None):
        self.root = Path(root)
        self.on_access = on_access

    def _folder(self, owner_id: int) -> Path:
        return self.root / str(int(owner_id)) / "documents"

    def _path(self, owner_id: int, document_id: str, extension: str) -> Path:
        if not _DOCUMENT_ID.fullmatch(document_id) or extension not in _EXTENSIONS + ("txt",):
            raise ValueError("Invalid document id")
        return self._folder(owner_id) / f"{document_id}.{extension}"

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    def save(self, owner_id: int, document_id: str, extension: str, content: bytes, text: str) -> None:
        folder = self._folder(owner_id)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._write(self._path(owner_id, document_id, extension), content)
        self._write(self._path(owner_id, document_id, "txt"), text.encode("utf-8"))

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        # Owner-only permissions; O_EXCL refuses to overwrite an existing file.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(data)

    def load(self, owner_id: int, document_id: str, extension: str, *, purpose: str) -> bytes | None:
        path = self._path(owner_id, document_id, extension)
        if not path.is_file():
            return None
        if self.on_access is not None:
            self.on_access(owner_id, document_id, purpose)
        return path.read_bytes()

    def load_text(self, owner_id: int, document_id: str, *, purpose: str) -> str:
        text = self.load(owner_id, document_id, "txt", purpose=purpose)
        return text.decode("utf-8", errors="replace") if text else ""

    def delete(self, owner_id: int, document_id: str) -> None:
        for extension in _EXTENSIONS + ("txt",):
            self._path(owner_id, document_id, extension).unlink(missing_ok=True)
