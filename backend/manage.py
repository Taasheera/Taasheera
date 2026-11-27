"""Operator commands that need database or storage access, never an HTTP endpoint.

Usage: python manage.py set-role --email someone@example.com --role admin
       python manage.py seed-load-test --users 300   (staging only; see loadtest/README.md)
       python manage.py encrypt-documents
"""

import argparse
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import bcrypt
from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.account_lock import account_lock
from app.activity import activity_directory_from_environment, record_activity
from app.config import deploy_environment
from app.consent import PRIVACY_POLICY_VERSION
from app.database import build_engine
from app.file_encryption import FileCipher, encrypt_existing_files
from app.models import ROLES, AccountErasure, PrivacyConsent, Traveler
from app.schema import require_current_schema

LOAD_TEST_DOMAIN = "example.com"  # Reserved for documentation: never a real person's address.


def seed_load_test(users: int, prefix: str) -> str:
    """Synthetic accounts for the capacity tests: `<prefix>-0001@example.com`..., each with the current
    privacy policy accepted, plus `<prefix>-admin@example.com` (admin) for health sampling.

    Staging only. Passwords come from LOADTEST_PASSWORD / LOADTEST_ADMIN_PASSWORD, never arguments,
    so they stay out of shell history and logs. Existing accounts are left as they are.
    """
    if deploy_environment() != "staging":
        raise SystemExit("seed-load-test runs only on staging (DEPLOY_ENVIRONMENT=staging).")
    password = os.environ.get("LOADTEST_PASSWORD", "")
    admin_password = os.environ.get("LOADTEST_ADMIN_PASSWORD", "")
    if len(password) < 12 or len(admin_password) < 12:
        raise SystemExit("Set LOADTEST_PASSWORD and LOADTEST_ADMIN_PASSWORD (12 characters or more).")
    if not 1 <= users <= 5000:
        raise SystemExit("Choose between 1 and 5000 users.")
    # One hash for every traveler (same cost as sign-up, so signing in costs what it does for real).
    traveler_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode("ascii")
    admin_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt(rounds=12)).decode("ascii")
    engine = build_engine()
    created = 0
    try:
        require_current_schema(engine)
        with Session(engine) as session:
            existing = set(session.exec(select(Traveler.email).where(Traveler.email.startswith(f"{prefix}-"))).all())
            accounts = [(f"{prefix}-{index:04d}@{LOAD_TEST_DOMAIN}", f"Load Test {index:04d}", "traveler", traveler_hash)
                        for index in range(1, users + 1)]
            accounts.append((f"{prefix}-admin@{LOAD_TEST_DOMAIN}", "Load Test Admin", "admin", admin_hash))
            for email, name, role, password_hash in accounts:
                if email in existing:
                    continue
                traveler = Traveler(name=name, email=email, password_hash=password_hash, role=role)
                session.add(traveler)
                session.flush()
                session.add(PrivacyConsent(traveler_id=traveler.id, policy_version=PRIVACY_POLICY_VERSION))
                created += 1
            session.commit()
    finally:
        engine.dispose()
    return f"{created} synthetic accounts created; {users + 1 - created} already existed."


def set_role(email: str, role: str) -> str:
    try:
        directory = activity_directory_from_environment()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    try:
        # Normalized the same way as sign-up and Google sign-in.
        email = str(TypeAdapter(EmailStr).validate_python(email)).lower()
    except ValidationError:
        raise SystemExit("Enter a valid email address.") from None
    engine = build_engine()
    try:
        url = engine.url
        if url.get_backend_name() == "sqlite" and url.database and not Path(url.database).is_file():
            # Connecting would quietly create an empty database at a mistyped path.
            raise SystemExit("No database file at that path. Check DATABASE_URL, or run "
                             "'python dev.py run' once to create the local database.")
        try:
            require_current_schema(engine)
        except OperationalError:
            raise SystemExit("Could not connect to the database. Check DATABASE_URL.") from None
        except RuntimeError:
            raise SystemExit("Database schema is not current. Run 'python migrate.py upgrade' first.") from None
        with Session(engine) as session:
            # Lock the account first, like login and password reset: a no-op UPDATE takes SQLite's
            # write lock, and FOR UPDATE the PostgreSQL row lock. Concurrent runs then record each
            # change from the right previous role.
            session.execute(update(Traveler).where(Traveler.email == email).values(role=Traveler.role))
            traveler = session.exec(select(Traveler).where(Traveler.email == email).with_for_update()).first()
            if traveler is None:
                raise SystemExit("No account uses that email. Sign up first, then run this command again.")
            if session.get(AccountErasure, traveler.id) is not None:
                raise SystemExit("Account erasure is pending; this account cannot change roles.")
            # Never print the email: terminal output often ends up in shared logs.
            account = f"Account {traveler.id} ({'password' if traveler.password_hash else 'Google'} sign-in)"
            previous, suspended = traveler.role, traveler.status == "suspended"
            if previous != role:
                traveler.role = role
                session.add(traveler)
                # Commits the role change and its audit event together.
                record_activity(session, directory, traveler.id, "role_change", f"cli:{previous}->{role}", "success")
    finally:
        engine.dispose()
    if previous == role:
        message = f"{account} already has the {role} role; nothing changed."
    else:
        message = f"{account} role changed from {previous} to {role}."
    if suspended and role != "traveler":
        message += " It is suspended, so it cannot use admin tools until it is reactivated."
    return message


def encrypt_documents() -> str:
    """Encrypt the traveler files written before DOCUMENT_ENCRYPTION_KEY was set (CMPS271-114)."""
    try:
        cipher = FileCipher.from_environment()
        lock_directory = activity_directory_from_environment().parent / ".account-locks"
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    if cipher is None:
        raise SystemExit("Set DOCUMENT_ENCRYPTION_KEY first, to the same key the API and agent worker use.")
    root = Path(os.environ.get("PASSPORT_STORAGE_DIR", str(Path(__file__).resolve().parent / "storage" / "passports")))
    if not root.is_dir():
        raise SystemExit("No storage folder at that path. Check PASSPORT_STORAGE_DIR.")

    @contextmanager
    def owner_lock(owner_id: int):
        try:
            with account_lock(lock_directory, owner_id):
                yield
        except HTTPException:
            raise TimeoutError from None

    counts = encrypt_existing_files(root, cipher, owner_lock)
    message = f"Encrypted {counts['encrypted']} files; {counts['already_encrypted']} were already encrypted."
    if counts["unreadable"]:
        message += f" {counts['unreadable']} encrypted files do not open with this key."
    if counts["busy_owners"]:
        message += f" {counts['busy_owners']} accounts were busy; run the command again."
    if counts["unreadable"] or counts["busy_owners"]:
        raise SystemExit(message)
    return message


def main(argv: list[str] | None = None) -> None:
    # No abbreviations: '--em' or '--ro' must not silently mean something.
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    set_role_parser = commands.add_parser(
        "set-role", help="Give an existing account the traveler, support or admin role.", allow_abbrev=False)
    set_role_parser.add_argument("--email", required=True)
    set_role_parser.add_argument("--role", required=True, choices=ROLES)
    seed_parser = commands.add_parser(
        "seed-load-test", help="Create synthetic load-test accounts on staging.", allow_abbrev=False)
    seed_parser.add_argument("--users", type=int, default=300)
    seed_parser.add_argument("--prefix", default="loadtest", choices=["loadtest"])
    commands.add_parser("encrypt-documents", allow_abbrev=False,
                        help="Encrypt stored traveler files written before DOCUMENT_ENCRYPTION_KEY was set.")
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] not in commands.choices and argv[0] not in ("-h", "--help"):
        # Anything before the command would be echoed as an invalid choice.
        parser.error(f"start with a command: {', '.join(commands.choices)}")
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        # argparse would echo them, and a mistaken '--password ...' must not reach shared logs.
        parser.error("unrecognized arguments (not shown)")
    if args.command == "seed-load-test":
        print(seed_load_test(args.users, args.prefix))
    elif args.command == "encrypt-documents":
        print(encrypt_documents())
    else:
        print(set_role(args.email, args.role))


if __name__ == "__main__":
    main()
