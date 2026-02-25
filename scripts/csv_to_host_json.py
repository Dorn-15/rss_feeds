#!/usr/bin/env python3
"""Parse all CSV feed catalogs and generate one JSON file per host."""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT_DIR / "csv"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "json_test"
FILENAME_PATTERN = "*.csv"
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")

LOCALE_HINTS = {
    "fra": ("fr", "fr"),
    "ang": ("uk", "en"),
    "ita": ("it", "it"),
    "sui": ("ch", "fr"),
}

COMMON_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "edu"}


@dataclass
class HostBucket:
    feeds: list[dict] = field(default_factory=list)
    seen_urls: set[str] = field(default_factory=set)
    company_votes: Counter[str] = field(default_factory=Counter)
    locale_votes: Counter[tuple[str, str]] = field(default_factory=Counter)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse CSV files from atlasflux and create one JSON file per host "
            "with company, host, img, country, language, fetchprotection and feeds."
        )
    )
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_INPUT_DIR),
        help=f"Directory that contains CSV files (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory where host JSON files are written (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--pattern",
        default=FILENAME_PATTERN,
        help=f"Glob pattern for CSV files (default: {FILENAME_PATTERN})",
    )
    parser.add_argument(
        "--clean-output",
        action="store_true",
        help="Delete existing JSON files in output dir before writing new files.",
    )
    return parser.parse_args()


def detect_locale_from_filename(csv_path: Path) -> tuple[str, str]:
    match = re.search(r"_rss_([a-z]{3})_", csv_path.stem.lower())
    if not match:
        return ("xx", "xx")
    return LOCALE_HINTS.get(match.group(1), ("xx", "xx"))


def decode_text(path: Path) -> str:
    raw = path.read_bytes()
    last_error: UnicodeDecodeError | None = None
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is None:
        raise UnicodeDecodeError("utf-8", b"", 0, 1, f"Could not decode {path}")
    raise last_error


def parse_csv_file(path: Path) -> list[list[str]]:
    text = decode_text(path)
    rows: list[list[str]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("<!--"):
            continue

        parsed = next(csv.reader([line], delimiter=";", quotechar='"'))
        while parsed and not parsed[-1].strip():
            parsed.pop()
        if len(parsed) < 2:
            continue
        rows.append(parsed)

    return rows


def extract_host(raw_url: str) -> str | None:
    url = raw_url.strip()
    if not url:
        return None

    parsed = urlsplit(url)
    if not parsed.netloc and parsed.path:
        parsed = urlsplit(f"https://{url}")

    host = parsed.netloc.strip().lower()
    if not host:
        return None
    if "@" in host:
        host = host.split("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    return host or None


def normalize_text(value: str) -> str:
    return " ".join(unescape(value).split()).strip()


def guess_company_from_title(title: str) -> str | None:
    clean = normalize_text(title)
    if not clean:
        return None
    if ":" in clean:
        clean = clean.split(":", 1)[0].strip()

    # Remove trivial wrappers without touching regular names.
    clean = re.sub(r"\([^)]*\)", "", clean).strip(" -_,.")
    return clean or None


def company_from_host(host: str) -> str:
    host_no_www = host[4:] if host.startswith("www.") else host
    labels = host_no_www.split(".")
    if len(labels) >= 3 and labels[-2] in COMMON_SECOND_LEVEL:
        base = labels[-3]
    elif len(labels) >= 2:
        base = labels[-2]
    else:
        base = labels[0]

    words = re.split(r"[-_]+", base)
    return " ".join(word.capitalize() for word in words if word) or "Unknown"


def choose_company(bucket: HostBucket, host: str) -> str:
    fallback = company_from_host(host)
    if not bucket.company_votes:
        return fallback

    best_name, best_count = bucket.company_votes.most_common(1)[0]
    total_votes = sum(bucket.company_votes.values())

    if best_count >= 2 and (best_count / total_votes) >= 0.35:
        return best_name
    if len(bucket.company_votes) == 1 and len(best_name) <= 32:
        return best_name
    return fallback


def choose_locale(bucket: HostBucket) -> tuple[str, str]:
    if not bucket.locale_votes:
        return ("xx", "xx")
    return bucket.locale_votes.most_common(1)[0][0]


def slugify_company(company: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", company).encode("ascii", "ignore").decode("ascii")
    )
    ascii_text = re.sub(r"[`'’]", " ", ascii_text)
    tokens = re.findall(r"[A-Za-z0-9]+", ascii_text)
    if not tokens:
        return "source"
    return "_".join(tokens)


def safe_filename(host: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", host).strip("._")
    return safe or "unknown_host"


def unique_non_empty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = normalize_text(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def build_host_buckets(csv_files: list[Path]) -> dict[str, HostBucket]:
    host_buckets: dict[str, HostBucket] = {}

    for csv_file in csv_files:
        country, language = detect_locale_from_filename(csv_file)
        rows = parse_csv_file(csv_file)

        for row in rows:
            title = normalize_text(row[0])
            url = normalize_text(row[1])
            host = extract_host(url)
            if not host:
                continue

            bucket = host_buckets.setdefault(host, HostBucket())
            if url in bucket.seen_urls:
                continue

            tags = unique_non_empty(row[2:]) if len(row) > 2 else []

            feed_entry = {"url": url, "title": title}
            if tags:
                feed_entry["tags"] = tags

            bucket.feeds.append(feed_entry)
            bucket.seen_urls.add(url)

            company_candidate = guess_company_from_title(title)
            if company_candidate:
                bucket.company_votes[company_candidate] += 1
            bucket.locale_votes[(country, language)] += 1

    return host_buckets


def write_host_json_files(host_buckets: dict[str, HostBucket], output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0

    for host in sorted(host_buckets):
        bucket = host_buckets[host]
        company = choose_company(bucket, host)
        country, language = choose_locale(bucket)
        img = f"{slugify_company(company)}.svg"

        payload = {
            "company": company,
            "host": host,
            "img": img,
            "country": country,
            "language": language,
            "fetchprotection": 1,
            "feeds": bucket.feeds,
        }

        output_path = output_dir / f"{safe_filename(host)}.json"
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written += 1

    return written


def clean_output_dir(output_dir: Path) -> int:
    removed = 0
    for json_file in output_dir.glob("*.json"):
        json_file.unlink()
        removed += 1
    return removed


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not input_dir.exists():
        raise SystemExit(f"Input directory not found: {input_dir}")
    if not input_dir.is_dir():
        raise SystemExit(f"Input path is not a directory: {input_dir}")

    csv_files = sorted(input_dir.glob(args.pattern))
    if not csv_files:
        raise SystemExit(f"No CSV files found in {input_dir} with pattern '{args.pattern}'")

    if args.clean_output and output_dir.exists():
        removed = clean_output_dir(output_dir)
        print(f"Removed {removed} existing JSON file(s) from {output_dir}")

    host_buckets = build_host_buckets(csv_files)
    written = write_host_json_files(host_buckets, output_dir)

    print(f"Parsed {len(csv_files)} CSV file(s)")
    print(f"Generated {written} host JSON file(s) in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
