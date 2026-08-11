from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str | None = None) -> Engine:
    url = database_url or f"sqlite:///{settings.database_path.as_posix()}"
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        pool_pre_ping=True,
    )

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_database(target_engine: Engine | None = None) -> None:
    from . import models  # noqa: F401

    selected = target_engine or engine
    Base.metadata.create_all(selected)
    with selected.begin() as connection:
        if selected.dialect.name == "sqlite":
            event_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(recognition_events)"
                ).fetchall()
            }
            if "identity_category_snapshot" not in event_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE recognition_events "
                    "ADD COLUMN identity_category_snapshot VARCHAR(20)"
                )
            connection.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS audit_log_no_update "
                "BEFORE UPDATE ON audit_log BEGIN "
                "SELECT RAISE(ABORT, 'audit log is append-only'); END"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS audit_log_no_delete "
                "BEFORE DELETE ON audit_log BEGIN "
                "SELECT RAISE(ABORT, 'audit log is append-only'); END"
            )
        connection.exec_driver_sql("PRAGMA optimize")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
