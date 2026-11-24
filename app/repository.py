"""Database access and the product-matching logic that needs the database."""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

from app.normalize import (
    best_match,
    clean_raw,
    name_key,
    normalize_category,
    normalize_store,
)
from app.schemas import (
    ConfirmReceipt,
    DraftItem,
    ExtractedReceipt,
    ReceiptDraft,
)

# ---------- stores & products ----------


def store_names(conn: psycopg.Connection) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM stores ORDER BY name")]


def get_or_create_store(conn: psycopg.Connection, name: str) -> int:
    row = conn.execute(
        "INSERT INTO stores (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (name,),
    ).fetchone()
    return row["id"]


def list_products(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        "SELECT id, name, generic_name, category FROM products ORDER BY name"
    ).fetchall()


def get_or_create_product(
    conn: psycopg.Connection, name: str, generic_name: str | None, category: str | None
) -> int:
    clean_name = " ".join(name.split())
    row = conn.execute(
        "SELECT id FROM products WHERE lower(name) = %s", (name_key(clean_name),)
    ).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "INSERT INTO products (name, generic_name, category) VALUES (%s, %s, %s) RETURNING id",
        (clean_name, (generic_name or clean_name).strip(), normalize_category(category)),
    ).fetchone()
    return row["id"]


# ---------- normalization against what we already know ----------


def build_draft(
    conn: psycopg.Connection,
    extracted: ExtractedReceipt,
    alias_threshold: float,
    product_threshold: float,
) -> ReceiptDraft:
    store = normalize_store(extracted.store_name, store_names(conn))
    store_row = conn.execute("SELECT id FROM stores WHERE name = %s", (store,)).fetchone()

    aliases: list[dict] = []
    if store_row:
        aliases = conn.execute(
            """SELECT a.raw_key, p.name, p.generic_name, p.category
               FROM product_aliases a JOIN products p ON p.id = a.product_id
               WHERE a.store_id = %s""",
            (store_row["id"],),
        ).fetchall()
    alias_by_key = {a["raw_key"]: a for a in aliases}
    alias_keys = [a["raw_key"] for a in aliases]

    products = list_products(conn)
    product_names = [p["name"] for p in products]

    items: list[DraftItem] = []
    for n, item in enumerate(extracted.items, start=1):
        base = dict(
            line_no=n,
            raw_name=item.raw_name,
            quantity=item.quantity or 1,
            line_total=round(item.line_total, 2),
            is_discount=item.is_discount or item.line_total < 0,
        )
        if base["is_discount"]:
            items.append(
                DraftItem(**base, product_name=None, generic_name=None, category=None, match_source="discount")
            )
            continue

        key = clean_raw(item.raw_name)

        # 1) This store already printed exactly this line before.
        if key in alias_by_key:
            a = alias_by_key[key]
            items.append(
                DraftItem(**base, product_name=a["name"], generic_name=a["generic_name"],
                          category=a["category"], match_source="alias", match_score=100)
            )
            continue

        # 2) Very similar line from the same store (typos / OCR noise).
        m = best_match(key, alias_keys, alias_threshold)
        if m:
            a = alias_by_key[m.value]
            items.append(
                DraftItem(**base, product_name=a["name"], generic_name=a["generic_name"],
                          category=a["category"], match_source="fuzzy_alias", match_score=m.score)
            )
            continue

        # 3) The model's suggested name matches a product from any store.
        m = best_match(item.suggested_name, product_names, product_threshold)
        if m:
            p = products[m.index]
            items.append(
                DraftItem(**base, product_name=p["name"], generic_name=p["generic_name"],
                          category=p["category"], match_source="catalogue", match_score=m.score)
            )
            continue

        # 4) New product: keep the model's suggestion for the user to confirm.
        items.append(
            DraftItem(**base, product_name=item.suggested_name.strip(),
                      generic_name=item.generic_name.strip(),
                      category=normalize_category(item.category), match_source="new")
        )

    purchased_on = _parse_date(extracted.purchase_date)
    items_sum = round(sum(i.line_total for i in items), 2)
    total_missing = extracted.total is None
    total = items_sum if total_missing else round(extracted.total, 2)
    duplicate = None
    if store_row and purchased_on:
        dup = conn.execute(
            "SELECT id FROM receipts WHERE store_id = %s AND purchased_on = %s AND total = %s LIMIT 1",
            (store_row["id"], purchased_on, total),
        ).fetchone()
        duplicate = dup["id"] if dup else None

    return ReceiptDraft(
        store=store,
        purchased_on=purchased_on,
        total=total,
        items=items,
        items_sum=items_sum,
        total_missing=total_missing,
        date_missing=purchased_on is None,
        possible_duplicate_of=duplicate,
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
    # Guard against OCR misreads such as a year in the far past/future.
    if not (date.today() - timedelta(days=730) <= parsed <= date.today() + timedelta(days=1)):
        return None
    return parsed


# ---------- saving ----------


def save_receipt(conn: psycopg.Connection, receipt: ConfirmReceipt) -> int:
    store_name = normalize_store(receipt.store, store_names(conn))
    store_id = get_or_create_store(conn, store_name)
    receipt_id = conn.execute(
        "INSERT INTO receipts (store_id, purchased_on, total, paid_by) VALUES (%s, %s, %s, %s) RETURNING id",
        (store_id, receipt.purchased_on, round(receipt.total, 2), receipt.paid_by),
    ).fetchone()["id"]

    for n, item in enumerate(receipt.items, start=1):
        is_discount = item.is_discount or item.line_total < 0
        product_id = None
        if not is_discount:
            product_id = get_or_create_product(
                conn, item.product_name or item.raw_name, item.generic_name, item.category
            )
            # Remember (or correct) how this store prints this product.
            conn.execute(
                """INSERT INTO product_aliases (store_id, raw_key, product_id)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (store_id, raw_key)
                   DO UPDATE SET product_id = EXCLUDED.product_id, updated_at = now()""",
                (store_id, clean_raw(item.raw_name), product_id),
            )
        conn.execute(
            """INSERT INTO receipt_items
               (receipt_id, line_no, raw_name, product_id, quantity, line_total, is_discount)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (receipt_id, n, item.raw_name, product_id, item.quantity or 1,
             round(item.line_total, 2), is_discount),
        )
    return receipt_id


def delete_receipt(conn: psycopg.Connection, receipt_id: int) -> bool:
    return conn.execute("DELETE FROM receipts WHERE id = %s", (receipt_id,)).rowcount > 0


# ---------- reading ----------


def month_bounds(month: str) -> tuple[date, date]:
    start = date.fromisoformat(f"{month}-01")
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


def list_receipts(conn: psycopg.Connection, month: str) -> list[dict]:
    start, end = month_bounds(month)
    receipts = conn.execute(
        """SELECT r.id, s.name AS store, r.purchased_on, r.total, r.paid_by
           FROM receipts r JOIN stores s ON s.id = r.store_id
           WHERE r.purchased_on >= %s AND r.purchased_on < %s
           ORDER BY r.purchased_on DESC, r.id DESC""",
        (start, end),
    ).fetchall()
    if not receipts:
        return []
    items = conn.execute(
        """SELECT receipt_id, line_no, raw_name, product, generic_name, category, quantity, line_total, is_discount
           FROM v_spending WHERE receipt_id = ANY(%s) ORDER BY receipt_id, line_no""",
        ([r["id"] for r in receipts],),
    ).fetchall()
    by_receipt: dict[int, list] = {}
    for it in items:
        by_receipt.setdefault(it["receipt_id"], []).append(it)
    for r in receipts:
        r["items"] = by_receipt.get(r["id"], [])
    return receipts


def summary(conn: psycopg.Connection, month: str) -> dict:
    start, end = month_bounds(month)
    params = (start, end)
    where = "purchased_on >= %s AND purchased_on < %s"

    totals = conn.execute(
        f"SELECT COALESCE(SUM(total), 0) AS total, COUNT(*) AS receipts FROM receipts WHERE {where}",
        params,
    ).fetchone()

    def group(sql: str) -> list[dict]:
        return conn.execute(sql, params).fetchall()

    by_person = group(
        f"SELECT paid_by AS label, SUM(total) AS amount FROM receipts WHERE {where} GROUP BY 1 ORDER BY 2 DESC"
    )
    by_store = group(
        f"""SELECT s.name AS label, SUM(r.total) AS amount FROM receipts r
            JOIN stores s ON s.id = r.store_id WHERE {where.replace('purchased_on', 'r.purchased_on')}
            GROUP BY 1 ORDER BY 2 DESC"""
    )
    by_category = group(
        f"SELECT category AS label, SUM(line_total) AS amount FROM v_spending WHERE {where} GROUP BY 1 ORDER BY 2 DESC"
    )
    top_products = group(
        f"""SELECT generic_name AS label, SUM(line_total) AS amount, SUM(quantity) AS quantity
            FROM v_spending WHERE {where} AND NOT is_discount
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10"""
    )

    # Last 6 months (including this one) for the monthly average.
    six_back = date(start.year - (start.month <= 5), (start.month - 6) % 12 + 1, 1)
    monthly = conn.execute(
        """SELECT to_char(date_trunc('month', purchased_on), 'YYYY-MM') AS month, SUM(total) AS amount
           FROM receipts WHERE purchased_on >= %s AND purchased_on < %s GROUP BY 1 ORDER BY 1""",
        (six_back, end),
    ).fetchall()
    amounts = [float(m["amount"]) for m in monthly]

    return {
        "month": month,
        "total": float(totals["total"]),
        "receipts": totals["receipts"],
        "monthly_average": round(sum(amounts) / len(amounts), 2) if amounts else 0.0,
        "monthly": monthly,
        "by_person": by_person,
        "by_store": by_store,
        "by_category": by_category,
        "top_products": top_products,
    }
