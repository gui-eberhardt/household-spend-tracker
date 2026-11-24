import pytest

from app.normalize import best_match, clean_raw, normalize_category, normalize_store


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  2 x Tesco Semi-Skmd  Mlk 2L   1.89 A", "TESCO SEMI SKMD MLK 2L"),
        ("Bananas Loose", "BANANAS LOOSE"),
        ("MILBONA S/SKIM MILK 2L €1.79", "MILBONA S/SKIM MILK 2L"),
        ("Coca-Cola 1.5L", "COCA COLA 1.5L"),
        ("Chicken Fillets 500g -0.50", "CHICKEN FILLETS 500G"),
    ],
)
def test_clean_raw(raw, expected):
    assert clean_raw(raw) == expected


def test_best_match_threshold():
    names = ["Semi-skimmed milk 2L", "Whole milk 2L", "Bananas (loose)"]
    assert best_match("Semi skimmed milk 2L", names, 90).value == "Semi-skimmed milk 2L"
    assert best_match("Orange juice 1L", names, 90) is None


def test_best_match_never_mixes_pack_sizes():
    names = ["Semi-skimmed milk 2L", "Chicken fillets 500g"]
    assert best_match("Semi-skimmed milk 1L", names, 80) is None
    assert best_match("Chicken fillets 1kg", names, 80) is None
    assert best_match("chicken fillets 500g", names, 90).value == "Chicken fillets 500g"


@pytest.mark.parametrize(
    "ocr, expected",
    [
        ("TESCO IRELAND LTD", "Tesco"),
        ("Lidl Ireland GmbH", "Lidl"),
        ("DUNNES STORES CORNELSCOURT", "Dunnes Stores"),
        ("supervalu", "SuperValu"),
        ("Brazil Store Dublin", "Brazil Store Dublin"),
        ("", "Unknown store"),
    ],
)
def test_normalize_store(ocr, expected):
    assert normalize_store(ocr, []) == expected


def test_normalize_category():
    assert normalize_category("dairy & eggs") == "Dairy & Eggs"
    assert normalize_category("Household and Cleaning") == "Household & Cleaning"
    assert normalize_category("spaceships") == "Other"
    assert normalize_category(None) == "Other"
