-- Household Spend Tracker schema (PostgreSQL / Neon)
-- Idempotent: safe to run on every startup.

CREATE TABLE IF NOT EXISTS stores (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,          -- canonical name, e.g. 'Tesco', 'Lidl'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per canonical product, shared by every store.
--   name         specific product, e.g. 'Semi-skimmed milk 2L'
--   generic_name product group used for analysis, e.g. 'Milk'
CREATE TABLE IF NOT EXISTS products (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    generic_name  TEXT NOT NULL,
    category      TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS products_name_ci ON products (lower(name));

-- How each store writes a product on the receipt ('TESCO SEMI SKMD MLK 2L').
-- This is the normalization memory: once an alias is confirmed, the same
-- receipt line is mapped to the same product automatically next time.
CREATE TABLE IF NOT EXISTS product_aliases (
    id          SERIAL PRIMARY KEY,
    store_id    INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    raw_key     TEXT NOT NULL,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (store_id, raw_key)
);

CREATE TABLE IF NOT EXISTS receipts (
    id            SERIAL PRIMARY KEY,
    store_id      INTEGER NOT NULL REFERENCES stores(id),
    purchased_on  DATE NOT NULL,
    total         NUMERIC(10, 2) NOT NULL,
    paid_by       TEXT NOT NULL,               -- household member who scanned/paid
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS receipts_purchased_on ON receipts (purchased_on);

CREATE TABLE IF NOT EXISTS receipt_items (
    id           SERIAL PRIMARY KEY,
    receipt_id   INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    line_no      INTEGER NOT NULL,
    raw_name     TEXT NOT NULL,                -- exactly as printed on the receipt
    product_id   INTEGER REFERENCES products(id),  -- NULL for discount lines
    quantity     NUMERIC(10, 3) NOT NULL DEFAULT 1,
    line_total   NUMERIC(10, 2) NOT NULL,      -- negative for discounts
    is_discount  BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS receipt_items_product ON receipt_items (product_id);

-- Flat view for analysis / BI tools (Power BI, notebooks, future dashboard).
CREATE OR REPLACE VIEW v_spending AS
SELECT
    r.id            AS receipt_id,
    r.purchased_on,
    date_trunc('month', r.purchased_on)::date AS month,
    r.paid_by,
    s.name          AS store,
    i.line_no,
    i.raw_name,
    COALESCE(p.name, 'Discount')         AS product,
    COALESCE(p.generic_name, 'Discount') AS generic_name,
    COALESCE(p.category, 'Discounts')    AS category,
    i.quantity,
    i.line_total,
    i.is_discount
FROM receipts r
JOIN stores s        ON s.id = r.store_id
JOIN receipt_items i ON i.receipt_id = r.id
LEFT JOIN products p ON p.id = i.product_id;
