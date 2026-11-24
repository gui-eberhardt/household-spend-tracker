from collections.abc import Iterator
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().database_url, row_factory=dict_row)


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA_FILE.read_text())


def get_conn() -> Iterator[psycopg.Connection]:
    """FastAPI dependency: one connection + transaction per request."""
    with connect() as conn:
        yield conn
