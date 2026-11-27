import os
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlmodel import create_engine


DEFAULT_DATABASE_URL = f"sqlite:///{(Path(__file__).resolve().parents[1] / 'taasheera.db').as_posix()}"


def normalize_database_url(url: str) -> str:
    # Render and other managed Postgres hosts provide a generic SQLAlchemy URL.
    # Our pinned PostgreSQL driver is psycopg 3, not SQLAlchemy's psycopg2 default.
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def sqlite_journal_mode() -> str | None:
    """SQLITE_JOURNAL_MODE=wal lets readers continue while one request writes (Sprint 5 capacity
    tests). Unset keeps SQLite's default rollback journal. See docs/CAPACITY_REPORT.md before
    turning it on in production: backups must copy the -wal file too (the online backup API does)."""
    mode = os.environ.get("SQLITE_JOURNAL_MODE", "").strip().lower()
    if mode not in {"", "wal"}:
        raise RuntimeError("SQLITE_JOURNAL_MODE must be empty or wal.")
    return mode or None


def build_engine(database_url: str | None = None):
    url = normalize_database_url(database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    sqlite = make_url(url).get_backend_name() == "sqlite"
    # FastAPI may use different threads during a request. Sessions stay per-request.
    connect_args = {"check_same_thread": False} if sqlite else {}
    engine = create_engine(url, connect_args=connect_args)
    if sqlite and sqlite_journal_mode() == "wal":
        @event.listens_for(engine, "connect")
        def write_ahead_log(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            # Durable at each checkpoint rather than each commit: the WAL-mode default trade-off.
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
    return engine
