"""GeminiReceiptReader retry/fallback logic, with a fake Gemini client."""

import pytest

from app.ocr import GeminiReceiptReader, OcrError
from app.schemas import ExtractedReceipt

RECEIPT = ExtractedReceipt(store_name="Lidl", total=1.0, items=[])


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"{code} error")
        self.code = code


class Response:
    parsed = RECEIPT
    text = ""


class FakeClient:
    def __init__(self, script):
        self.script = script  # {model: [error code or "ok", ...]}
        self.calls = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        outcome = self.script[model].pop(0)
        if outcome == "ok":
            return Response()
        raise ApiError(outcome)


def reader(script, fallbacks=("backup",)):
    client = FakeClient(script)
    return GeminiReceiptReader("", "main", list(fallbacks), backoff_seconds=0, client=client), client


def test_retries_when_busy():
    r, client = reader({"main": [503, "ok"]})
    assert r.read(b"img", "image/jpeg", []) == RECEIPT
    assert client.calls == ["main", "main"]


def test_falls_back_when_main_model_stays_busy():
    r, client = reader({"main": [503, 503], "backup": ["ok"]})
    assert r.read(b"img", "image/jpeg", []) == RECEIPT
    assert client.calls == ["main", "main", "backup"]


def test_skips_retired_model():
    r, client = reader({"main": [404], "backup": ["ok"]})
    r.read(b"img", "image/jpeg", [])
    assert client.calls == ["main", "backup"]


def test_friendly_message_when_everything_is_busy():
    r, _ = reader({"main": [503, 503], "backup": [429, 429]})
    with pytest.raises(OcrError, match="busy"):
        r.read(b"img", "image/jpeg", [])


def test_bad_request_is_not_retried():
    r, client = reader({"main": [400]})
    with pytest.raises(OcrError):
        r.read(b"img", "image/jpeg", [])
    assert client.calls == ["main"]
