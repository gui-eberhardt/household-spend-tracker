"""Pure normalization helpers (no database access), easy to unit test.

The idea: the same product is printed differently by each supermarket
('TESCO SEMI SKMD MLK 2L', 'Milbona Semi Skimmed Milk 2L', ...). We
normalize in three layers:

1. ``clean_raw`` turns a receipt line into a stable key for alias lookups.
2. ``best_match`` fuzzy-matches a key/name against known candidates.
3. The OCR model suggests a canonical name, which we match against the
   existing product catalogue so two stores end up on the same product.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process, utils

CATEGORIES = [
    "Fruit & Veg",
    "Meat & Fish",
    "Dairy & Eggs",
    "Bakery",
    "Pantry",
    "Frozen",
    "Snacks & Sweets",
    "Drinks",
    "Alcohol",
    "Household & Cleaning",
    "Personal Care",
    "Baby & Pets",
    "Other",
]

# Canonical store names; anything else is title-cased and created on the fly.
KNOWN_STORES = [
    "Tesco",
    "Lidl",
    "Aldi",
    "Dunnes Stores",
    "SuperValu",
    "Centra",
    "Spar",
    "Londis",
    "Marks & Spencer",
    "Iceland",
    "Penneys",
    "Boots",
    "Mace",
    "Costcutter",
]

_PRICE_TAIL = re.compile(r"\s+-?€?\d+[.,]\d{2}\s*[A-Z]?$")
_QTY_PREFIX = re.compile(r"^\d+\s*[xX@]\s+")
_NON_WORD = re.compile(r"[^\w%./ ]+")
_SPACES = re.compile(r"\s+")


def clean_raw(raw: str) -> str:
    """Stable lookup key for a receipt line.

    >>> clean_raw('  2 x Tesco Semi-Skmd  Mlk 2L   1.89 A')
    'TESCO SEMI SKMD MLK 2L'
    """
    text = raw.strip().upper()
    text = _PRICE_TAIL.sub("", text)
    text = _QTY_PREFIX.sub("", text)
    text = text.replace("-", " ")
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def name_key(name: str) -> str:
    """Case/space-insensitive key for canonical product names."""
    return _SPACES.sub(" ", name.strip().lower())


@dataclass(frozen=True)
class Match:
    value: str
    score: float
    index: int


_NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUMBERS.findall(text)}


def _size_aware_ratio(a: str, b: str, *, score_cutoff: float | None = None, **_: object) -> float:
    """token_sort_ratio, but 'milk 2L' never matches 'milk 1L'."""
    if _numbers(a) != _numbers(b):
        return 0.0
    return fuzz.token_sort_ratio(a, b, score_cutoff=score_cutoff)


def best_match(query: str, candidates: list[str], threshold: float) -> Match | None:
    """Best fuzzy match (0-100) at or above ``threshold``, else None.

    Pack sizes / numbers must be identical for a match.
    """
    if not query or not candidates:
        return None
    result = process.extractOne(
        query,
        candidates,
        scorer=_size_aware_ratio,
        processor=utils.default_process,  # lowercase + ignore punctuation ('semi-skimmed' == 'semi skimmed')
        score_cutoff=threshold,
    )
    if result is None:
        return None
    value, score, index = result
    return Match(value=value, score=float(score), index=int(index))


def normalize_category(category: str | None) -> str:
    if not category:
        return "Other"
    for known in CATEGORIES:
        if known.lower() == category.strip().lower():
            return known
    match = best_match(category, CATEGORIES, threshold=80)
    return match.value if match else "Other"


def normalize_store(name: str | None, existing: list[str]) -> str:
    """Map an OCR store name ('TESCO IRELAND LTD') to a canonical one."""
    if not name or not name.strip():
        return "Unknown store"
    candidates = list(dict.fromkeys(KNOWN_STORES + existing))
    lowered = name.lower()
    # Containment first: 'TESCO IRELAND LTD' -> 'Tesco'
    for store in candidates:
        if re.search(rf"\b{re.escape(store.lower())}\b", lowered):
            return store
    match = best_match(name, candidates, threshold=85)
    if match:
        return match.value
    return _SPACES.sub(" ", name.strip()).title()
