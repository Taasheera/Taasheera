"""Private storage for the agent's page screenshots, in the traveler's own folder (CMPS271-70).

`<root>/<traveler_id>/agent/<application_id>/<screenshot_id>.png`. Screenshots show passport
details, so they live with the passport photos: account erasure removes the owner folder, and
deleting an application removes its own folder. Ids are our random hex, never client input.
"""

import os
import re
import shutil
from pathlib import Path

_ID = re.compile(r"[0-9a-f]{32}")


class AgentFiles:
    def __init__(self, root: Path | str, *, on_access=None):
        self.root = Path(root)
        self.on_access = on_access

    def _folder(self, owner_id: int, application_id: str) -> Path:
        if not _ID.fullmatch(application_id):
            raise ValueError("Invalid application id")
        return self.root / str(int(owner_id)) / "agent" / application_id

    def _path(self, owner_id: int, application_id: str, screenshot_id: str) -> Path:
        if not _ID.fullmatch(screenshot_id):
            raise ValueError("Invalid screenshot id")
        return self._folder(owner_id, application_id) / f"{screenshot_id}.png"

    def save(self, owner_id: int, application_id: str, screenshot_id: str, png: bytes) -> None:
        folder = self._folder(owner_id, application_id)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self._path(owner_id, application_id, screenshot_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(png)

    def load(self, owner_id: int, application_id: str, screenshot_id: str) -> bytes | None:
        path = self._path(owner_id, application_id, screenshot_id)
        if not path.is_file():
            return None
        if self.on_access is not None:
            self.on_access(owner_id, screenshot_id, "agent_screenshot")
        return path.read_bytes()

    def remove_application(self, owner_id: int, application_id: str) -> None:
        folder = self._folder(owner_id, application_id)
        if folder.is_symlink():
            folder.unlink()
        elif folder.exists():
            shutil.rmtree(folder)
