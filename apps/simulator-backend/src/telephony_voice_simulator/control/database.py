"""Database configuration and repository construction."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


@dataclass(frozen=True)
class MySQLSettings:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"


def parse_mysql_uri(uri: str) -> MySQLSettings:
    """Parse a MySQL URI without ever rendering its credentials."""

    parsed = urlparse(uri)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ValueError("MySQL URI must start with mysql:// or mysql+pymysql://")
    database = parsed.path.lstrip("/")
    if not parsed.hostname or not parsed.username or not database:
        raise ValueError("MySQL URI must include user, host, and database name")
    query = parse_qs(parsed.query)
    return MySQLSettings(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username),
        password=unquote(parsed.password or ""),
        database=unquote(database),
        charset=query.get("charset", ["utf8mb4"])[0],
    )


def _sqlite_path_from_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "sqlite":
        raise ValueError("SQLite URI must start with sqlite://")
    if parsed.netloc not in {"", "localhost"}:
        raise ValueError("SQLite URI cannot contain a remote host")
    raw_path = unquote(parsed.path)
    if raw_path == "/:memory:":
        return Path(":memory:")
    # sqlite:///relative.db -> /relative.db from urlparse, while four slashes
    # intentionally represent an absolute filesystem path.
    if uri.startswith("sqlite:////"):
        return Path(f"/{raw_path.lstrip('/')}")
    return Path(raw_path.lstrip("/"))


def create_simulator_store(
    path: str | Path | None = None,
    *,
    uri: str | None = None,
):
    """Create the configured repository.

    Explicit arguments take precedence. `SIMULATOR_DATABASE_URI` is preferred
    over the legacy `SIMULATOR_DB_PATH` when neither argument is supplied.
    """

    from .store import SQLiteSimulatorStore

    if path is not None:
        return SQLiteSimulatorStore(path)

    selected_uri = uri or os.environ.get("SIMULATOR_DATABASE_URI")
    if selected_uri:
        if selected_uri.startswith("sqlite:"):
            return SQLiteSimulatorStore(_sqlite_path_from_uri(selected_uri))
        if selected_uri.startswith(("mysql://", "mysql+pymysql://")):
            from .mysql_store import MySQLSimulatorStore

            return MySQLSimulatorStore(selected_uri)
        raise ValueError(
            "Unsupported SIMULATOR_DATABASE_URI scheme; use sqlite://, mysql://, "
            "or mysql+pymysql://"
        )

    return SQLiteSimulatorStore(os.environ.get("SIMULATOR_DB_PATH"))
