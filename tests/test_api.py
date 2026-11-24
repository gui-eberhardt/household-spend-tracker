from datetime import date

from app.schemas import ExtractedItem, ExtractedReceipt

TODAY = date.today().isoformat()
MONTH = TODAY[:7]
JPEG = ("r.jpg", b"\xff\xd8fake", "image/jpeg")


def item(raw, total, name, generic, category, discount=False):
    return ExtractedItem(raw_name=raw, line_total=total, suggested_name=name,
                         generic_name=generic, category=category, is_discount=discount)


def tesco_receipt():
    return ExtractedReceipt(
        store_name="TESCO IRELAND LTD", purchase_date=TODAY, total=4.38,
        items=[
            item("TESCO SEMI SKMD MLK 2L", 1.89, "Semi-skimmed milk 2L", "Milk", "Dairy & Eggs"),
            item("BANANAS LOOSE", 2.99, "Bananas (loose)", "Bananas", "Fruit & Veg"),
            item("CLUBCARD PRICE", -0.50, "Discount", "Discount", "Other", discount=True),
        ],
    )


def confirm(draft, paid_by="Guilherme"):
    return {
        "store": draft["store"], "purchased_on": draft["purchased_on"], "total": draft["total"],
        "paid_by": paid_by,
        "items": [
            {k: i[k] for k in ("raw_name", "quantity", "line_total", "is_discount",
                               "product_name", "generic_name", "category")}
            for i in draft["items"]
        ],
    }


def scan(client, reader, receipt):
    reader.queue.append(receipt)
    r = client.post("/api/receipts/scan", files={"image": JPEG})
    assert r.status_code == 200, r.text
    return r.json()


def test_requires_login(client):
    client.cookies.clear()
    assert client.get("/api/summary").status_code == 401
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401


def test_scan_new_receipt(client, reader):
    draft = scan(client, reader, tesco_receipt())
    assert draft["store"] == "Tesco"
    assert [i["match_source"] for i in draft["items"]] == ["new", "new", "discount"]
    assert draft["items_sum"] == 4.38


def test_alias_is_remembered_and_corrections_stick(client, reader):
    draft = scan(client, reader, tesco_receipt())
    body = confirm(draft)
    body["items"][1]["product_name"] = "Bananas"  # user renames on review
    assert client.post("/api/receipts", json=body).status_code == 201

    again = scan(client, reader, tesco_receipt())
    assert [i["match_source"] for i in again["items"]] == ["alias", "alias", "discount"]
    assert again["items"][1]["product_name"] == "Bananas"
    assert again["possible_duplicate_of"] is not None


def test_same_product_across_stores(client, reader):
    client.post("/api/receipts", json=confirm(scan(client, reader, tesco_receipt())))

    lidl = ExtractedReceipt(
        store_name="Lidl", purchase_date=TODAY, total=1.79,
        items=[item("MILBONA S/SKIM MILK 2L", 1.79, "Semi skimmed milk 2L", "Milk", "Dairy & Eggs")],
    )
    draft = scan(client, reader, lidl)
    assert "Semi-skimmed milk 2L" in reader.last_known  # catalogue is sent to the model
    milk = draft["items"][0]
    assert milk["match_source"] == "catalogue"
    assert milk["product_name"] == "Semi-skimmed milk 2L"
    client.post("/api/receipts", json=confirm(draft, paid_by="Lazia"))

    s = client.get(f"/api/summary?month={MONTH}").json()
    assert s["total"] == 6.17
    assert s["receipts"] == 2
    assert {p["label"]: float(p["amount"]) for p in s["by_person"]} == {"Guilherme": 4.38, "Lazia": 1.79}
    top = {p["label"]: float(p["amount"]) for p in s["top_products"]}
    assert top["Milk"] == 3.68
    cats = {c["label"]: float(c["amount"]) for c in s["by_category"]}
    assert cats["Discounts"] == -0.50


def test_typo_in_ocr_still_matches_alias(client, reader):
    client.post("/api/receipts", json=confirm(scan(client, reader, tesco_receipt())))
    noisy = tesco_receipt()
    noisy.items[0].raw_name = "TESCO SEMI SKMD MILK 2L"
    noisy.items[0].suggested_name = "Something else entirely"
    draft = scan(client, reader, noisy)
    assert draft["items"][0]["match_source"] == "fuzzy_alias"
    assert draft["items"][0]["product_name"] == "Semi-skimmed milk 2L"


def test_receipts_list_and_delete(client, reader):
    rid = client.post("/api/receipts", json=confirm(scan(client, reader, tesco_receipt()))).json()["id"]
    receipts = client.get(f"/api/receipts?month={MONTH}").json()
    assert len(receipts) == 1 and len(receipts[0]["items"]) == 3
    assert client.delete(f"/api/receipts/{rid}").status_code == 200
    assert client.get(f"/api/receipts?month={MONTH}").json() == []


def test_validation(client, reader):
    body = confirm(scan(client, reader, tesco_receipt()), paid_by="Stranger")
    assert client.post("/api/receipts", json=body).status_code == 422
    assert client.get("/api/summary?month=2026-13").status_code == 422
    r = client.post("/api/receipts/scan", files={"image": ("a.txt", b"hi", "text/plain")})
    assert r.status_code == 415


def test_cut_off_photo_flags_missing_total_and_date(client, reader):
    # Photo cut before the total/date (real case: Dunnes receipt)
    cut = ExtractedReceipt(
        store_name="DUNNES STORES", purchase_date=None, total=None,
        items=[
            item("FAIRY DISH", 16.00, "Washing-up liquid", "Washing-up liquid", "Household & Cleaning"),
            item("FAIRY DISH", 16.00, "Washing-up liquid", "Washing-up liquid", "Household & Cleaning"),
            item("* SAVER DEAL !", -12.00, "Discount", "Discount", "Other", discount=True),
            item("MR MUSCLE", 5.00, "Surface cleaner", "Surface cleaner", "Household & Cleaning"),
        ],
    )
    draft = scan(client, reader, cut)
    assert draft["total_missing"] and draft["date_missing"]
    assert draft["total"] == draft["items_sum"] == 25.00
    assert draft["purchased_on"] is None
