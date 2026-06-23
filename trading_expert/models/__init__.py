"""Database engine, session, and base model for SQLAlchemy ORM."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
import os


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


def init_db(database_url: str | None = None):
    """Create all tables. Call on startup."""
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    return engine
