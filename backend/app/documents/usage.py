from sqlalchemy import func
from sqlmodel import Session, select

from ..models import SupportingDocument

FILE_KEY = func.coalesce(SupportingDocument.file_id, SupportingDocument.id)


def rows_for_file(session: Session, traveler_id: int, file_key: str) -> list[SupportingDocument]:
    return list(session.exec(select(SupportingDocument)
                             .where(SupportingDocument.traveler_id == traveler_id, FILE_KEY == file_key)
                             .order_by(SupportingDocument.uploaded_at, SupportingDocument.id)).all())


def unused_files(session: Session, file_keys: list[str]) -> list[str]:
    if not file_keys:
        return []
    used = set(session.exec(select(FILE_KEY).where(FILE_KEY.in_(file_keys))).all())
    return [key for key in dict.fromkeys(file_keys) if key not in used]
