"""Read-only semantic grading of captured Apple catalog agent runs.

No provider requests. A completed chat or three tool calls is not proof: each
final recommendation must match an item-specific catalog record and storefront.
"""
from __future__ import annotations

import argparse
from html import unescape
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / ".local" / "product-review"


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def canonical_url(value: object) -> str:
    try:
        parsed = urlsplit(str(value or ""))
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return ""
        return f"{parsed.hostname}{unquote(parsed.path).rstrip('/').rstrip('.,)')}".casefold()
    except ValueError:
        return ""


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


def decoded_result(value: object):
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def source_items(run: dict, catalog: str) -> list[dict]:
    items = {}
    for action in run.get("tool_outputs", []):
        if action.get("status") != "completed":
            continue
        payload = decoded_result(action.get("result"))
        if payload is None:
            continue
        for container in dictionaries(payload):
            item = container.get("catalog_item")
            if not isinstance(item, dict) or not item:
                continue
            url = str(item.get("url") or container.get("url") or (payload.get("url") if isinstance(payload,dict) else "") or "")
            key = canonical_url(url)
            host = "tv.apple.com" if catalog == "apple_tv" else "books.apple.com"
            kind = "movie" if catalog == "apple_tv" else "book"
            if key.startswith(f"{host}/us/{kind}/"):
                enriched = {**item, "url": url, "source_tool": action.get("tool_name"), "source_region": container.get("region") or (payload.get("region") if isinstance(payload,dict) else None)}
                # Prefer the richest verified item if it was read more than once.
                if key not in items or len(json.dumps(enriched)) > len(json.dumps(items[key])):
                    items[key] = enriched
    return list(items.values())


def local_final_fragment(answer: str, url: str) -> str:
    key = canonical_url(url)
    matches = list(re.finditer(r"https?://[^\s<>\]]+", answer))
    match = next((item for item in matches if canonical_url(item.group(0)) == key), None)
    if match is None:
        return ""
    line_start = answer.rfind("\n", 0, match.start()) + 1
    line_end = answer.find("\n", match.end())
    line = answer[line_start:line_end if line_end >= 0 else len(answer)]
    if "|" in line:
        return line
    # A numbered/bold heading defines the recommendation's own block.
    heading = re.compile(r"(?m)^\s*(?:\d+[.)]\s+|#{1,6}\s+|\*\*[^\n]+\*\*)")
    before = [item for item in heading.finditer(answer) if item.start() <= match.start()]
    start = before[-1].start() if before else max(0, match.start() - 600)
    following = next((item.start() for item in heading.finditer(answer) if item.start() > match.end()), len(answer))
    return answer[start:following]


def names(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [name for child in value for name in names(child)]
    if isinstance(value, dict):
        return names(value.get("name"))
    return []


def item_authors(item: dict) -> list[str]:
    values = names(item.get("authors") or item.get("author") or item.get("artist_name") or item.get("artistName"))
    # Apple may encode multiple coauthors as one HTML-escaped schema string.
    return [part.strip() for value in values for part in re.split(r"\s+(?:&|and|и)\s+", unescape(value)) if part.strip()]


def verified_price(item: dict) -> tuple[str | None, str | None]:
    value = item.get("price")
    currency = item.get("currency") or item.get("price_currency") or item.get("priceCurrency")
    if isinstance(value, dict):
        currency = value.get("currency") or value.get("priceCurrency") or currency
        value = value.get("amount", value.get("value", value.get("price")))
    if value is None:
        offers = item.get("offers")
        offer = offers[0] if isinstance(offers, list) and offers else offers
        if isinstance(offer, dict):
            value = offer.get("price")
            currency = offer.get("priceCurrency") or offer.get("currency") or currency
    if value is None or isinstance(value, (dict, list, bool)):
        return None, None
    try:
        amount = str(Decimal(str(value).replace(",", ".")).normalize())
    except InvalidOperation:
        return None, None
    return amount, str(currency).upper() if currency else None


def price_in_final(fragment: str, amount: str | None, currency: str | None) -> bool:
    if amount is None or currency != "USD":
        return bool(re.search(r"(?i)цен\w*[^.\n]{0,45}(?:неизвест|не\s+подтвержд|не\s+удалось|не\s+провер)|(?:price[^.\n]{0,35}(?:unknown|unverified))", fragment))
    if Decimal(amount) == 0:
        return bool(re.search(r"(?i)бесплатн|\bfree\b|(?:\$\s*0(?:[.,]00)?\b|\b0(?:[.,]00)?\s*(?:USD|\$))",fragment))
    pattern = re.escape(format(Decimal(amount), "f")).replace(r"\.", "[.,]")
    return bool(re.search(rf"(?i)(?:\$\s*{pattern}\b|\b{pattern}\s*(?:USD|\$))", fragment))


def grade(run: dict, case: str) -> dict:
    answer = str(run.get("answer") or "")
    items = source_items(run, case)
    graded = []
    for item in items:
        fragment = local_final_fragment(answer, item["url"])
        title = str(item.get("title") or item.get("name") or "")
        checks = {
            "official_us_direct_card": bool(canonical_url(item["url"])),
            "source_storefront_matches": str(item.get("region", "")).casefold() == "us" and str(item.get("source_region") or "us").casefold() == "us",
            "final_contains_verified_url": bool(fragment),
            "final_title_matches_source": bool(title and normalize(title) in normalize(fragment)),
        }
        facts = {"url":item["url"],"title":title,"source_tool":item["source_tool"],"source_region":item.get("source_region")}
        if case == "apple_tv":
            provider_id = item.get("provider_id")
            offer_type = item.get("offer_type")
            checks.update({
                "item_bound_apple_tv_subscription": item.get("access") == "subscription" and provider_id == "tvs.sbd.4000" and offer_type == "TV_PLUS",
                "subscription_evidence_present": bool(item.get("access_evidence")),
                "final_subscription_claim_present": bool(re.search(r"(?i)подписк|subscription|TV_PLUS",fragment)),
            })
            year = str(item.get("year") or item.get("release_year") or str(item.get("datePublished") or "")[:4])
            if year:
                checks["final_year_matches_source"] = bool(re.search(rf"\b{re.escape(year)}\b", fragment))
            facts.update({key:item.get(key) for key in ["access","provider_id","offer_type","access_evidence"]})
            facts["year"] = year
        else:
            authors = item_authors(item)
            amount,currency = verified_price(item)
            checks.update({
                "author_source_present": bool(authors),
                "final_author_matches_source": bool(authors and all(normalize(author) in normalize(fragment) for author in authors)),
                "price_is_verified_or_explicitly_unknown": price_in_final(fragment,amount,currency),
            })
            facts.update({"authors":authors,"verified_price":amount,"currency":currency})
        graded.append({"facts":facts,"checks":checks,"passed":all(checks.values()),"final_fragment":fragment})
    passed = [item for item in graded if item["passed"]]
    return {
        "case":case,"model":run.get("model"),"run_status":run.get("status"),"elapsed_seconds":run.get("elapsed_seconds"),
        "grade":"PASS" if run.get("status")=="COMPLETE" and len(passed)>=3 else "FAIL",
        "verified_item_count":len(items),"grounded_final_item_count":len(passed),"items":graded,
        "tool_failures":[{"tool":action.get("tool_name"),"result":action.get("result")} for action in run.get("tool_outputs",[]) if action.get("status")!='completed'],
        "prompt":run.get("prompt"),"answer":answer,
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--directory",type=Path,default=DEFAULT_DIR)
    parser.add_argument("--output",type=Path,default=DEFAULT_DIR/"apple-agent-live.json")
    args=parser.parse_args()
    results=[]
    for case in ["apple_tv","apple_books"]:
        path=args.directory/f"apple-agent-{case}.json"
        if path.is_file():
            results.append(grade(json.loads(path.read_text(encoding="utf-8")),case))
    payload={"method":"item-specific official US card + final grounded title/author/price/provider check", "results":results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(args.output),"grades":[{"case":item["case"],"grade":item["grade"],"verified":item["grounded_final_item_count"]} for item in results]},ensure_ascii=True))
    return 0 if len(results)==2 and all(result["grade"]=="PASS" for result in results) else 1


if __name__=="__main__":raise SystemExit(main())
