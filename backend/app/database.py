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


DEFAULT_API_THREADS = 40  # AnyIO's default thread pool for synchronous endpoints


def api_threads() -> int:
    """API_THREADS sizes the thread pool that runs the (synchronous) endpoints, and the SQLite pool."""
    raw = os.environ.get("API_THREADS", "").strip()
    if not raw:
        return DEFAULT_API_THREADS
    if not raw.isascii() or not raw.isdigit() or not 10 <= int(raw) <= 200:
        raise RuntimeError("API_THREADS must be a whole number between 10 and 200.")
    return int(raw)


def build_engine(database_url: str | None = None):
    url = normalize_database_url(database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    parsed = make_url(url)
    sqlite = parsed.get_backend_name() == "sqlite"
    # FastAPI may use different threads during a request. Sessions stay per-request.
    connect_args = {"check_same_thread": False} if sqlite else {}
    pool = {}
    if sqlite and parsed.database not in (None, "", ":memory:"):
        # A request keeps its connection until it ends, including slow work such as password hashing.
        # SQLAlchemy's default pool (5 + 10) is smaller than the endpoint threads, so during a burst of
        # sign-ins other requests waited 30 s for a connection and failed (docs/CAPACITY_REPORT.md).
        # SQLite connections are cheap and SQLite orders writes itself: give every thread one.
        pool = {"pool_size": api_threads(), "max_overflow": 10, "pool_use_lifo": True}
    engine = create_engine(url, connect_args=connect_args, **pool)
    if sqlite and sqlite_journal_mode() == "wal":
        @event.listens_for(engine, "connect")
        def write_ahead_log(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            # Durable at each checkpoint rather than each commit: the WAL-mode default trade-off.
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
    return engine
