"""Normalize the raw in-page extraction dict into a flat product record."""
from __future__ import annotations

import time
from typing import Any, Optional


def _f(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize(raw: dict[str, Any], url: str, product_id: str, shard: int) -> dict[str, Any]:
    j = raw.get("jsonld") or {}

    brand = j.get("brand") or {}
    if isinstance(brand, str):
        brand = {"name": brand}
    category = j.get("category") or {}
    if isinstance(category, str):
        category = {"name": category}
    offers = j.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    rating = j.get("aggregateRating") or {}
    weight = j.get("weight") or {}

    list_price = _f(offers.get("price"))
    display_price = _f(raw.get("display_price"))
    availability = (offers.get("availability") or "")
    availability = availability.split("/")[-1] if availability else None

    sale_price = None
    if display_price is not None and list_price is not None and display_price < list_price:
        sale_price = display_price

    return {
        "product_id": str(product_id),
        "url": url,
        "name": j.get("name") or raw.get("h1"),
        "brand": brand.get("name"),
        "brand_id": brand.get("identifier"),
        "brand_url": brand.get("url"),
        "category": category.get("name"),
        "category_id": category.get("identifier"),
        "sku": j.get("sku") or j.get("mpn"),
        "mpn": j.get("mpn"),
        "gtin": j.get("gtin12") or j.get("gtin13") or j.get("gtin14") or j.get("gtin"),
        "list_price": list_price,
        "display_price": display_price,
        "sale_price": sale_price,
        "currency": offers.get("priceCurrency") or "USD",
        "price_hidden": bool(raw.get("price_hidden")),
        "availability": availability,
        "rating": _f(rating.get("ratingValue")),
        "review_count": rating.get("reviewCount"),
        "weight_value": weight.get("value") if isinstance(weight, dict) else None,
        "weight_unit": weight.get("unitText") if isinstance(weight, dict) else None,
        "description": j.get("description"),
        "main_image": (raw.get("images") or [None])[0],
        "images": raw.get("images") or [],
        "specs": raw.get("specs") or {},
        "supplement_facts": raw.get("supplement_facts"),
        "ingredients": raw.get("ingredients"),
        "directions": raw.get("directions"),
        "warnings": raw.get("warnings"),
        "shard": shard,
        "scraped_at": int(time.time()),
    }


def is_valid(rec: dict[str, Any]) -> bool:
    """A usable record has a name and some price signal."""
    if not rec.get("name"):
        return False
    return (
        rec.get("list_price") is not None
        or rec.get("display_price") is not None
        or rec.get("price_hidden")
    )
