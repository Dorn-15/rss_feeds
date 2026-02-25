#!/usr/bin/env python3
"""Detect fetch protection level for RSS feeds and write it to JSON files.

This script tests feed URLs with progressively stronger fetch strategies and
writes a global `fetchprotection` field in each JSON file.

Level map:
- 0: blocked (none of the methods returned XML successfully)
- 1: `httpx` basic request
- 2: `httpx` with RSS/browser-like headers (from get_httpx_networking_cli.py)
- 3: `httpx` with stronger browser headers + referer
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover - graceful fallback when dependency is missing
    httpx = None  # type: ignore

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_CONCURRENT_RSS_REQUESTS = 30
DEFAULT_COMPANY_REQUESTS_PER_SECOND = 2.0
DEFAULT_CONCURRENCY = DEFAULT_MAX_CONCURRENT_RSS_REQUESTS
if httpx is not None:
    DEFAULT_HTTPX_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20)
else:
    DEFAULT_HTTPX_LIMITS = None

DEFAULT_RSS_CHECK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
    "Accept": "text/html, application/rss+xml, application/xml, application/atom+xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}

METHOD_LABELS = {
    0: "blocked",
    1: "httpx_basic",
    2: "httpx_rss_headers",
    3: "httpx_browser_referer",
}

XML_MARKERS = ("<?xml", "<rss", "<feed", "<rdf:rdf", "<atom:")
XML_CONTENT_TYPE_MARKERS = ("xml", "rss", "atom")
TLS_CERT_REASON_MARKERS = (
    "certificate verify failed",
    "no alternative certificate",
    "certificate",
    "certificat",
)


@dataclass
class AttemptResult:
    level: int
    method: str
    ok: bool
    status_code: int | None
    content_type: str | None
    elapsed_ms: int
    reason: str


@dataclass
class UrlCheckResult:
    url: str
    resolved_url: str
    level: int
    method: str
    attempts: list[AttemptResult]


class JsonFeedRequestLimiter:
    """Double semaphore limiter aligned with Manifeed backend behavior."""

    def __init__(
        self,
        *,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_RSS_REQUESTS,
        company_requests_per_second: float = DEFAULT_COMPANY_REQUESTS_PER_SECOND,
    ) -> None:
        if company_requests_per_second <= 0:
            raise ValueError("company_requests_per_second must be greater than 0")

        self._global_semaphore = asyncio.Semaphore(max(1, int(max_concurrent)))
        self._company_min_interval_seconds = 1.0 / company_requests_per_second
        self._company_semaphores: dict[str, asyncio.Semaphore] = {}
        self._company_next_allowed_at: dict[str, float] = {}

    @asynccontextmanager
    async def acquire(self, company_key: str):
        resolved_company_key = _normalize_rate_limit_key(company_key)
        company_semaphore = self._company_semaphores.get(resolved_company_key)
        if company_semaphore is None:
            company_semaphore = asyncio.Semaphore(1)
            self._company_semaphores[resolved_company_key] = company_semaphore

        async with company_semaphore:
            await self._wait_company_rate_limit(resolved_company_key)
            async with self._global_semaphore:
                yield

    async def _wait_company_rate_limit(self, company_key: str) -> None:
        now = time.monotonic()
        next_allowed_at = self._company_next_allowed_at.get(company_key, 0.0)
        wait_seconds = next_allowed_at - now
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
            now = time.monotonic()

        self._company_next_allowed_at[company_key] = now + self._company_min_interval_seconds


def _normalize_rate_limit_key(key: str) -> str:
    if not key.strip():
        return "json:unknown"
    return key.strip().lower()


def _resolve_file_rate_limit_key(file_path: Path) -> str:
    return f"json:{file_path.stem}"


def _load_httpx_defaults_from_manifeed() -> tuple[Any, dict[str, str]]:
    """Best-effort import from Manifeed get_httpx_networking_cli.py."""
    if httpx is None:
        return DEFAULT_HTTPX_LIMITS, DEFAULT_RSS_CHECK_HEADERS

    project_root = Path(__file__).resolve().parents[2]
    backend_path = project_root / "Manifeed" / "backend"
    if not backend_path.exists():
        return DEFAULT_HTTPX_LIMITS, DEFAULT_RSS_CHECK_HEADERS

    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))

    try:
        from app.clients.networking.get_httpx_networking_cli import (  # type: ignore
            DEFAULT_HTTPX_LIMITS as imported_limits,
            DEFAULT_RSS_CHECK_HEADERS as imported_headers,
        )

        return imported_limits, dict(imported_headers)
    except Exception:
        return DEFAULT_HTTPX_LIMITS, DEFAULT_RSS_CHECK_HEADERS


def _company_from_filename(file_path: Path) -> str:
    return file_path.stem.replace("_", " ").strip()


def _first_non_empty(entries: list[dict[str, Any]], field: str) -> str | None:
    for entry in entries:
        value = entry.get(field)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        tags: list[str] = []
        for item in value:
            if isinstance(item, str):
                normalized = item.strip()
                if normalized:
                    tags.append(normalized)
        return tags
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    return []


def _ensure_structured_payload(file_path: Path, payload: Any) -> dict[str, Any]:
    """Accept both old list format and new dict-with-feeds format."""
    if isinstance(payload, dict) and isinstance(payload.get("feeds"), list):
        return payload

    if isinstance(payload, list):
        entries = [entry for entry in payload if isinstance(entry, dict)]
        return {
            "img": _first_non_empty(entries, "img"),
            "country": _first_non_empty(entries, "country"),
            "language": _first_non_empty(entries, "language") or _first_non_empty(entries, "country"),
            "company": _first_non_empty(entries, "company") or _company_from_filename(file_path),
            "feeds": [
                {
                    "url": entry.get("url"),
                    "title": entry.get("title"),
                    "tags": _normalize_tags(entry.get("tags")),
                }
                for entry in entries
            ],
        }

    raise ValueError("Unsupported JSON format: expected list or object with `feeds`")


def _extract_urls(payload: dict[str, Any]) -> list[str]:
    feeds = payload.get("feeds")
    if not isinstance(feeds, list):
        return []

    urls: list[str] = []
    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        url = feed.get("url")
        if isinstance(url, str):
            normalized = url.strip()
            if normalized:
                urls.append(normalized)
    # Preserve order, remove duplicates
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def _accept_language_from_code(language: str | None) -> str:
    if not language:
        return "en-US,en;q=0.9"
    code = language.strip().lower()[:2]
    if not code:
        return "en-US,en;q=0.9"
    if code == "en":
        return "en-US,en;q=0.9"
    return f"{code}-{code.upper()};q=0.9,{code};q=0.8,en;q=0.6"


def _build_origin(url: str) -> str | None:
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return None
        return f"{parts.scheme}://{parts.netloc}"
    except Exception:
        return None


def _append_query_param(url: str, key: str, value: str) -> str:
    try:
        parts = urlsplit(url)
        query_items = parse_qsl(parts.query, keep_blank_values=True)
        existing = {item_key for item_key, _ in query_items}
        if key not in existing:
            query_items.append((key, value))
        query = urlencode(query_items)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    except Exception:
        return url


def _replace_host(url: str, host: str) -> str:
    try:
        parts = urlsplit(url)
        if not parts.scheme:
            return url
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    except Exception:
        return url


def _replace_scheme(url: str, scheme: str) -> str:
    try:
        parts = urlsplit(url)
        if not parts.netloc:
            return url
        return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return url


def _is_https_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return parts.scheme.lower() == "https"
    except Exception:
        return False


def _is_tls_certificate_reason(reason: str | None) -> bool:
    normalized = (reason or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in TLS_CERT_REASON_MARKERS)


def _should_retry_http_same_level(url: str, attempt: AttemptResult) -> bool:
    return not attempt.ok and _is_https_url(url) and _is_tls_certificate_reason(attempt.reason)


def _build_bbc_candidate_urls(url: str) -> list[str]:
    candidates: list[str] = [url]
    try:
        parts = urlsplit(url)
    except Exception:
        return candidates

    host = parts.netloc.lower()
    if host != "newsrss.bbc.co.uk":
        return candidates

    # Legacy host has broken TLS cert. The same endpoint works via HTTP and redirects
    # to the modern feeds.bbci.co.uk HTTPS URLs.
    candidates.append(urlunsplit(("http", parts.netloc, parts.path, parts.query, parts.fragment)))

    match = re.match(r"^/rss/newsonline_uk_edition/([^/]+)/rss\.xml$", parts.path)
    if match:
        section = match.group(1).strip().lower()
        if section == "front_page":
            candidates.append("https://feeds.bbci.co.uk/news/rss.xml?edition=uk")
            candidates.append("https://feeds.bbci.co.uk/news/rss.xml")
        else:
            section_alias_map = {
                "science": "science_and_environment",
            }
            mapped_section = section_alias_map.get(section, section)
            candidates.append(f"https://feeds.bbci.co.uk/news/{mapped_section}/rss.xml")
            candidates.append(f"https://feeds.bbci.co.uk/news/{mapped_section}/rss.xml?edition=uk")

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _build_rtve_candidate_urls(url: str) -> list[str]:
    candidates: list[str] = [url]
    try:
        parts = urlsplit(url)
    except Exception:
        return candidates

    host = parts.netloc.lower()
    if host not in {"www.rtve.es", "api.rtve.es", "api2.rtve.es"}:
        return candidates
    if not parts.path.startswith("/rss/"):
        return candidates

    # Some RTVE edges return `{}` unless output=rss is explicitly requested.
    candidates.append(_append_query_param(url, "output", "rss"))

    for target_host in ("api2.rtve.es", "api.rtve.es", "www.rtve.es"):
        replaced_host = _replace_host(url, target_host)
        for target_scheme in ("http", "https"):
            replaced = _replace_scheme(replaced_host, target_scheme)
            candidates.append(replaced)
            candidates.append(_append_query_param(replaced, "output", "rss"))

    # Some RTVE edges intermittently cache `{}`. Add cache-busting variants.
    candidates_with_cache_busters: list[str] = []
    for candidate in candidates:
        candidates_with_cache_busters.append(candidate)
        candidates_with_cache_busters.append(_append_query_param(candidate, "__cb1", str(int(time.time()))))
        candidates_with_cache_busters.append(_append_query_param(candidate, "__cb2", str(int(time.time() * 1000))))

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates_with_cache_busters:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _build_url_candidates(url: str) -> list[str]:
    candidates = _build_bbc_candidate_urls(url)
    expanded: list[str] = []
    for candidate in candidates:
        expanded.extend(_build_rtve_candidate_urls(candidate))

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in expanded:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique or [url]


def _looks_like_xml(content_type: str | None, body: str | None) -> bool:
    ct = (content_type or "").lower()
    if any(marker in ct for marker in XML_CONTENT_TYPE_MARKERS):
        return True

    text = (body or "")[:3000].lower()
    return any(marker in text for marker in XML_MARKERS)


def _is_ok_response(status_code: int | None, content_type: str | None, body: str | None) -> tuple[bool, str]:
    if status_code != 200:
        return False, f"http_{status_code}"
    if not _looks_like_xml(content_type=content_type, body=body):
        return False, "not_xml_payload"
    return True, "ok"


async def _attempt_httpx(
    client: Any,
    url: str,
    level: int,
    method: str,
    headers: dict[str, str] | None = None,
) -> AttemptResult:
    started_at = time.perf_counter()
    try:
        response = await client.get(url, headers=headers)
        content_type = response.headers.get("content-type")
        ok, reason = _is_ok_response(
            status_code=response.status_code,
            content_type=content_type,
            body=response.text,
        )
        return AttemptResult(
            level=level,
            method=method,
            ok=ok,
            status_code=response.status_code,
            content_type=content_type,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            reason=reason,
        )
    except Exception as exception:
        exception_text = str(exception).strip().replace("\n", " ")
        reason = f"request_error:{exception.__class__.__name__}"
        if exception_text:
            reason = f"{reason}:{exception_text[:120]}"
        return AttemptResult(
            level=level,
            method=method,
            ok=False,
            status_code=None,
            content_type=None,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            reason=reason,
        )


def _build_browser_headers(language: str | None) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, text/html;q=0.8, */*;q=0.7",
        "Accept-Language": _accept_language_from_code(language),
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


async def _check_url_candidate(
    original_url: str,
    candidate_url: str,
    language: str | None,
    client: Any | None,
    rss_check_headers: dict[str, str],
) -> UrlCheckResult:
    attempts: list[AttemptResult] = []
    active_url = candidate_url
    origin = _build_origin(active_url)
    browser_headers = _build_browser_headers(language=language)
    if origin:
        browser_headers["Referer"] = f"{origin}/"
        browser_headers["Origin"] = origin

    async def run_with_optional_http_retry(
        attempt_builder: Callable[[str], Awaitable[AttemptResult]],
    ) -> AttemptResult:
        nonlocal active_url

        attempt = await attempt_builder(active_url)
        attempts.append(attempt)
        if attempt.ok:
            return attempt

        if _should_retry_http_same_level(active_url, attempt):
            fallback_url = _replace_scheme(active_url, "http")
            if fallback_url != active_url:
                active_url = fallback_url
                updated_origin = _build_origin(active_url)
                if updated_origin:
                    browser_headers["Referer"] = f"{updated_origin}/"
                    browser_headers["Origin"] = updated_origin
                else:
                    browser_headers.pop("Referer", None)
                    browser_headers.pop("Origin", None)
                retry_attempt = await attempt_builder(active_url)
                attempts.append(retry_attempt)
                return retry_attempt

        return attempt

    if client is None:
        attempts.extend(
            [
                AttemptResult(
                    level=1,
                    method=METHOD_LABELS[1],
                    ok=False,
                    status_code=None,
                    content_type=None,
                    elapsed_ms=0,
                    reason="httpx_not_installed",
                ),
                AttemptResult(
                    level=2,
                    method=METHOD_LABELS[2],
                    ok=False,
                    status_code=None,
                    content_type=None,
                    elapsed_ms=0,
                    reason="httpx_not_installed",
                ),
                AttemptResult(
                    level=3,
                    method=METHOD_LABELS[3],
                    ok=False,
                    status_code=None,
                    content_type=None,
                    elapsed_ms=0,
                    reason="httpx_not_installed",
                ),
            ]
        )
    else:
        attempt = await run_with_optional_http_retry(
            lambda target_url: _attempt_httpx(
                client=client,
                url=target_url,
                level=1,
                method=METHOD_LABELS[1],
            )
        )
        if attempt.ok:
            return UrlCheckResult(
                url=original_url,
                resolved_url=active_url,
                level=1,
                method=METHOD_LABELS[1],
                attempts=attempts,
            )

        attempt = await run_with_optional_http_retry(
            lambda target_url: _attempt_httpx(
                client=client,
                url=target_url,
                level=2,
                method=METHOD_LABELS[2],
                headers=rss_check_headers,
            )
        )
        if attempt.ok:
            return UrlCheckResult(
                url=original_url,
                resolved_url=active_url,
                level=2,
                method=METHOD_LABELS[2],
                attempts=attempts,
            )

        attempt = await run_with_optional_http_retry(
            lambda target_url: _attempt_httpx(
                client=client,
                url=target_url,
                level=3,
                method=METHOD_LABELS[3],
                headers=browser_headers,
            )
        )
        if attempt.ok:
            return UrlCheckResult(
                url=original_url,
                resolved_url=active_url,
                level=3,
                method=METHOD_LABELS[3],
                attempts=attempts,
            )

    return UrlCheckResult(
        url=original_url,
        resolved_url=active_url,
        level=0,
        method=METHOD_LABELS[0],
        attempts=attempts,
    )


async def _check_url(
    url: str,
    language: str | None,
    client: Any | None,
    rss_check_headers: dict[str, str],
) -> UrlCheckResult:
    candidate_urls = _build_url_candidates(url)

    combined_attempts: list[AttemptResult] = []
    last_result: UrlCheckResult | None = None
    for candidate_url in candidate_urls:
        result = await _check_url_candidate(
            original_url=url,
            candidate_url=candidate_url,
            language=language,
            client=client,
            rss_check_headers=rss_check_headers,
        )
        last_result = result
        combined_attempts.extend(result.attempts)
        if result.level > 0:
            result.attempts = combined_attempts
            return result

    if last_result is None:
        return UrlCheckResult(
            url=url,
            resolved_url=url,
            level=0,
            method=METHOD_LABELS[0],
            attempts=[],
        )

    last_result.attempts = combined_attempts
    return last_result


def _compute_global_fetchprotection(results: list[UrlCheckResult]) -> int:
    if not results:
        return 0

    successful_levels = [result.level for result in results if result.level > 0]
    if not successful_levels:
        return 0
    return max(successful_levels)


def _read_json(file_path: Path) -> Any:
    with file_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _write_json(file_path: Path, payload: dict[str, Any]) -> None:
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


async def _process_file(
    file_path: Path,
    timeout: float,
    concurrency: int,
    company_requests_per_second: float,
    max_urls_per_file: int | None,
    dry_run: bool,
    httpx_limits: Any,
    rss_check_headers: dict[str, str],
) -> dict[str, Any]:
    raw_payload = _read_json(file_path)
    payload = _ensure_structured_payload(file_path=file_path, payload=raw_payload)

    language = payload.get("language")
    if not isinstance(language, str):
        country = payload.get("country")
        language = country if isinstance(country, str) else None

    urls = _extract_urls(payload)
    if max_urls_per_file is not None:
        urls = urls[: max(0, max_urls_per_file)]

    url_results: list[UrlCheckResult] = []
    limiter = JsonFeedRequestLimiter(
        max_concurrent=max(1, concurrency),
        company_requests_per_second=company_requests_per_second,
    )
    company_key = _resolve_file_rate_limit_key(file_path)

    async def run_one(url: str, client: Any | None) -> UrlCheckResult:
        async with limiter.acquire(company_key):
            return await _check_url(
                url=url,
                language=language,
                client=client,
                rss_check_headers=rss_check_headers,
            )

    if urls and httpx is not None and httpx_limits is not None:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            limits=httpx_limits,
        ) as client:
            url_results = await asyncio.gather(*[run_one(url, client) for url in urls])
    elif urls:
        url_results = await asyncio.gather(*[run_one(url, None) for url in urls])

    fetchprotection = _compute_global_fetchprotection(url_results)
    payload["fetchprotection"] = fetchprotection

    if not dry_run:
        _write_json(file_path, payload)

    blocked_count = len([result for result in url_results if result.level == 0])
    max_level = max([result.level for result in url_results], default=0)
    return {
        "file": file_path.name,
        "fetchprotection": fetchprotection,
        "urls_total": len(urls),
        "urls_blocked": blocked_count,
        "max_url_level": max_level,
        "url_results": [
            {
                "url": result.url,
                "resolved_url": result.resolved_url,
                "level": result.level,
                "method": result.method,
                "attempts": [asdict(attempt) for attempt in result.attempts],
            }
            for result in url_results
        ],
    }


def _iter_target_files(
    input_dir: Path,
    pattern: str,
    include_test_files: bool,
) -> list[Path]:
    files = sorted(input_dir.glob(pattern))
    filtered: list[Path] = []
    for file_path in files:
        if file_path.suffix.lower() != ".json":
            continue
        if not include_test_files and file_path.stem.endswith("_test"):
            continue
        filtered.append(file_path)
    return filtered


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_input_dir = script_dir.parent

    parser = argparse.ArgumentParser(
        description="Detect feed fetch protection and write global fetchprotection in JSON files."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=default_input_dir,
        help=f"Directory containing feed JSON files (default: {default_input_dir})",
    )
    parser.add_argument(
        "--pattern",
        default="*.json",
        help="Glob pattern of target files (default: *.json)",
    )
    parser.add_argument(
        "--include-test-files",
        action="store_true",
        help="Also process files ending with _test.json.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Global max concurrent async feed checks (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--company-requests-per-second",
        type=float,
        default=DEFAULT_COMPANY_REQUESTS_PER_SECOND,
        help=(
            "Rate limit for feeds within a single JSON file "
            f"(default: {DEFAULT_COMPANY_REQUESTS_PER_SECOND})"
        ),
    )
    parser.add_argument(
        "--max-urls-per-file",
        type=int,
        default=None,
        help="Limit number of URLs tested per file (for quick runs).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write JSON files; only run checks and print/report results.",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=None,
        help="Write detailed report JSON to this path.",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    input_dir: Path = args.input_dir

    if args.company_requests_per_second <= 0:
        raise SystemExit("--company-requests-per-second must be greater than 0")

    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {input_dir}")

    files = _iter_target_files(
        input_dir=input_dir,
        pattern=args.pattern,
        include_test_files=args.include_test_files,
    )
    if not files:
        print("No JSON files matched.")
        return

    httpx_limits, rss_headers = _load_httpx_defaults_from_manifeed()
    started_at = time.perf_counter()
    file_reports: list[dict[str, Any]] = []
    for file_path in files:
        try:
            report = await _process_file(
                file_path=file_path,
                timeout=args.timeout,
                concurrency=args.concurrency,
                company_requests_per_second=args.company_requests_per_second,
                max_urls_per_file=args.max_urls_per_file,
                dry_run=args.dry_run,
                httpx_limits=httpx_limits,
                rss_check_headers=rss_headers,
            )
            file_reports.append(report)
            print(
                f"{report['file']}: fetchprotection={report['fetchprotection']} "
                f"(urls={report['urls_total']}, blocked={report['urls_blocked']})"
            )
        except Exception as exception:
            print(f"{file_path.name}: error={exception}")

    duration_ms = int((time.perf_counter() - started_at) * 1000)
    blocked_files = len([item for item in file_reports if item.get("fetchprotection") == 0])
    print(
        f"\nDone. Files processed: {len(file_reports)} | "
        f"Blocked files: {blocked_files} | Duration: {duration_ms}ms"
    )

    if args.report_file:
        report_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "method_map": METHOD_LABELS,
            "dry_run": bool(args.dry_run),
            "input_dir": str(input_dir),
            "files": file_reports,
        }
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        with args.report_file.open("w", encoding="utf-8") as file:
            json.dump(report_payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        print(f"Report written: {args.report_file}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
