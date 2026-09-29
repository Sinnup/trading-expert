"""Database engine, session, and base model for SQLAlchemy ORM."""

import logging
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


def get_engine(database_url: str | None = None):
    """Create SQLAlchemy engine from DATABASE_URL or default to SQLite."""
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///data/trading.db")
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, echo=False, connect_args=connect_args)


def get_session(engine=None):
    """Create a new database session."""
    if engine is None:
        engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()


def _backfill_columns(engine) -> None:
    """Add columns that exist on the models but not yet in the database.

    SQLite has no migration framework wired up here (schema is managed by
    ``create_all``), so newly-added model columns never reach a database that
    already has the table. This walks each mapped table and issues an idempotent
    ``ALTER TABLE ... ADD COLUMN`` for any missing column. Safe to run on every
    startup: existing columns are skipped.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    dialect = engine.dialect

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all already made brand-new tables in full
            db_columns = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in db_columns:
                    continue
                col_type = column.type.compile(dialect=dialect)
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}')
                )
                logger.info("Added column %s.%s (%s)", table.name, column.name, col_type)


def _import_models() -> None:
    """Import all model modules so their tables register on Base.metadata.

    ``create_all`` and the column backfill both only see tables whose model
    classes have been imported. Importing them here makes schema setup
    independent of the caller's import order.
    """
    from . import article, portfolio, signal  # noqa: F401


def init_db(database_url: str | None = None):
    """Create all tables and backfill any newly-added columns. Call on startup."""
    _import_models()
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    _backfill_columns(engine)
    return engine
