"""Check an uploaded supporting document and read its text.

Like passport photos, the type comes from the file's content, never its name or the
Content-Type header. Images are re-encoded as clean JPEGs (rotation applied, metadata such
as GPS location stripped). PDFs can't be re-encoded safely, so they are kept as uploaded,
after checking they open, aren't encrypted and aren't huge; they are only ever served as
downloads, never rendered on our origin.
"""

import logging
from dataclasses import dataclass
from io import BytesIO

import pytesseract
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..passport.images import MAX_UPLOAD_BYTES, InvalidImageError, normalize_image

logger = logging.getLogger(__name__)

MAX_PDF_PAGES = 30
# Enough text to find a name, a date of birth and a passport number; bank statements run long.
MAX_TEXT_CHARS = 20_000
OCR_TIMEOUT_SECONDS = 8


class InvalidDocumentError(ValueError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class CheckedDocument:
    content: bytes
    content_type: str  # "application/pdf" or "image/jpeg"
    extension: str  # "pdf" or "jpg"


def check_document(data: bytes) -> CheckedDocument:
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidDocumentError("Files must be 10 MB or smaller.", 413)
    if data.startswith(b"%PDF-"):
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise InvalidDocumentError("This PDF is password-protected. Upload a copy without a password.", 422)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise InvalidDocumentError(f"PDFs can have at most {MAX_PDF_PAGES} pages.", 413)
        except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
            if isinstance(exc, InvalidDocumentError):
                raise
            raise InvalidDocumentError("The file isn't a readable PDF.", 422) from None
        return CheckedDocument(data, "application/pdf", "pdf")
    try:
        return CheckedDocument(normalize_image(data), "image/jpeg", "jpg")
    except InvalidImageError as exc:
        message = "Upload a PDF, JPEG, PNG or WebP file." if exc.status_code in (415, 422) else str(exc)
        raise InvalidDocumentError(message, exc.status_code) from None


def read_text(document: CheckedDocument) -> str:
    """The document's text, for the cross-check. Empty when nothing could be read; never raises."""
    try:
        if document.content_type == "application/pdf":
            reader = PdfReader(BytesIO(document.content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if text.strip():
                return text[:MAX_TEXT_CHARS]
            # A scanned PDF has no text layer; reading its images is a later improvement.
            return ""
        with Image.open(BytesIO(document.content)) as image:
            return pytesseract.image_to_string(image, timeout=OCR_TIMEOUT_SECONDS)[:MAX_TEXT_CHARS]
    except Exception:  # noqa: BLE001 - a document we can't read is still a valid upload
        # The type only: the content and the error text could hold the traveler's details.
        logger.warning("Supporting document text unreadable (%s)", document.content_type)
        return ""
