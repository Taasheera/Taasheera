"""Optional authenticator codes. Enrollment is flagged; enrolled accounts always enforce MFA."""
import base64
import hashlib
import hmac
import io
import re
import secrets
import time
from typing import Annotated

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
import pyotp
import qrcode
from sqlalchemy import delete, update
from sqlmodel import Session, func, select

from .activity import record_activity
from .features import require_feature
from .models import GoogleIdentity, MfaBackupCode, MfaChallenge, MfaSettings, Traveler
from .security import audit, throttle

CHALLENGE_SECONDS = 300


def now():
    return int(time.time())


class Reauthenticate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_traveler_id: int = Field(gt=0)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=72)
    google_id_token: SecretStr | None = Field(default=None, min_length=1, max_length=8192)


class MfaProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_traveler_id: int = Field(gt=0)
    code: SecretStr = Field(min_length=6, max_length=32)


class ChallengeToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_token: SecretStr = Field(min_length=43, max_length=43)


class ChallengeProof(ChallengeToken):
    code: SecretStr = Field(min_length=6, max_length=32)


def cipher(request):
    key = hmac.new(request.app.state.auth_settings.jwt_secret.encode(), b"taasheera-totp-encryption-v1", hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def decrypt(request, encrypted):
    try:
        return cipher(request).decrypt(encrypted.encode()).decode()
    except (InvalidToken, AttributeError, UnicodeError):
        raise HTTPException(503, "Security settings are unavailable. Contact support.") from None


def fingerprint(traveler):
    return hashlib.sha256((traveler.auth_type + ":" + traveler.password_hash).encode()).hexdigest()


def lock_owner(session, traveler_id):
    session.execute(update(Traveler).where(Traveler.id == traveler_id).values(status=Traveler.status))
    traveler = session.exec(select(Traveler).where(Traveler.id == traveler_id)
                            .execution_options(populate_existing=True)).first()
    if traveler is None or traveler.status != "active":
        raise HTTPException(401, "Please sign in again.")
    return traveler


def need_owner(expected, traveler):
    if expected != traveler.id:
        raise HTTPException(409, "Your signed-in account changed. Reload before changing security settings.")


def confirm_primary(data, request, session, traveler):
    from .google_auth import verify_google_token
    need_owner(data.expected_traveler_id, traveler)
    claims = None
    if not traveler.password_enabled:
        client = request.app.state.auth_settings.google_client_id
        if not client or data.google_id_token is None:
            raise HTTPException(403, "Confirm your Google identity to change security settings.")
        claims = verify_google_token(data.google_id_token.get_secret_value(), client)
    traveler = lock_owner(session, traveler.id)
    if traveler.password_enabled:
        try:
            valid = data.password is not None and bcrypt.checkpw(data.password.get_secret_value().encode(),
                                                                  traveler.password_hash.encode())
        except (ValueError, UnicodeError): valid = False
    else:
        identity = session.get(GoogleIdentity, claims["sub"]) if claims else None
        valid = identity is not None and identity.traveler_id == traveler.id
    if not valid:
        raise HTTPException(403, "Confirm your existing sign-in method to change security settings.")
    return traveler


def totp_counter(secret, code, last_counter):
    if not re.fullmatch(r"[0-9]{6}", code): return None
    current = now() // 30
    generator = pyotp.TOTP(secret)
    for counter in (current, current - 1, current + 1):
        if counter > last_counter and hmac.compare_digest(generator.at(counter * 30), code):
            return counter
    return None


def backup_hash(request, owner, code):
    return hmac.new(request.app.state.auth_settings.jwt_secret.encode(),
                    f"mfa-backup:{owner}:{code}".encode(), hashlib.sha256).hexdigest()


def prove_second_factor(request, session, settings, raw, *, backup=True):
    code = raw.strip()
    counter = totp_counter(decrypt(request, settings.secret_ciphertext), code, settings.last_counter)
    if counter is not None:
        settings.last_counter = counter
        session.add(settings)
        return True
    normalized = re.sub(r"[-\s]", "", code).upper()
    if backup and re.fullmatch(r"[0-9A-F]{24}", normalized):
        row = session.get(MfaBackupCode, backup_hash(request, settings.traveler_id, normalized))
        if row is not None and row.traveler_id == settings.traveler_id and not row.used:
            row.used = True
            session.add(row)
            return True
    return False


def new_backups(request, session, owner):
    session.execute(delete(MfaBackupCode).where(MfaBackupCode.traveler_id == owner))
    codes = []
    for _ in range(10):
        raw = secrets.token_hex(12).upper()
        codes.append("-".join(raw[index:index + 6] for index in range(0, 24, 6)))
        session.add(MfaBackupCode(code_hash=backup_hash(request, owner, raw), traveler_id=owner))
    return codes


def begin_challenge(traveler, request, response, session, method):
    from .auth import token_hash
    settings = session.get(MfaSettings, traveler.id)
    if settings is None or not settings.enabled:
        return None
    timestamp = now()
    session.execute(delete(MfaChallenge).where(MfaChallenge.expires_at <= timestamp))
    # Bound the number of pending challenges per account; a new primary sign-in
    # supersedes the old challenge rather than creating an unbounded set of guesses.
    session.execute(delete(MfaChallenge).where(MfaChallenge.traveler_id == traveler.id))
    raw = secrets.token_urlsafe(32)
    session.add(MfaChallenge(token_hash=token_hash(raw), traveler_id=traveler.id,
        expires_at=timestamp + CHALLENGE_SECONDS, method=method, credential_fingerprint=fingerprint(traveler),
        generation=settings.generation))
    record_activity(session, request.app.state.activity_directory, traveler.id, "sign_in_step", method,
                    "mfa_required", resource_type="security")
    return JSONResponse(status_code=202, headers={"Cache-Control": "no-store"}, content={
        "mfa_required": True, "challenge_token": raw, "expires_in": CHALLENGE_SECONDS,
        "methods": ["totp", "backup_code"]})


def create_mfa_router(get_session, current_traveler):
    from .auth import check_origin, revoke_other_sessions, start_session, token_hash
    router = APIRouter(prefix="/auth/mfa", tags=["Two-factor sign-in"], dependencies=[Depends(check_origin)])
    SessionDep = Annotated[Session, Depends(get_session)]
    OwnerDep = Annotated[Traveler, Depends(current_traveler)]

    @router.get("")
    def status(session: SessionDep, traveler: OwnerDep):
        settings = session.get(MfaSettings, traveler.id)
        remaining = session.exec(select(func.count()).select_from(MfaBackupCode).where(
            MfaBackupCode.traveler_id == traveler.id, MfaBackupCode.used.is_(False))).one()
        return {"enabled": bool(settings and settings.enabled), "backup_codes_remaining": remaining}

    @router.post("/setup", dependencies=[Depends(require_feature("two_factor_signin"))])
    def setup(data: Reauthenticate, request: Request, session: SessionDep, traveler: OwnerDep):
        throttle(request, session, "mfa-setup", account=str(traveler.id), account_limit=5)
        traveler = confirm_primary(data, request, session, traveler)
        settings = session.get(MfaSettings, traveler.id) or MfaSettings(traveler_id=traveler.id)
        if settings.enabled: raise HTTPException(409, "Two-factor sign-in is already enabled.")
        secret = pyotp.random_base32()
        settings.pending_ciphertext = cipher(request).encrypt(secret.encode()).decode()
        settings.pending_expires_at = now() + 600
        session.add(settings); session.commit()
        uri = pyotp.TOTP(secret).provisioning_uri(name=traveler.email, issuer_name="Taasheera")
        output = io.BytesIO(); qrcode.make(uri).save(output, format="PNG")
        return {"secret": secret, "provisioning_uri": uri,
                "qr_data_url": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode(),
                "expires_in": 600}

    @router.post("/enable", dependencies=[Depends(require_feature("two_factor_signin"))])
    def enable(data: MfaProof, request: Request, session: SessionDep, traveler: OwnerDep):
        throttle(request, session, "mfa-enable", account=str(traveler.id), account_limit=5)
        need_owner(data.expected_traveler_id, traveler); lock_owner(session, traveler.id)
        settings = session.get(MfaSettings, traveler.id)
        if settings is None or settings.enabled or not settings.pending_expires_at or settings.pending_expires_at <= now():
            raise HTTPException(409, "Authenticator setup expired or changed. Start setup again.")
        counter = totp_counter(decrypt(request, settings.pending_ciphertext), data.code.get_secret_value(), -1)
        if counter is None: raise HTTPException(403, "Enter a current code from your authenticator app.")
        settings.secret_ciphertext = settings.pending_ciphertext
        settings.pending_ciphertext = None; settings.pending_expires_at = None
        settings.enabled = True; settings.last_counter = counter; settings.generation += 1
        session.add(settings)
        codes = new_backups(request, session, traveler.id)
        revoke_other_sessions(session, traveler.id, request.state.auth_session_id)
        session.execute(delete(MfaChallenge).where(MfaChallenge.traveler_id == traveler.id))
        record_activity(session, request.app.state.activity_directory, traveler.id,
                        "security_change", "mfa_enable", "success", resource_type="security")
        return {"enabled": True, "backup_codes": codes}

    @router.post("/disable")
    def disable(data: MfaProof, request: Request, session: SessionDep, traveler: OwnerDep):
        throttle(request, session, "mfa-disable", account=str(traveler.id), account_limit=5)
        need_owner(data.expected_traveler_id, traveler); lock_owner(session, traveler.id)
        settings = session.get(MfaSettings, traveler.id)
        if settings is None or not settings.enabled: raise HTTPException(409, "Two-factor sign-in is not enabled.")
        if not prove_second_factor(request, session, settings, data.code.get_secret_value()):
            raise HTTPException(403, "Enter a new authenticator code or an unused backup code.")
        settings.enabled = False; settings.secret_ciphertext = None; settings.generation += 1
        settings.pending_ciphertext = None; settings.pending_expires_at = None
        session.add(settings)
        session.execute(delete(MfaBackupCode).where(MfaBackupCode.traveler_id == traveler.id))
        session.execute(delete(MfaChallenge).where(MfaChallenge.traveler_id == traveler.id))
        revoke_other_sessions(session, traveler.id, request.state.auth_session_id)
        record_activity(session, request.app.state.activity_directory, traveler.id,
                        "security_change", "mfa_disable", "success", resource_type="security")
        return {"enabled": False}

    @router.post("/backup-codes")
    def regenerate(data: MfaProof, request: Request, session: SessionDep, traveler: OwnerDep):
        throttle(request, session, "mfa-backups", account=str(traveler.id), account_limit=5)
        need_owner(data.expected_traveler_id, traveler); lock_owner(session, traveler.id)
        settings = session.get(MfaSettings, traveler.id)
        if settings is None or not settings.enabled: raise HTTPException(409, "Two-factor sign-in is not enabled.")
        if not prove_second_factor(request, session, settings, data.code.get_secret_value()):
            raise HTTPException(403, "Enter a new authenticator code or an unused backup code.")
        settings.generation += 1; session.add(settings)
        codes = new_backups(request, session, traveler.id)
        session.execute(delete(MfaChallenge).where(MfaChallenge.traveler_id == traveler.id))
        record_activity(session, request.app.state.activity_directory, traveler.id,
                        "security_change", "mfa_backup_codes", "success", resource_type="security")
        return {"backup_codes": codes}

    @router.post("/verify")
    def verify(data: ChallengeProof, request: Request, response: Response, session: SessionDep):
        throttle(request, session, "mfa-verify", ip_limit=30)
        key = token_hash(data.challenge_token.get_secret_value())
        challenge = session.get(MfaChallenge, key)
        if challenge is None: raise HTTPException(401, "Invalid or expired sign-in code.")
        owner_id = challenge.traveler_id
        throttle(request, session, "mfa-verify", account=str(owner_id), account_limit=10)
        traveler = lock_owner(session, owner_id)
        challenge = session.exec(select(MfaChallenge).where(MfaChallenge.token_hash == key)
                                  .execution_options(populate_existing=True)).first()
        if challenge is None: raise HTTPException(401, "Invalid or expired sign-in code.")
        settings = session.get(MfaSettings, traveler.id)
        if (challenge.used or challenge.expires_at <= now() or challenge.attempts >= 5
                or settings is None or not settings.enabled or settings.generation != challenge.generation
                or not hmac.compare_digest(challenge.credential_fingerprint, fingerprint(traveler))):
            raise HTTPException(401, "Invalid or expired sign-in code.")
        challenge.attempts += 1
        if not prove_second_factor(request, session, settings, data.code.get_secret_value()):
            if challenge.attempts >= 5: challenge.used = True
            session.add(challenge); session.commit()
            audit(request, "mfa-verify", "rejected", traveler.id)
            raise HTTPException(401, "Invalid or expired sign-in code. Use a new code or an unused backup code.")
        challenge.used = True
        session.add(challenge); session.flush()
        method = challenge.method
        result = start_session(traveler, request, response, session, method=method, mfa_verified=True)
        record_activity(session, request.app.state.activity_directory, traveler.id,
                        "sign_in", method, "success", resource_type="security")
        audit(request, "mfa-verify", "success", traveler.id)
        return result

    @router.post("/cancel", status_code=204, response_class=Response)
    def cancel(data: ChallengeToken, session: SessionDep):
        session.execute(update(MfaChallenge).where(MfaChallenge.token_hash == token_hash(data.challenge_token.get_secret_value()))
                        .values(used=True))
        session.commit()
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    return router
