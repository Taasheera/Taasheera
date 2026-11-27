"""Support requests (CMPS271-92): a traveler asks a person for help.

`create_support_request` is the one way to open a request: the website's "Talk to a person" button
and the Telegram `/help` command (CMPS271-103) both call it. It keeps only the message the traveler
typed, never the assistant conversation. Like `notifications.notify`, it adds the row to the
caller's session and the caller commits.
"""

from sqlmodel import Session

from .models import SUPPORT_REQUEST_SOURCES, Application, SupportRequest

MESSAGE_LIMIT = 1000
REPLY_LIMIT = 2000


class SupportRequestError(ValueError):
    """A request that can't be opened. The message is written for the traveler."""


def plain_text(value: str, limit: int) -> str:
    """The text with Windows line endings made plain and the ends trimmed; refuses control characters."""
    text = value.replace("\r\n", "\n").strip()
    if len(text) > limit or any(ord(character) < 32 and character not in "\n\t" for character in text):
        raise ValueError(f"Use plain text of at most {limit} characters.")
    return text


def create_support_request(session: Session, traveler_id: int, *, source: str, message: str,
                           application_id: str | None = None) -> SupportRequest:
    """Open a request for this traveler, about one of their own applications if named. The caller commits.

    `source` is "website" or "telegram". Raises SupportRequestError for an empty or too long message,
    an unknown source, or an application that isn't this traveler's.
    """
    if source not in SUPPORT_REQUEST_SOURCES:
        raise SupportRequestError("Unknown support request source.")
    try:
        text = plain_text(message, MESSAGE_LIMIT) if isinstance(message, str) else ""
    except ValueError:
        text = ""
    if not text:
        raise SupportRequestError(f"Write your message in plain text, up to {MESSAGE_LIMIT} characters.")
    if application_id is not None:
        application = session.get(Application, application_id)
        if application is None or application.traveler_id != traveler_id:
            raise SupportRequestError("Application not found.")
    support_request = SupportRequest(traveler_id=traveler_id, application_id=application_id, source=source,
                                     message=text)
    session.add(support_request)
    session.flush()
    return support_request
