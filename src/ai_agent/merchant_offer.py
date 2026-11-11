"""Normalize a single public price directly from a product page's structured data."""

import re
from urllib.parse import urljoin, urlsplit


def _schema_types(node: dict) -> set[str]:
    value = node.get("@type", [])
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list):
        return set()
    return {item.rsplit("/", 1)[-1].casefold() for item in values if isinstance(item, str)}


def _page_key(url: str) -> tuple | None:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return None
        return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), parts.path.rstrip("/")
    except ValueError:
        return None


def _matches_page(candidate, page_url: str, page_key: tuple) -> bool:
    if not isinstance(candidate, str):
        return False
    try:
        absolute = urljoin(page_url, candidate)
        query = urlsplit(absolute).query
        return _page_key(absolute) == page_key and (not query or query == urlsplit(page_url).query)
    except ValueError:
        return False


def merchant_offer(structured_data: list, page_url: str, checked_at: str) -> dict | None:
    page_key = _page_key(page_url)
    if page_key is None:
        return None
    # Accept Product nodes at the document root or under a JSON-LD graph.
    nodes = [item for item in structured_data if isinstance(item, dict)]
    nodes.extend(
        child for item in nodes[:] if isinstance(item.get("@graph"), list)
        for child in item["@graph"][:50] if isinstance(child, dict)
    )
    products = [node for node in nodes if "product" in _schema_types(node)]
    if len(products) != 1:
        return None
    product = products[0]
    title = product.get("name")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 180:
        return None
    product_url = product.get("url")
    if product_url is not None and not _matches_page(product_url, page_url, page_key):
        return None
    offers = product.get("offers")
    if isinstance(offers, list) and len(offers) == 1:
        offers = offers[0]
    if not isinstance(offers, dict) or "offer" not in _schema_types(offers):
        return None
    offer_url = offers.get("url")
    if offer_url is not None and not _matches_page(offer_url, page_url, page_key):
        return None
    price = str(offers.get("price", "")).strip()
    currency = offers.get("priceCurrency")
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,4})?", price, flags=re.ASCII):
        return None
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        return None
    return {"title": title.strip(), "price": price, "currency": currency, "url": page_url, "checked_at": checked_at}
