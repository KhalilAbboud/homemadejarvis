"""Public-web search and page retrieval with SSRF protections."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

_TIMEOUT: Final = httpx.Timeout(15.0, connect=5.0)
_MAX_PAGE_BYTES: Final = 1_000_000
_MAX_REDIRECTS: Final = 3


class WebError(RuntimeError):
    """Raised when a web request is unsafe or unsuccessful."""


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    text: str


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class _TextExtractor(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_title:
            self.title = f"{self.title} {cleaned}".strip()
        self._parts.append(cleaned)

    @property
    def text(self) -> str:
        return " ".join(self._parts)


async def _validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebError("Only absolute http(s) URLs may be fetched.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise WebError("Local network addresses cannot be fetched.")
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise WebError(f"Could not resolve host '{hostname}'.") from e
    for _family, _type, _protocol, _canonname, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            raise WebError("Private, loopback, and reserved network addresses cannot be fetched.")
    return url


async def _download(url: str) -> tuple[str, str]:
    current = await _validate_public_url(url)
    headers = {"User-Agent": "OSIRIS/1.0 (personal assistant)"}
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False, headers=headers) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            response = await client.get(current)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise WebError("The server returned a redirect without a destination.")
                current = await _validate_public_url(urljoin(current, location))
                continue
            response.raise_for_status()
            if len(response.content) > _MAX_PAGE_BYTES:
                raise WebError("The page is too large to process safely.")
            return current, response.text
    raise WebError(f"Too many redirects while fetching '{url}'.")


async def fetch_web_page(url: str, max_characters: int = 12_000) -> WebPage:
    """Fetch a public web page and return its readable text, not raw HTML."""
    if not 1 <= max_characters <= 50_000:
        raise ValueError("max_characters must be between 1 and 50000")
    try:
        final_url, body = await _download(url)
    except httpx.HTTPError as e:
        raise WebError(f"Web request failed: {e}") from e
    extractor = _TextExtractor()
    extractor.feed(body)
    return WebPage(url=final_url, title=html.unescape(extractor.title), text=html.unescape(extractor.text)[:max_characters])


async def search_web(query: str, max_results: int = 5) -> list[SearchResult]:
    """Search the public web via DuckDuckGo's HTML endpoint."""
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not 1 <= max_results <= 10:
        raise ValueError("max_results must be between 1 and 10")
    _url, body = await _download(f"https://html.duckduckgo.com/html/?q={quote_plus(query.strip())}")

    # DuckDuckGo's HTML endpoint emits one result__a (title+link) followed by
    # one result__snippet per result, in the same order. They're extracted
    # separately (different tag structure) and paired positionally rather
    # than with one combined regex, which would be fragile against the
    # snippet's own nested <b>/<a> tags.
    raw_snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', body, flags=re.DOTALL)

    results: list[SearchResult] = []
    for index, (raw_link, raw_title) in enumerate(
        re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, flags=re.DOTALL)
    ):
        link = html.unescape(raw_link)
        if "uddg=" in link:
            link = unquote(parse_qs(urlparse(link).query).get("uddg", [link])[0])
        if urlparse(link).scheme not in {"http", "https"}:
            continue
        title = html.unescape(re.sub("<[^>]+>", "", raw_title)).strip()
        if not title:
            continue
        snippet = ""
        if index < len(raw_snippets):
            snippet = html.unescape(re.sub("<[^>]+>", "", raw_snippets[index])).strip()
        results.append(SearchResult(title=title, url=link, snippet=snippet))
        if len(results) >= max_results:
            break
    return results