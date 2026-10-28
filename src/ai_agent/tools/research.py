from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from time import monotonic
from typing import Any, Callable
from urllib import parse
import json
import re

from ai_agent.security import PROMPT_INJECTION_PATTERN
from ai_agent.tools.base import ToolResult, failure_result
from ai_agent.tools.browser import BrowserFailure, PlaywrightBrowserRunner
from ai_agent.tools.http import HttpSandbox


READABLE_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "application/json", "text/plain")
LOGIN_MARKERS = ("sign in", "log in", "login", "войти", "авторизация")
PAYWALL_MARKERS = ("subscribe to continue", "subscription required", "paywall", "оформите подписку")
ANTIBOT_MARKERS = ("verify you are human", "checking your browser", "captcha", "access denied", "bot detection")
POLICY_MARKERS = ("disallowed by robots", "robots.txt", "unavailable for legal reasons")
BOILERPLATE_MARKERS = (
    "accept all cookies", "cookie settings", "privacy policy", "terms of use",
    "принять все файлы cookie", "настройки cookie",
)
SUPPORT_MATRIX = {
    "static_html": "supported_http",
    "json_ld": "supported_http",
    "article_main": "supported_http",
    "tables_lists": "supported_http",
    "spa_javascript": "browser_fallback",
    "apple_books": "site_adapter_fail_closed",
    "apple_tv": "site_adapter_fail_closed",
    "login": "refused",
    "paywall": "refused",
    "anti_bot": "typed_failure",
    "private_network": "refused",
    "downloads": "refused",
}


@dataclass(frozen=True, slots=True)
class ResearchFailure(Exception):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class BrowserResearchReader:
    """Two-tier public-page reader: bounded HTTP first, isolated browser only when needed."""

    def __init__(
        self,
        http: HttpSandbox,
        browser: PlaywrightBrowserRunner,
        *,
        cache_ttl_seconds: int = 300,
        max_fragments: int = 8,
    ) -> None:
        self.http = http
        self.browser = browser
        self.cache_ttl_seconds = max(0, min(int(cache_ttl_seconds), 3600))
        self.max_fragments = max(1, min(int(max_fragments), 20))
        self._cache: dict[str, tuple[float, dict]] = {}
        self._trace: list[dict[str, Any]] = []

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def support_matrix(self) -> dict[str, str]:
        return dict(SUPPORT_MATRIX)

    def trace(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._trace[-50:]]

    def read(
        self,
        *,
        url: str,
        locale: str = "ru-RU",
        region: str = "auto",
        catalog: str = "auto",
        force_browser: bool = False,
    ) -> dict:
        normalized_url = _normalize_url(url)
        normalized_locale = _normalize_locale(locale)
        normalized_region = _normalize_region(region)
        normalized_catalog = catalog.strip().casefold() or "auto"
        cache_key = sha256(
            f"{normalized_url}\0{normalized_locale}\0{normalized_region}\0{normalized_catalog}\0{force_browser}".encode()
        ).hexdigest()
        cached = self._cache.get(cache_key)
        if cached and monotonic() - cached[0] <= self.cache_ttl_seconds:
            result = _deepcopy(cached[1])
            result["cache_hit"] = True
            self._record_trace(result, "cache")
            return result

        adapter = _site_adapter(normalized_url, normalized_catalog)
        started = monotonic()
        try:
            result = self._read_uncached(
                normalized_url,
                locale=normalized_locale,
                region=normalized_region,
                catalog=normalized_catalog,
                adapter=adapter,
                force_browser=force_browser,
            )
        except ResearchFailure as exc:
            self._trace.append(
                {
                    "url_hash": sha256(normalized_url.encode()).hexdigest(),
                    "method": "failed",
                    "adapter": adapter,
                    "code": exc.code,
                    "latency_ms": round((monotonic() - started) * 1000, 1),
                }
            )
            raise
        result["cache_hit"] = False
        self._cache[cache_key] = (monotonic(), _deepcopy(result))
        self._record_trace(result, result["extraction_method"], started)
        return result

    def _read_uncached(
        self,
        url: str,
        *,
        locale: str,
        region: str,
        catalog: str,
        adapter: str,
        force_browser: bool,
    ) -> dict:
        http_fields: dict[str, Any] = {}
        http_response: dict[str, Any] | None = None
        if not force_browser:
            try:
                http_response = self.http.fetch(
                    url,
                    headers={"Accept": "text/html,application/xhtml+xml,application/json;q=0.9,text/plain;q=0.5"},
                )
                _validate_http_response(http_response)
                http_fields = extract_public_page(
                    http_response["body"],
                    http_response["headers"],
                )
                refusal = _classify_refusal(http_response["status"], http_fields, url)
                if refusal:
                    raise refusal
                if not _needs_browser(http_fields):
                    return _research_result(
                        url=http_response["url"],
                        locale=locale,
                        region=region,
                        adapter=adapter,
                        method="http",
                        status=http_response["status"],
                        fields=http_fields,
                        catalog=catalog,
                        max_fragments=self.max_fragments,
                    )
            except ResearchFailure:
                raise
            except ValueError as exc:
                message = str(exc)
                code = "private_network_blocked" if "private" in message.casefold() or "local" in message.casefold() else "http_failed"
                raise ResearchFailure(code, message, retryable=code == "http_failed") from exc

        available, reason = self.browser.availability()
        if not available:
            raise ResearchFailure(
                "unsupported_dynamic_page",
                reason or "Dynamic page требует browser fallback, но он недоступен.",
                details={"http_text_chars": len(str(http_fields.get("main_text", "")))},
            )
        try:
            snapshot = self.browser.read(url=url, screenshot=False)
        except BrowserFailure as exc:
            mapped = exc.code if exc.code in {
                "private_network_blocked", "host_not_allowed", "timeout", "blocked_navigation"
            } else "unsupported_dynamic_page"
            raise ResearchFailure(mapped, exc.message, exc.retryable, exc.details) from exc
        browser_fields = {
            "title": snapshot.get("title", ""),
            "main_text": snapshot.get("main_text", ""),
            "lists": snapshot.get("lists", []),
            "tables": snapshot.get("tables", []),
            "json_ld": snapshot.get("json_ld", []),
            "links": snapshot.get("links", []),
            "script_count": snapshot.get("script_count", 0),
        }
        refusal = _classify_refusal(snapshot.get("status") or 200, browser_fields, snapshot.get("url", url))
        if refusal:
            raise refusal
        if not str(browser_fields.get("main_text", "")).strip() and not browser_fields["json_ld"]:
            raise ResearchFailure("unsupported_dynamic_page", "Страница не предоставила извлекаемых публичных данных.")
        return _research_result(
            url=snapshot.get("url", url),
            locale=locale,
            region=region,
            adapter=adapter,
            method="browser",
            status=snapshot.get("status"),
            fields=browser_fields,
            catalog=catalog,
            max_fragments=self.max_fragments,
        )

    def _record_trace(self, result: dict, method: str, started: float | None = None) -> None:
        self._trace.append(
            {
                "url_hash": sha256(str(result.get("url", "")).encode()).hexdigest(),
                "method": method,
                "adapter": result.get("adapter", "generic"),
                "evidence_count": len(result.get("evidence", [])),
                "confidence": result.get("confidence", 0),
                "latency_ms": 0 if started is None else round((monotonic() - started) * 1000, 1),
            }
        )


class _PublicPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.main_parts: list[str] = []
        self.all_parts: list[str] = []
        self.lists: list[list[str]] = []
        self.tables: list[list[list[str]]] = []
        self.json_ld: list[Any] = []
        self.links: list[dict[str, str]] = []
        self.script_count = 0
        self._title_depth = 0
        self._main_depth = 0
        self._skip_depth = 0
        self._list: list[str] | None = None
        self._list_item: list[str] | None = None
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._json_ld_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag == "title":
            self._title_depth += 1
        if tag in {"main", "article"} or attributes.get("role", "").casefold() == "main":
            self._main_depth += 1
        if tag in {"nav", "header", "footer", "aside", "form", "noscript", "style"}:
            self._skip_depth += 1
        if tag == "script":
            self.script_count += 1
            if attributes.get("type", "").casefold() == "application/ld+json":
                self._json_ld_parts = []
            else:
                self._skip_depth += 1
        if tag in {"ul", "ol"} and self._skip_depth == 0:
            self._list = []
        elif tag == "li" and self._list is not None:
            self._list_item = []
        if tag == "table" and self._skip_depth == 0:
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        if tag == "a" and self._skip_depth == 0:
            href = attributes.get("href", "").strip()
            if href and len(self.links) < 100:
                self.links.append({"url": href, "text": ""})

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in {"main", "article"} and self._main_depth:
            self._main_depth -= 1
        if tag in {"nav", "header", "footer", "aside", "form", "noscript", "style"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "script":
            if self._json_ld_parts is not None:
                raw = "".join(self._json_ld_parts).strip()
                if raw:
                    try:
                        self.json_ld.append(json.loads(raw))
                    except json.JSONDecodeError:
                        pass
                self._json_ld_parts = None
            elif self._skip_depth:
                self._skip_depth -= 1
        if tag == "li" and self._list is not None and self._list_item is not None:
            item = _clean_text(" ".join(self._list_item))
            if item:
                self._list.append(item)
            self._list_item = None
        elif tag in {"ul", "ol"} and self._list is not None:
            if self._list:
                self.lists.append(self._list[:50])
            self._list = None
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(_clean_text(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table[:100])
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)
            return
        cleaned = _clean_text(data)
        if not cleaned or self._skip_depth:
            return
        self.all_parts.append(cleaned)
        if self._title_depth:
            self.title_parts.append(cleaned)
        if self._main_depth:
            self.main_parts.append(cleaned)
        if self._list_item is not None:
            self._list_item.append(cleaned)
        if self._cell is not None:
            self._cell.append(cleaned)


def extract_public_page(body: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    content_type = _header(headers, "content-type").casefold()
    if "application/json" in content_type or body.lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            text = _clean_text(json.dumps(payload, ensure_ascii=False))
            return {"title": "", "main_text": text, "lists": [], "tables": [], "json_ld": [payload], "links": [], "script_count": 0}
    parser = _PublicPageParser()
    try:
        parser.feed(body)
        parser.close()
    except (ValueError, TypeError):
        pass
    main = _clean_text("\n".join(parser.main_parts or parser.all_parts))
    for marker in BOILERPLATE_MARKERS:
        main = re.sub(re.escape(marker), "", main, flags=re.IGNORECASE)
    return {
        "title": _clean_text(" ".join(parser.title_parts))[:500],
        "main_text": main[:50000],
        "lists": parser.lists[:20],
        "tables": parser.tables[:10],
        "json_ld": parser.json_ld[:20],
        "links": parser.links[:100],
        "script_count": parser.script_count,
    }


def research_read_result(reader: BrowserResearchReader, arguments: dict) -> ToolResult:
    try:
        output = reader.read(
            url=str(arguments.get("url", "")),
            locale=str(arguments.get("locale", "ru-RU")),
            region=str(arguments.get("region", "auto")),
            catalog=str(arguments.get("catalog", "auto")),
            force_browser=bool(arguments.get("force_browser", False)),
        )
    except ResearchFailure as exc:
        return failure_result(exc.code, exc.message, retryable=exc.retryable, details=exc.details or {})
    return ToolResult(status="completed", output=output)


def _research_result(*, url: str, locale: str, region: str, adapter: str, method: str, status: int | None, fields: dict, catalog: str, max_fragments: int) -> dict:
    _validate_adapter(adapter, url, region, catalog, fields)
    evidence = _evidence(fields, max_fragments)
    if not evidence:
        raise ResearchFailure("no_public_evidence", "Страница не содержит достаточных публичных данных для цитирования.")
    confidence = 0.95 if adapter != "generic" else 0.85 if fields.get("json_ld") else 0.75 if len(str(fields.get("main_text", ""))) >= 500 else 0.6
    injection = bool(PROMPT_INJECTION_PATTERN.search(str(fields.get("main_text", ""))))
    return {
        "url": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "locale": locale,
        "region": _resolved_region(url, region),
        "status": status,
        "extraction_method": method,
        "adapter": adapter,
        "confidence": confidence,
        "fields": fields,
        "evidence": evidence,
        "safety": {"untrusted_external_data": True, "prompt_injection_detected": injection},
        "support": SUPPORT_MATRIX.get(adapter, "supported_generic"),
    }


def _validate_http_response(response: dict) -> None:
    disposition = _header(response.get("headers"), "content-disposition").casefold()
    content_type = _header(response.get("headers"), "content-type").split(";", 1)[0].strip().casefold()
    if "attachment" in disposition or (content_type and not any(content_type.startswith(item) for item in READABLE_CONTENT_TYPES)):
        raise ResearchFailure("download_blocked", "Research reader не загружает файлы или бинарные ответы.")
    if response.get("truncated"):
        raise ResearchFailure("response_too_large", "Страница превышает безопасный лимит ответа.")


def _classify_refusal(status: int, fields: dict, url: str) -> ResearchFailure | None:
    searchable = f"{fields.get('title', '')} {fields.get('main_text', '')} {url}".casefold()
    if status == 451 or any(marker in searchable for marker in POLICY_MARKERS):
        return ResearchFailure("robots_or_policy", "Страница недоступна из-за robots или policy-ограничения.")
    if status in {401, 407} or any(marker in searchable for marker in LOGIN_MARKERS) and len(str(fields.get("main_text", ""))) < 1500:
        return ResearchFailure("requires_login", "Для чтения страницы требуется вход; личные сессии не используются.")
    if status == 402 or any(marker in searchable for marker in PAYWALL_MARKERS):
        return ResearchFailure("paywall", "Публичный контент скрыт paywall.")
    if status in {403, 429} and any(marker in searchable for marker in ANTIBOT_MARKERS):
        return ResearchFailure("anti_bot", "Сайт отклонил изолированный reader anti-bot проверкой.", True)
    if status >= 400:
        return ResearchFailure("http_status", f"Страница вернула HTTP {status}.", status >= 500)
    return None


def _needs_browser(fields: dict) -> bool:
    text = str(fields.get("main_text", "")).strip()
    return len(text) < 160 and int(fields.get("script_count", 0)) > 0 and not fields.get("json_ld")


def _site_adapter(url: str, catalog: str) -> str:
    parsed = parse.urlparse(url)
    host = (parsed.hostname or "").casefold()
    if catalog == "apple_books" or host == "books.apple.com":
        return "apple_books"
    if catalog in {"apple_tv", "apple_tv_plus"} or host == "tv.apple.com":
        return "apple_tv"
    return "generic"


def _validate_adapter(adapter: str, url: str, region: str, catalog: str, fields: dict) -> None:
    parsed = parse.urlparse(url)
    path = parsed.path.casefold()
    searchable = f"{fields.get('title', '')} {fields.get('main_text', '')} {json.dumps(fields.get('json_ld', []), ensure_ascii=False)}".casefold()
    if adapter == "apple_books":
        if parsed.hostname != "books.apple.com" or "/book/" not in path:
            raise ResearchFailure("catalog_unconfirmed", "Не подтверждена прямая карточка Apple Books.")
    elif adapter == "apple_tv":
        if parsed.hostname != "tv.apple.com" or not any(part in path for part in ("/movie/", "/show/")):
            raise ResearchFailure("catalog_unconfirmed", "Не подтверждена прямая карточка Apple TV.")
        if catalog == "apple_tv_plus" and not any(marker in searchable for marker in ("apple tv+", "apple tv plus", "included with apple tv+")):
            raise ResearchFailure("availability_unconfirmed", "Страница не подтверждает доступность по подписке Apple TV+.")
    if adapter in {"apple_books", "apple_tv"} and region == "auto" and _region_from_path(path) is None:
        raise ResearchFailure("region_unknown", "Storefront/region страницы не удалось подтвердить.")


def _evidence(fields: dict, limit: int) -> list[dict[str, str]]:
    fragments: list[tuple[str, str]] = []
    title = _clean_text(str(fields.get("title", "")))
    if title:
        fragments.append(("title", title))
    main = _clean_text(str(fields.get("main_text", "")))
    if main:
        for chunk in re.split(r"(?<=[.!?])\s+|\n+", main):
            chunk = _clean_text(chunk)
            if len(chunk) >= 20:
                fragments.append(("main", chunk[:500]))
            if len(fragments) >= limit:
                break
        if not fragments and main:
            fragments.append(("main", main[:500]))
    if len(fragments) < limit:
        for item in fields.get("json_ld", []):
            fragment = _clean_text(json.dumps(item, ensure_ascii=False))[:500]
            if fragment:
                fragments.append(("json_ld", fragment))
            if len(fragments) >= limit:
                break
    seen: set[str] = set()
    result = []
    for source, fragment in fragments:
        digest = sha256(fragment.encode()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        result.append({"source": source, "fragment": fragment, "sha256": digest})
    return result[:limit]


def _normalize_url(value: str) -> str:
    parsed = parse.urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ResearchFailure("invalid_url", "Research URL должен быть публичным http/https URL.")
    if parsed.username or parsed.password:
        raise ResearchFailure("credentials_forbidden", "Credentials в URL запрещены.")
    return parsed.geturl()


def _normalize_locale(value: str) -> str:
    cleaned = value.strip() or "ru-RU"
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2})?", cleaned):
        raise ResearchFailure("invalid_locale", "Locale должен иметь формат ru или ru-RU.")
    return cleaned


def _normalize_region(value: str) -> str:
    cleaned = value.strip().casefold() or "auto"
    if cleaned != "auto" and not re.fullmatch(r"[a-z]{2}", cleaned):
        raise ResearchFailure("invalid_region", "Region должен быть двухбуквенным кодом или auto.")
    return cleaned


def _resolved_region(url: str, requested: str) -> str:
    if requested != "auto":
        return requested
    return _region_from_path(parse.urlparse(url).path) or "unknown"


def _region_from_path(path: str) -> str | None:
    first = path.strip("/").split("/", 1)[0].casefold()
    return first if re.fullmatch(r"[a-z]{2}", first) else None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _header(headers: dict[str, str] | None, name: str) -> str:
    return next((str(value) for key, value in (headers or {}).items() if str(key).casefold() == name.casefold()), "")


def _deepcopy(value: dict) -> dict:
    return json.loads(json.dumps(value, ensure_ascii=False))
