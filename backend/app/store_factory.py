from __future__ import annotations

import os

from .database import database_url_from_env, workspace_id_from_env
from .database_store import DatabaseStore
from .runtime_cache import runtime_cache_from_env
from .store import JsonStore


def create_store() -> JsonStore:
    backend = os.getenv("STORE_BACKEND", "").strip().lower()
    database_url = database_url_from_env()

    if backend in {"postgres", "postgresql", "database"}:
        if not database_url:
            raise RuntimeError("DATABASE_URL is required when STORE_BACKEND=postgresql")
        workspace_id = workspace_id_from_env()
        return DatabaseStore(database_url, workspace_id=workspace_id, runtime_cache=runtime_cache_from_env(workspace_id))

    if backend == "json":
        return JsonStore()

    if database_url:
        workspace_id = workspace_id_from_env()
        return DatabaseStore(database_url, workspace_id=workspace_id, runtime_cache=runtime_cache_from_env(workspace_id))
    return JsonStore()
