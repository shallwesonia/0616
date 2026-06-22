from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


WORKSPACE_SCHEMAS = ("config", "runtime", "message", "audit", "export")
DEFAULT_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")


def database_url_from_env() -> str | None:
    return os.getenv("DATABASE_URL")


def workspace_id_from_env() -> UUID:
    raw_value = os.getenv("WORKSPACE_ID")
    return UUID(raw_value) if raw_value else DEFAULT_WORKSPACE_ID


def create_database_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
    if engine.dialect.name == "sqlite":
        engine = engine.execution_options(
            schema_translate_map={schema: None for schema in WORKSPACE_SCHEMAS}
        )
    return engine


class Database:
    def __init__(self, database_url: str):
        self.url = database_url
        self.engine = create_database_engine(database_url)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def health(self) -> dict:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return {
                "status": "ok",
                "backend": "postgresql" if self.engine.dialect.name == "postgresql" else self.engine.dialect.name,
            }
        except Exception as exc:
            return {
                "status": "error",
                "backend": self.engine.dialect.name,
                "error": str(exc),
            }
