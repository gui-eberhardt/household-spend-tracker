"""Test setup. Needs a throwaway PostgreSQL database in TEST_DATABASE_URL,
e.g. postgresql://postgres@localhost/receipts_test (it is wiped on each test)."""

import os

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
if not TEST_DB:
    pytest.skip("TEST_DATABASE_URL not set", allow_module_level=True)

os.environ.update(
    DATABASE_URL=TEST_DB,
    HOUSEHOLD_PASSWORD="test-pass",
    SECRET_KEY="test-secret",
    HOUSEHOLD_MEMBERS="Guilherme,Lazia",
    COOKIE_SECURE="false",
    GEMINI_API_KEY="",
)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect  # noqa: E402
from app.main import app, get_reader  # noqa: E402
from app.schemas import ExtractedReceipt  # noqa: E402


class FakeReader:
    """Returns a queued ExtractedReceipt instead of calling Gemini."""

    def __init__(self):
        self.queue: list[ExtractedReceipt] = []
        self.last_known: list[str] = []

    def read(self, image, mime_type, known_products):
        self.last_known = known_products
        return self.queue.pop(0)


@pytest.fixture
def reader():
    return FakeReader()


@pytest.fixture
def client(reader):
    with connect() as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    app.dependency_overrides[get_reader] = lambda: reader
    with TestClient(app) as c:
        c.post("/api/login", json={"password": "test-pass"})
        yield c
    app.dependency_overrides.clear()
