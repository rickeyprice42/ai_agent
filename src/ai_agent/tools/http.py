from __future__ import annotations

from html.parser import HTMLParser
from urllib import error, parse, request
from xml.etree import ElementTree
from collections import Counter
import gzip
import ipaddress
import json
import re
import socket
import zlib


ALLOWED_METHODS = {"GET", "HEAD"}
BLOCKED_HEADERS = {"authorization", "cookie", "proxy-authorization"}
SEARCH_ENDPOINT = "https://www.bing.com/search"
SEARCH_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Avelin/0.1"
SEARCH_PROVIDER = "bing_rss"
APPLE_SEARCH_ENDPOINT = "https://itunes.apple.com/search"
APPLE_LOOKUP_ENDPOINT = "https://itunes.apple.com/lookup"
APPLE_CHART_ENDPOINT = "https://rss.applemarketingtools.com/api/v2/{country}/books/top-paid/{limit}/books.json"
SEARCH_STOP_WORDS = {
    "a", "affordable", "an", "and", "best", "budget", "buy", "cheap", "for", "good", "in", "of", "on", "or", "recommendation", "recommendations", "the", "to",
    "актуальные", "в", "для", "и", "которые", "купить", "лучшие", "мог", "на", "недорогие",
    "несколько", "по", "рекомендации", "с", "хорошие", "я",
}
JUNK_PATH_PARTS = {"account", "auth", "help", "login", "mail", "signin", "support"}


class HttpSandbox:
    def __init__(
        self,
        timeout_seconds: int = 15,
        max_response_chars: int = 20000,
        allow_private_networks: bool = False,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_chars = max_response_chars
        self.allow_private_networks = allow_private_networks

    def request(
        self,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
    ) -> str:
        parsed = self._validate_url(url)
        normalized_method = method.strip().upper() or "GET"
        if normalized_method not in ALLOWED_METHODS:
            raise ValueError(f"HTTP method not allowed: {method}. Allowed: GET, HEAD.")

        safe_headers = self._validate_headers(headers or {})
        safe_headers.setdefault("Accept-Encoding", "identity")
        http_request = request.Request(
            parsed.geturl(),
            headers=safe_headers,
            method=normalized_method,
        )

        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", response.getcode())
                response_headers = dict(response.headers.items())
                raw_body = b"" if normalized_method == "HEAD" else response.read(self.max_response_chars + 1)
        except error.HTTPError as exc:
            status = exc.code
            response_headers = dict(exc.headers.items())
            raw_body = b"" if normalized_method == "HEAD" else exc.read(self.max_response_chars + 1)
        except error.URLError as exc:
            raise ValueError(f"HTTP request failed: {exc}") from exc

        body, truncated = _decode_body(
            raw_body,
            self.max_response_chars,
            response_headers,
        )
        return _format_response(
            url=parsed.geturl(),
            method=normalized_method,
            status=int(status),
            headers=response_headers,
            body=body,
            truncated=truncated,
        )

    def search(self, query: str, limit: int = 5, country: str | None = None) -> str:
        normalized_query = " ".join(query.split())
        if len(normalized_query) < 2:
            raise ValueError("Search query must contain at least 2 characters.")
        normalized_limit = max(1, min(int(limit), 10))
        apple_books_intent = "apple books" in normalized_query.lower()
        storefront = _normalize_storefront(country, normalized_query)
        provider = SEARCH_PROVIDER
        candidates: list[dict[str, str]] = []
        if apple_books_intent:
            try:
                candidates = self._search_apple_books(
                    normalized_query,
                    max(normalized_limit * 4, 10),
                    storefront,
                )
            except ValueError:
                candidates = []
            if candidates:
                provider = "apple_search_api"

        provider_query = _provider_query(normalized_query)
        search_url = f"{SEARCH_ENDPOINT}?{parse.urlencode({'q': provider_query, 'format': 'rss'})}"
        search_request = request.Request(
            search_url,
            headers={
                "User-Agent": SEARCH_USER_AGENT,
                "Accept-Encoding": "identity",
                "Accept-Language": "ru,en;q=0.8",
            },
            method="GET",
        )
        if not candidates:
            try:
                with request.urlopen(search_request, timeout=self.timeout_seconds) as response:
                    headers = dict(response.headers.items())
                    raw_body = response.read(self.max_response_chars * 8)
            except error.HTTPError as exc:
                raise ValueError(f"Web search returned HTTP {exc.code}.") from exc
            except error.URLError as exc:
                raise ValueError("Web search is temporarily unavailable.") from exc

            body, _ = _decode_body(raw_body, self.max_response_chars * 4, headers)
            candidates = _parse_search_results(body, max(normalized_limit * 4, 10))
        if not candidates:
            raise ValueError("Web search returned no readable results.")
        results, rejected = _rank_search_results(normalized_query, candidates, normalized_limit)
        if not results:
            reasons = ", ".join(f"{key}={value}" for key, value in sorted(rejected.items()))
            suffix = f" ({reasons})" if reasons else ""
            raise ValueError(f"Web search found no relevant results for the request{suffix}.")

        rejected_count = sum(rejected.values())
        reason_summary = ", ".join(f"{key}={value}" for key, value in sorted(rejected.items())) or "none"
        lines = [
            f"Search query: {normalized_query}",
            f"Search provider: {provider}",
            *( [f"Storefront: {storefront}"] if provider == "apple_search_api" else [] ),
            f"Quality: accepted={len(results)}; rejected={rejected_count}",
            f"Rejected reasons: {reason_summary}",
            "Results:",
        ]
        for index, item in enumerate(results, start=1):
            lines.extend(
                [
                    f"{index}. {item['title']}",
                    f"   URL: {item['url']}",
                    f"   Snippet: {item['snippet'] or '<not provided>'}",
                ]
            )
        return "\n".join(lines)

    def _search_apple_books(self, query: str, limit: int, country: str = "us") -> list[dict[str, str]]:
        term = _apple_books_term(query)
        if not term:
            return self._search_apple_books_chart(country, limit)
        url = f"{APPLE_SEARCH_ENDPOINT}?{parse.urlencode({'term': term, 'country': country, 'media': 'ebook', 'entity': 'ebook', 'limit': min(limit, 50)})}"
        payload = self._fetch_apple_json(url, "Apple Books search")
        return _apple_book_candidates(payload, limit)

    def _search_apple_books_chart(self, country: str, limit: int) -> list[dict[str, str]]:
        chart_url = APPLE_CHART_ENDPOINT.format(country=country, limit=min(max(limit, 2), 50))
        chart_payload = self._fetch_apple_json(chart_url, "Apple Books chart")
        feed = chart_payload.get("feed", {}) if isinstance(chart_payload, dict) else {}
        chart_results = feed.get("results", []) if isinstance(feed, dict) else []
        chart_items = [item for item in chart_results if isinstance(item, dict) and item.get("id")]
        if not chart_items:
            return []
        ids = ",".join(str(item["id"]) for item in chart_items[:limit])
        lookup_url = f"{APPLE_LOOKUP_ENDPOINT}?{parse.urlencode({'id': ids, 'country': country, 'entity': 'ebook'})}"
        lookup_payload = self._fetch_apple_json(lookup_url, "Apple Books lookup")
        ranks = {str(item["id"]): index for index, item in enumerate(chart_items, start=1)}
        candidates = _apple_book_candidates(lookup_payload, limit, ranks=ranks)
        candidates.sort(key=lambda item: (_book_price_sort_key(item), item.get("rank", limit + 1)))
        return candidates[:limit]

    def _fetch_apple_json(self, url: str, label: str) -> dict:
        search_request = request.Request(
            url,
            headers={"User-Agent": SEARCH_USER_AGENT, "Accept-Encoding": "identity"},
            method="GET",
        )
        try:
            with request.urlopen(search_request, timeout=self.timeout_seconds) as response:
                headers = dict(response.headers.items())
                raw_body = response.read(self.max_response_chars * 8)
        except error.HTTPError as exc:
            raise ValueError(f"{label} returned HTTP {exc.code}.") from exc
        except error.URLError as exc:
            raise ValueError(f"{label} is temporarily unavailable.") from exc
        body, _ = _decode_body(raw_body, self.max_response_chars * 4, headers)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{label} returned an invalid payload.")
        return payload

    def _validate_url(self, url: str) -> parse.ParseResult:
        stripped = url.strip()
        if not stripped:
            raise ValueError("URL must not be empty.")

        parsed = parse.urlparse(stripped)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http and https URLs are allowed.")
        if not parsed.hostname:
            raise ValueError("URL must include a hostname.")

        if not self.allow_private_networks:
            _reject_private_hostname(parsed.hostname)
        return parsed

    def _validate_headers(self, headers: dict) -> dict[str, str]:
        safe_headers: dict[str, str] = {}
        for key, value in headers.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            if normalized_key.lower() in BLOCKED_HEADERS:
                raise ValueError(f"Header is not allowed: {normalized_key}")
            safe_headers[normalized_key] = str(value)
        safe_headers.setdefault("User-Agent", "Avelin-Agent/0.1")
        return safe_headers


def _reject_private_hostname(hostname: str) -> None:
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname: {hostname}") from exc

    for address in addresses:
        ip_raw = address[4][0]
        ip = ipaddress.ip_address(ip_raw)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ValueError("Private, local, link-local, multicast and unspecified addresses are blocked.")


def _decode_body(
    raw_body: bytes,
    max_chars: int,
    headers: dict[str, str] | None = None,
) -> tuple[str, bool]:
    normalized_headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    content_encoding = normalized_headers.get("content-encoding", "").lower()
    try:
        if "gzip" in content_encoding or raw_body.startswith(b"\x1f\x8b"):
            raw_body = gzip.decompress(raw_body)
        elif "deflate" in content_encoding:
            raw_body = zlib.decompress(raw_body)
    except (OSError, zlib.error):
        return "<response body could not be decompressed>", False

    charset = "utf-8"
    content_type = normalized_headers.get("content-type", "")
    for part in content_type.split(";")[1:]:
        key, separator, value = part.strip().partition("=")
        if separator and key.lower() == "charset" and value.strip():
            charset = value.strip().strip('"\'')
            break
    try:
        text = raw_body.decode(charset, errors="replace")
    except LookupError:
        text = raw_body.decode("utf-8", errors="replace")
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return text, truncated


def _format_response(
    url: str,
    method: str,
    status: int,
    headers: dict[str, str],
    body: str,
    truncated: bool,
) -> str:
    content_type = headers.get("Content-Type") or headers.get("content-type") or ""
    lines = [
        f"URL: {url}",
        f"Method: {method}",
        f"Status: {status}",
        f"Content-Type: {content_type}",
    ]
    if body:
        lines.extend(["Body:", body + ("\n[truncated]" if truncated else "")])
    else:
        lines.append("Body: <empty>")
    return "\n".join(lines)


class _DuckDuckGoLiteParser(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.results: list[dict[str, str]] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._pending_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag == "a" and "result-link" in classes and len(self.results) < self.limit:
            self._capture = "title"
            self._buffer = []
            self._pending_url = _unwrap_search_url(attributes.get("href", ""))
        elif tag == "td" and "result-snippet" in classes and self.results:
            self._capture = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture == "title":
            title = " ".join("".join(self._buffer).split())
            if title and self._pending_url:
                self.results.append({"title": title, "url": self._pending_url, "snippet": ""})
            self._capture = None
            self._buffer = []
        elif tag == "td" and self._capture == "snippet":
            snippet = " ".join("".join(self._buffer).split())
            if self.results:
                self.results[-1]["snippet"] = snippet
            self._capture = None
            self._buffer = []


def _unwrap_search_url(url: str) -> str:
    normalized = url.strip()
    if normalized.startswith("//"):
        normalized = f"https:{normalized}"
    parsed = parse.urlparse(normalized)
    if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
        target = parse.parse_qs(parsed.query).get("uddg", [])
        if target:
            normalized = target[0]
    target_url = parse.urlparse(normalized)
    if target_url.scheme not in {"http", "https"} or not target_url.hostname:
        return ""
    return normalized


def _parse_search_results(body: str, limit: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        root = None
    if root is not None:
        for item in root.findall(".//item")[:limit]:
            title = " ".join((item.findtext("title") or "").split())
            url = (item.findtext("link") or "").strip()
            snippet = _strip_html(item.findtext("description") or "")
            parsed_url = parse.urlparse(url)
            if title and parsed_url.scheme in {"http", "https"} and parsed_url.hostname:
                results.append({"title": title, "url": url, "snippet": snippet})
        if results:
            return results

    parser = _DuckDuckGoLiteParser(limit)
    parser.feed(body)
    return parser.results[:limit]


def _provider_query(query: str) -> str:
    lowered = query.lower()
    if "apple books" in lowered and "site:" not in lowered:
        return f"{query} site:books.apple.com"
    return query


def _normalize_storefront(country: str | None, query: str) -> str:
    explicit = (country or "").strip().lower()
    if explicit:
        if not re.fullmatch(r"[a-z]{2}", explicit):
            raise ValueError("Apple Books country/storefront must be a two-letter code.")
        return explicit
    lowered = query.lower()
    if any(marker in lowered for marker in ("росси", "russia", "russian storefront")):
        return "ru"
    return "us"


def _rank_search_results(
    query: str,
    candidates: list[dict[str, str]],
    limit: int,
) -> tuple[list[dict[str, str]], Counter[str]]:
    query_terms = _meaningful_terms(query)
    apple_books_intent = "apple books" in query.lower()
    price_requested = bool(re.search(r"недорог|дешев|цен[аыу]|cheap|budget|price", query.lower()))
    ranked: list[tuple[int, int, dict[str, str]]] = []
    rejected: Counter[str] = Counter()
    seen_urls: set[str] = set()

    for position, item in enumerate(candidates):
        normalized_url = item["url"].strip()
        if normalized_url in seen_urls:
            rejected["duplicate"] += 1
            continue
        seen_urls.add(normalized_url)
        parsed_url = parse.urlparse(normalized_url)
        hostname = (parsed_url.hostname or "").lower().removeprefix("www.")
        path_parts = {part.lower() for part in parsed_url.path.split("/") if part}
        searchable = " ".join((item["title"], item["snippet"], hostname, parsed_url.path)).lower()
        result_terms = _meaningful_terms(searchable)
        overlap = query_terms & result_terms
        preferred_apple_source = hostname == "books.apple.com" or hostname.endswith(".books.apple.com")
        apple_evidence = "apple books" in searchable or preferred_apple_source
        book_evidence = bool({"book", "books", "ebook", "книга", "книги", "книг"} & result_terms)

        if apple_books_intent and not (preferred_apple_source or (apple_evidence and book_evidence)):
            rejected["wrong_catalog"] += 1
            continue
        if price_requested and not _has_price_evidence(item):
            rejected["price_not_verified"] += 1
            continue
        if path_parts & JUNK_PATH_PARTS and not preferred_apple_source:
            rejected["login_or_support"] += 1
            continue
        if not overlap and not preferred_apple_source:
            rejected["irrelevant"] += 1
            continue
        if parsed_url.path in {"", "/"} and len(overlap) < 2 and not preferred_apple_source:
            rejected["generic_homepage"] += 1
            continue

        score = len(overlap) * 3
        if preferred_apple_source:
            score += 12
        if "apple books" in searchable:
            score += 5
        if book_evidence:
            score += 2
        ranked.append((score, -position, item))

    ranked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    accepted: list[dict[str, str]] = []
    per_domain: Counter[str] = Counter()
    for _, _, item in ranked:
        hostname = (parse.urlparse(item["url"]).hostname or "").lower().removeprefix("www.")
        if not apple_books_intent and per_domain[hostname] >= 3:
            rejected["domain_duplicate"] += 1
            continue
        accepted.append(item)
        per_domain[hostname] += 1
        if len(accepted) >= limit:
            break
    return accepted, rejected


def _meaningful_terms(value: str) -> set[str]:
    terms = {
        token.lower()
        for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", value)
        if len(token) >= 2
    }
    return {term for term in terms if term not in SEARCH_STOP_WORDS and not term.isdigit()}


def _apple_books_term(query: str) -> str:
    without_catalog = re.sub(r"apple\s+books", " ", query, flags=re.IGNORECASE)
    terms = [term for term in _meaningful_terms(without_catalog) if term not in {"book", "books", "ebook", "книга", "книги", "книг"}]
    return " ".join(sorted(terms))


def _apple_book_candidates(
    payload: dict,
    limit: int,
    ranks: dict[str, int] | None = None,
) -> list[dict[str, str]]:
    raw_results = payload.get("results", []) if isinstance(payload, dict) else []
    candidates: list[dict[str, str]] = []
    for item in raw_results:
        if not isinstance(item, dict) or str(item.get("kind", "")).lower() not in {"book", "ebook"}:
            continue
        title = str(item.get("trackName") or item.get("collectionName") or "").strip()
        url = str(item.get("trackViewUrl") or item.get("collectionViewUrl") or "").strip()
        parsed_url = parse.urlparse(url)
        if not title or parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            continue
        author = str(item.get("artistName") or "").strip()
        price = str(item.get("formattedPrice") or "").strip()
        genres = item.get("genres") or []
        genre_text = ", ".join(str(value) for value in genres[:3]) if isinstance(genres, list) else ""
        item_id = str(item.get("trackId") or item.get("collectionId") or "")
        rank = (ranks or {}).get(item_id)
        evidence = [
            value
            for value in (
                "Каталог: Apple Books",
                f"Рейтинг Top Paid: #{rank}" if rank else "",
                f"Автор: {author}" if author else "",
                f"Цена: {price}" if price else "",
                f"Жанры: {genre_text}" if genre_text else "",
            )
            if value
        ]
        candidates.append(
            {
                "title": title,
                "url": url,
                "snippet": ". ".join(evidence) or "Карточка книги в Apple Books.",
                "price": item.get("price"),
                "rank": rank,
            }
        )
    if not ranks:
        candidates.sort(key=_book_price_sort_key)
    return candidates[:limit]


def _has_price_evidence(item: dict[str, str]) -> bool:
    price = item.get("price")
    if isinstance(price, (int, float)):
        return price >= 0
    text = item.get("snippet", "")
    return bool(re.search(r"(?:цена|price)\s*:\s*[^.;]+|[$€£₽]\s*\d|\d[\d\s.,]*\s*(?:руб|₽|usd|eur|€|\$)", text, re.IGNORECASE))


def _book_price_sort_key(item: dict[str, str]) -> tuple[float, str]:
    price = item.get("price")
    numeric = float(price) if isinstance(price, (int, float)) else float("inf")
    return numeric, item.get("title", "").lower()


def _strip_html(value: str) -> str:
    parser = _PlainTextParser()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
