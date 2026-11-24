"""Receipt reading with a vision model (Google Gemini).

``ReceiptReader`` is a small interface so the model can be swapped
(Claude, OpenAI, Tesseract...) and so tests can use a fake reader.
"""

from __future__ import annotations

import time
from typing import Protocol

from app.normalize import CATEGORIES
from app.schemas import ExtractedReceipt

PROMPT = """You are reading a photo of a supermarket receipt from Ireland (prices in EUR).

Return every purchased line, in order, as structured data:
- raw_name: the item text exactly as printed (keep abbreviations).
- quantity: number of units (or kg for weighed items); 1 if not printed.
- line_total: the final amount charged for that line. Discounts, Clubcard/
  Lidl Plus savings, multibuy offers and vouchers are separate lines with a
  NEGATIVE line_total and is_discount = true.
- suggested_name: a clean, brand-free English product name including pack
  size when known, e.g. "Semi-skimmed milk 2L", "Bananas (loose)",
  "Chicken breast fillets 500g". Expand abbreviations.
- generic_name: a short product group, e.g. "Milk", "Bananas", "Chicken breast".
- category: one of: {categories}.

Do NOT include subtotals, totals, payment, change, VAT summaries, bag
charges summary lines or loyalty point balances as items. Plastic bag
purchases ARE items (category "Household & Cleaning").

Also return store_name, purchase_date (YYYY-MM-DD) and the total paid.
Only report what is printed and visible. If the photo is cut off and the
total or the date cannot be seen, return null for them. NEVER calculate,
estimate or guess the total or the date.

When an item is the same product as one of these existing catalogue names,
use that EXACT name as suggested_name so the data stays consistent:
{known_products}
"""


class ReceiptReader(Protocol):
    def read(self, image: bytes, mime_type: str, known_products: list[str]) -> ExtractedReceipt: ...


class OcrError(RuntimeError):
    pass


RETRYABLE = {429, 500, 503, 504}  # rate limit / overloaded / timeout
SKIP_MODEL = {404}  # model retired or not available to this account


class GeminiReceiptReader:
    def __init__(
        self,
        api_key: str,
        model: str,
        fallback_models: list[str] | None = None,
        attempts_per_model: int = 2,
        backoff_seconds: float = 2.0,
        client=None,
    ):
        if not api_key and client is None:
            raise OcrError("GEMINI_API_KEY is not set")
        if client is None:
            from google import genai  # imported lazily so tests don't need it

            client = genai.Client(api_key=api_key)
        self._client = client
        self._models = list(dict.fromkeys([model, *(fallback_models or [])]))
        self._attempts = attempts_per_model
        self._backoff = backoff_seconds

    def read(self, image: bytes, mime_type: str, known_products: list[str]) -> ExtractedReceipt:
        from google.genai import types

        prompt = PROMPT.format(
            categories=", ".join(CATEGORIES),
            known_products="\n".join(f"- {p}" for p in known_products[:400]) or "(none yet)",
        )
        contents = [types.Part.from_bytes(data=image, mime_type=mime_type), prompt]
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedReceipt,
            temperature=0,
        )

        response, last_error = None, None
        for model in self._models:
            for attempt in range(self._attempts):
                try:
                    response = self._client.models.generate_content(
                        model=model, contents=contents, config=config
                    )
                    break
                except Exception as exc:  # network, quota, overload, safety...
                    last_error = exc
                    code = getattr(exc, "code", None)
                    if code in SKIP_MODEL:
                        break  # try the next model
                    if code not in RETRYABLE:
                        raise OcrError(f"Gemini request failed: {exc}") from exc
                    if attempt + 1 < self._attempts:
                        time.sleep(self._backoff * (attempt + 1))
            if response is not None:
                break

        if response is None:
            code = getattr(last_error, "code", None)
            if code in RETRYABLE:
                raise OcrError("The reading service is busy right now. Try again in a minute.") from last_error
            raise OcrError(f"Gemini request failed: {last_error}") from last_error

        if isinstance(response.parsed, ExtractedReceipt):
            return response.parsed
        try:
            return ExtractedReceipt.model_validate_json(response.text or "")
        except Exception as exc:
            raise OcrError("Could not read this receipt. Try a sharper, flatter photo.") from exc
