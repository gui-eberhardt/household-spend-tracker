from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# ---------- What the OCR model returns ----------


class ExtractedItem(BaseModel):
    raw_name: str = Field(description="Item text exactly as printed on the receipt")
    quantity: float = Field(default=1, description="Units or kg; 1 if not shown")
    line_total: float = Field(description="Final amount for the line in EUR; negative for discounts")
    is_discount: bool = Field(default=False, description="True for discount/offer/voucher lines")
    suggested_name: str = Field(
        description="Clean English product name with brand-free description and pack size, e.g. 'Semi-skimmed milk 2L'"
    )
    generic_name: str = Field(description="Short product group, e.g. 'Milk', 'Bananas', 'Chicken breast'")
    category: str = Field(description="One of the allowed categories")


class ExtractedReceipt(BaseModel):
    store_name: str
    purchase_date: str | None = Field(default=None, description="YYYY-MM-DD if printed")
    total: float | None = Field(
        default=None, description="Total paid in EUR exactly as printed; null if the total is not visible"
    )
    items: list[ExtractedItem]


# ---------- Draft sent to the phone for review ----------

MatchSource = Literal["alias", "fuzzy_alias", "catalogue", "new", "discount"]


class DraftItem(BaseModel):
    line_no: int
    raw_name: str
    quantity: float
    line_total: float
    is_discount: bool
    product_name: str | None
    generic_name: str | None
    category: str | None
    match_source: MatchSource
    match_score: float | None = None


class ReceiptDraft(BaseModel):
    store: str
    purchased_on: date | None
    total: float
    items: list[DraftItem]
    items_sum: float
    total_missing: bool = False  # total not visible on the photo; using the sum of the lines
    date_missing: bool = False  # date not visible on the photo
    possible_duplicate_of: int | None = None


# ---------- Confirmed receipt sent back to be saved ----------


class ConfirmItem(BaseModel):
    raw_name: str = Field(min_length=1)
    quantity: float = 1
    line_total: float
    is_discount: bool = False
    product_name: str | None = None
    generic_name: str | None = None
    category: str | None = None


class ConfirmReceipt(BaseModel):
    store: str = Field(min_length=1)
    purchased_on: date
    total: float
    paid_by: str
    items: list[ConfirmItem] = Field(min_length=1)


class LoginRequest(BaseModel):
    password: str
