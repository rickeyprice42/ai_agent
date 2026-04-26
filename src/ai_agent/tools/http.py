from __future__ import annotations

from urllib import error, parse, request
import ipaddress
import socket


ALLOWED_METHODS = {"GET", "HEAD"}
BLOCKED_HEADERS = {"authorization", "cookie", "proxy-authorization"}


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

        body, truncated = _decode_body(raw_body, self.max_response_chars)
        return _format_response(
            url=parsed.geturl(),
            method=normalized_method,
            status=int(status),
            headers=response_headers,
            body=body,
            truncated=truncated,
        )

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


def _decode_body(raw_body: bytes, max_chars: int) -> tuple[str, bool]:
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
