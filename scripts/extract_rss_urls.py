#!/usr/bin/env python3
"""Extrait des URLs de flux RSS depuis une page source et les exporte en TXT.

Le script :
1) telecharge une page source HTML,
2) repere les liens contenus dans les `div` de classe `boite`,
3) garde les URLs qui commencent par une base (ex: https://feeds.bbci.co.uk),
4) conserve uniquement les URLs qui ressemblent a des flux RSS/Atom,
5) ecrit un fichier texte avec `titre<TAB>url` (une ligne par flux).
"""

from __future__ import annotations

import argparse
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import sys
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_SOURCE_URL = "https://atlasflux.saynete.net/atlas_des_flux_rss_ang_dedicated_bbc.htm"
DEFAULT_BASE_URL = "https://feeds.bbci.co.uk"
DEFAULT_OUTPUT_FILE = Path(__file__).resolve().parents[1] / "bbc_feed_urls.txt"
DEFAULT_TIMEOUT = 30.0
DEFAULT_KEYWORDS = (
    "feed",
    "feeds",
    "rss",
    "xml",
    "atom",
    "rdf",
    "syndication",
)
TRAILING_CHARS = ".,;:!?)]}>\"'"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recupere les URLs d'une page source, filtre celles qui commencent par "
            "une URL de base, puis conserve uniquement les URLs ressemblant a des flux RSS."
        )
    )
    parser.add_argument(
        "--source-url",
        default=DEFAULT_SOURCE_URL,
        help=f"URL source a parser (defaut: {DEFAULT_SOURCE_URL})",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Prefixe d'URL a conserver (defaut: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--output-file",
        default=str(DEFAULT_OUTPUT_FILE),
        help=f"Fichier texte de sortie (defaut: {DEFAULT_OUTPUT_FILE})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout HTTP en secondes (defaut: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=list(DEFAULT_KEYWORDS),
        help=(
            "Mots-clés utilises pour detecter les URLs de flux. "
            f"Defaut: {' '.join(DEFAULT_KEYWORDS)}"
        ),
    )
    return parser.parse_args()


def fetch_text(url: str, timeout: float) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) CodexRSSExtractor/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    request = Request(url=url, headers=headers)

    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset()

    if charset:
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass

    for fallback in ("utf-8", "iso-8859-1", "latin-1"):
        try:
            return raw.decode(fallback)
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace")


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ValueError("base_url vide")
    return normalized


def clean_url(raw_url: str) -> str | None:
    url = unescape(raw_url).strip()
    while url and url[-1] in TRAILING_CHARS:
        url = url[:-1]

    if not url:
        return None

    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return url


def looks_like_rss_url(url: str, keywords: Iterable[str]) -> bool:
    lower_url = url.lower()
    clean_keywords = [keyword.strip().lower() for keyword in keywords if keyword.strip()]

    if any(keyword in lower_url for keyword in clean_keywords):
        return True

    parsed = urlsplit(lower_url)
    if parsed.path.endswith((".xml", ".rss", ".atom", ".rdf")):
        return True

    query_hints = ("format=rss", "type=rss", "output=rss", "feed=rss", "rss=1")
    if any(hint in parsed.query for hint in query_hints):
        return True

    return False


def normalize_title(raw_title: str | None) -> str:
    if raw_title is None:
        return "Sans titre"
    normalized = " ".join(unescape(raw_title).split())
    return normalized if normalized else "Sans titre"


def _has_boite_class(class_value: str | None) -> bool:
    if not class_value:
        return False
    classes = [token.strip().lower() for token in class_value.split() if token.strip()]
    return "boite" in classes


class BoiteLinkParser(HTMLParser):
    """Parse les liens RSS situes dans des div.boite et conserve leur titre."""

    def __init__(self, *, base_url: str, keywords: Iterable[str]) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url_lower = base_url.lower()
        self._keywords = tuple(keywords)
        self._div_stack: list[str | None] = []
        self._seen_urls: set[str] = set()

        self.prefixed_in_boite_total = 0
        self.results: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower_tag = tag.lower()
        attr_map = {key.lower(): value for key, value in attrs}

        if lower_tag == "div":
            if _has_boite_class(attr_map.get("class")):
                self._div_stack.append(normalize_title(attr_map.get("title")))
            else:
                self._div_stack.append(None)
            return

        if lower_tag != "a":
            return

        boite_title = self._current_boite_title()
        if boite_title is None:
            return

        href = attr_map.get("href")
        if href is None:
            return

        cleaned_url = clean_url(href)
        if cleaned_url is None:
            return
        if not cleaned_url.lower().startswith(self._base_url_lower):
            return

        self.prefixed_in_boite_total += 1

        if not looks_like_rss_url(cleaned_url, self._keywords):
            return
        if cleaned_url in self._seen_urls:
            return

        self._seen_urls.add(cleaned_url)
        self.results.append((boite_title, cleaned_url))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "div" and self._div_stack:
            self._div_stack.pop()

    def _current_boite_title(self) -> str | None:
        for title in reversed(self._div_stack):
            if title is not None:
                return title
        return None


def extract_boite_rss_links(
    html: str, *, base_url: str, keywords: Iterable[str]
) -> tuple[int, list[tuple[str, str]]]:
    parser = BoiteLinkParser(base_url=base_url, keywords=keywords)
    parser.feed(html)
    parser.close()
    return parser.prefixed_in_boite_total, parser.results


def write_urls(output_file: str, rows: list[tuple[str, str]]) -> Path:
    destination = Path(output_file).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{title}\t{url}" for title, url in rows]
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return destination


def main() -> int:
    args = parse_args()

    try:
        base_url = normalize_base_url(args.base_url)
    except ValueError as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 2

    try:
        html = fetch_text(args.source_url, args.timeout)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        print(f"Erreur de recuperation de la source: {exc}", file=sys.stderr)
        return 1

    prefixed_count, rss_links_with_titles = extract_boite_rss_links(
        html,
        base_url=base_url,
        keywords=args.keywords,
    )
    output_path = write_urls(args.output_file, rss_links_with_titles)

    print(f"Source URL        : {args.source_url}")
    print(f"Base URL          : {base_url}")
    print(f"URLs prefixees    : {prefixed_count}")
    print(f"URLs RSS retenues : {len(rss_links_with_titles)}")
    print(f"Sortie            : {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
