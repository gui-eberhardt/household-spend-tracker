import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import repository as repo
from app.auth import COOKIE_NAME, MAX_AGE, check_password, make_token, require_session
from app.config import get_settings
from app.db import get_conn, init_schema
from app.normalize import CATEGORIES
from app.ocr import GeminiReceiptReader, OcrError, ReceiptReader
from app.schemas import ConfirmReceipt, LoginRequest, ReceiptDraft

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ALLOWED_IMAGES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_schema()
    yield


app = FastAPI(title="Household Spend Tracker", lifespan=lifespan)

Conn = Annotated[psycopg.Connection, Depends(get_conn)]
Auth = Depends(require_session)


def get_reader() -> ReceiptReader:
    s = get_settings()
    try:
        fallbacks = [m.strip() for m in s.gemini_fallback_models.split(",") if m.strip()]
        return GeminiReceiptReader(s.gemini_api_key, s.gemini_model, fallbacks)
    except OcrError as exc:
        raise HTTPException(503, str(exc)) from exc


def this_month() -> str:
    return date.today().strftime("%Y-%m")


# ---------- auth ----------


@app.post("/api/login")
def login(body: LoginRequest, response: Response):
    if not check_password(body.password):
        raise HTTPException(401, "Wrong password")
    response.set_cookie(
        COOKIE_NAME, make_token(), max_age=MAX_AGE, httponly=True,
        samesite="lax", secure=get_settings().cookie_secure,
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/api/config", dependencies=[Auth])
def config(conn: Conn):
    return {
        "members": get_settings().members,
        "categories": CATEGORIES,
        "stores": repo.store_names(conn),
        "products": repo.list_products(conn),
    }


# ---------- receipts ----------


@app.post("/api/receipts/scan", response_model=ReceiptDraft, dependencies=[Auth])
async def scan_receipt(
    conn: Conn,
    image: UploadFile = File(...),
    reader: ReceiptReader = Depends(get_reader),
):
    if image.content_type not in ALLOWED_IMAGES:
        raise HTTPException(415, "Send a photo (JPEG, PNG, WEBP or HEIC)")
    data = await image.read()
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "Photo is too large")

    known = [p["name"] for p in repo.list_products(conn)]
    try:
        extracted = reader.read(data, image.content_type, known)
    except OcrError as exc:
        logger.warning("Receipt reading failed: %s", exc.__cause__ or exc)
        raise HTTPException(502, str(exc)) from exc
    if not extracted.items:
        raise HTTPException(422, "No items found on this photo")

    s = get_settings()
    return repo.build_draft(conn, extracted, s.alias_match_threshold, s.product_match_threshold)


@app.post("/api/receipts", status_code=201, dependencies=[Auth])
def create_receipt(body: ConfirmReceipt, conn: Conn):
    if body.paid_by not in get_settings().members:
        raise HTTPException(422, "Unknown household member")
    return {"id": repo.save_receipt(conn, body)}


@app.get("/api/receipts", dependencies=[Auth])
def get_receipts(conn: Conn, month: str = Query(default_factory=this_month, pattern=MONTH_PATTERN)):
    return repo.list_receipts(conn, month)


@app.delete("/api/receipts/{receipt_id}", dependencies=[Auth])
def remove_receipt(receipt_id: int, conn: Conn):
    if not repo.delete_receipt(conn, receipt_id):
        raise HTTPException(404, "Receipt not found")
    return {"ok": True}


@app.get("/api/summary", dependencies=[Auth])
def get_summary(conn: Conn, month: str = Query(default_factory=this_month, pattern=MONTH_PATTERN)):
    return repo.summary(conn, month)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------- front-end ----------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
