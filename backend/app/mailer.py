import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import TypeAdapter, EmailStr


class ResetSender(Protocol):
    def send_reset(self, recipient: str, code: str) -> None: ...


class NotificationSender(Protocol):
    """Sends a plain-text notification email (CMPS271-98). Raises when the message was not accepted."""

    def send_notification(self, recipient: str, subject: str, body: str) -> None: ...


@dataclass(frozen=True)
class MailSettings:
    frontend_url: str
    host: str
    from_email: str
    port: int = 587
    security: str = "starttls"
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    timeout: int = 10
    provider: str = "smtp"
    region: str = "us-east-1"

    @classmethod
    def from_environment(cls):
        provider = os.environ.get("RESET_MAIL_PROVIDER", "smtp").lower()
        if provider not in {"smtp", "ses"}:
            raise RuntimeError("RESET_MAIL_PROVIDER must be smtp or ses.")
        if provider == "ses":
            try:
                sender = str(TypeAdapter(EmailStr).validate_python(os.environ.get("SES_FROM_EMAIL", "")))
            except ValueError:
                raise RuntimeError("Set SES_FROM_EMAIL to a verified SES sender.") from None
            region = os.environ.get("SES_REGION", "us-east-1")
            if region != "us-east-1":
                raise RuntimeError("This deployment's SES_REGION must be us-east-1.")
            return cls("", "", sender, provider="ses", region=region)
        frontend = os.environ.get("RESET_FRONTEND_URL", "")
        host = os.environ.get("SMTP_HOST", "")
        if not frontend and not host:
            return None  # Existing auth remains usable without SMTP configuration.
        parsed = urlsplit(frontend)
        if (
            not host or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(c.isspace() for c in frontend)
            or "\\" in frontend
            or (parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            ))
        ):
            raise RuntimeError("Set SMTP_HOST and a trusted RESET_FRONTEND_URL (HTTPS, or loopback HTTP; no query/fragment).")
        try:
            parsed.port  # Reject malformed ports at startup.
            sender = TypeAdapter(EmailStr).validate_python(os.environ.get("SMTP_FROM", ""))
            port = int(os.environ.get("SMTP_PORT", "587"))
            timeout = int(os.environ.get("SMTP_TIMEOUT_SECONDS", "10"))
            if not 1 <= port <= 65535 or not 1 <= timeout <= 60:
                raise ValueError
        except (ValueError, TypeError):
            raise RuntimeError("Set a valid SMTP_FROM, SMTP_PORT (1-65535), and SMTP_TIMEOUT_SECONDS (1-60).") from None
        security = os.environ.get("SMTP_SECURITY", "starttls").lower()
        username = os.environ.get("SMTP_USERNAME", "")
        password = os.environ.get("SMTP_PASSWORD", "")
        if security not in {"starttls", "ssl", "none"} or bool(username) != bool(password):
            raise RuntimeError("SMTP_SECURITY must be starttls, ssl, or none; supply both SMTP_USERNAME and SMTP_PASSWORD or neither.")
        if security == "none" and (host not in {"localhost", "127.0.0.1", "::1"} or username):
            raise RuntimeError("Unencrypted SMTP is restricted to a loopback mail catcher without credentials.")
        return cls(frontend, host, sender, port, security, username, password, timeout)


class SmtpResetSender:
    def __init__(self, settings: MailSettings):
        self.settings = settings

    def send_reset(self, recipient: str, code: str) -> None:
        self._send(recipient, "Reset your Taasheera password", otp_message(code))

    def send_notification(self, recipient: str, subject: str, body: str) -> None:
        self._send(recipient, subject, body)

    def _send(self, recipient: str, subject: str, body: str) -> None:
        settings = self.settings
        message = EmailMessage()
        message["From"] = settings.from_email
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        context = ssl.create_default_context()
        if settings.security == "ssl":
            connection = smtplib.SMTP_SSL(settings.host, settings.port, timeout=settings.timeout, context=context)
        else:
            connection = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)
        # Never enable SMTP debug output: it exposes message bodies and credentials.
        with connection as smtp:
            smtp.ehlo()
            if settings.security == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
            if settings.username:
                smtp.login(settings.username, settings.password)
            if smtp.send_message(message):
                raise smtplib.SMTPException("The email was not accepted.")


def otp_message(code: str) -> str:
    return (
        f"Your Taasheera password-reset code is: {code}\n\n"
        "Enter this code in the app. It expires in 10 minutes and can be used once.\n"
        "Never share this code. If you did not request it, ignore this email.\n"
        "Google-only accounts recover access through Google.\n"
    )


class SesResetSender:
    def __init__(self, settings: MailSettings):
        self.settings = settings

    def send_reset(self, recipient: str, code: str) -> None:
        self._send(recipient, "Your Taasheera password-reset code", otp_message(code))

    def send_notification(self, recipient: str, subject: str, body: str) -> None:
        self._send(recipient, subject, body)

    def _send(self, recipient: str, subject: str, body: str) -> None:
        import boto3
        from botocore.config import Config
        # Uses the EC2 instance role, including IMDSv2. No long-lived access keys.
        # Bounded timeouts; no automatic retries that could duplicate OTP emails.
        client = boto3.client("sesv2", region_name=self.settings.region,
                              config=Config(connect_timeout=3, read_timeout=10,
                                            retries={"total_max_attempts": 1}))
        try:
            client.send_email(
                FromEmailAddress=self.settings.from_email,
                Destination={"ToAddresses": [recipient]},
                Content={"Simple": {
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                }},
            )
        finally:
            client.close()


class LogNotificationSender:
    """Development without a mail server: write notification emails to the backend's log instead.

    Never used in production, where a missing mail configuration means no email copies at all.
    """

    def __init__(self, logger):
        self.logger = logger

    def send_notification(self, recipient: str, subject: str, body: str) -> None:
        name, _, domain = recipient.partition("@")
        self.logger.info("Notification email (not sent, no mail server) to %s***@%s: %s\n%s",
                         name[:1], domain, subject, body)
