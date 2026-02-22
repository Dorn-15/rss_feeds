# rss_feeds

Curated RSS feed catalog with one JSON file per source, plus helper scripts to normalize files and detect feed accessibility.

## Repository structure
- `json/`: source files (`<Source>.json`)
- `img/`: SVG logos referenced by `img`
- `scripts/detect_fetchprotection.py`: checks feed accessibility and writes `fetchprotection`
- `scripts/reorder_company_and_feeds.py`: reorders root keys (`company` first, `feeds` last)
- `fetchprotection_report.json`: optional generated report
- `README.md`, `LICENSE`

## Requirements
- Python 3.10+
- `httpx` for real network checks:
  ```bash
  python3 -m pip install httpx
  ```
- Optional: if `Manifeed/backend` exists in the same workspace, `detect_fetchprotection.py` reuses shared HTTPX defaults from `app/clients/networking/get_httpx_networking_cli.py`.

If `httpx` is not installed, the script still runs but every URL is marked blocked (`reason=httpx_not_installed`).

## JSON schema (current)
Each file should be a root object:
- `company` (string): source display name
- `img` (string, optional): logo filename in `img/`
- `country` (string, optional): country code (`fr`, `uk`, `eu`, ...)
- `language` (string, optional): language code (`fr`, `en`, ...); falls back to `country` during checks
- `fetchprotection` (int, computed): global accessibility level for that file
- `feeds` (array): list of feed entries

Each entry in `feeds`:
- `url` (string): feed URL
- `title` (string, optional): feed title
- `tags` (array of strings, optional): feed tags

Example:
```json
{
  "company": "Example Source",
  "img": "Example_Source.svg",
  "country": "fr",
  "language": "fr",
  "fetchprotection": 1,
  "feeds": [
    {
      "url": "https://example.com/rss.xml",
      "title": "Top stories",
      "tags": ["news"]
    }
  ]
}
```

Legacy list-based JSON is still accepted by `detect_fetchprotection.py` and rewritten into the object structure above.

## fetchprotection levels
- `0`: blocked (no method returned valid XML)
- `1`: `httpx_basic`
- `2`: `httpx_rss_headers`
- `3`: `httpx_browser_referer`

`fetchprotection` is computed per file as the maximum successful level among all URLs in `feeds`.

A URL attempt is considered successful only if:
- HTTP status is `200`
- response looks like XML (`content-type` contains `xml`/`rss`/`atom`, or body starts with XML/feed markers)

## Scripts

### `detect_fetchprotection.py`
Checks feed URLs and updates `fetchprotection` in each JSON file.

Behavior:
- tests methods progressively from level `1` to `3`
- expands URL candidates for known edge cases:
  - BBC legacy host (`newsrss.bbc.co.uk`) including modern feed alternatives
  - RTVE host/scheme variants, `output=rss`, and cache-busting query params
- if an HTTPS attempt fails with a certificate-related error, retries HTTP at the same level
- can emit a detailed JSON report with all attempts

Main options:
- `--input-dir`
- `--pattern`
- `--include-test-files`
- `--timeout`
- `--concurrency`
- `--max-urls-per-file`
- `--dry-run`
- `--report-file`

Examples:
```bash
# Full update on source files
python3 scripts/detect_fetchprotection.py --input-dir json --report-file fetchprotection_report.json

# Quick check of one file without writing changes
python3 scripts/detect_fetchprotection.py --input-dir json --pattern "BBC_News.json" --dry-run
```

### `reorder_company_and_feeds.py`
Reorders root keys for consistency:
- `company` first (if present)
- `feeds` last (if present)
- keeps all other root keys in their existing relative order

Main options:
- `--input-dir`
- `--pattern`
- `--include-test-files`
- `--dry-run`

Example:
```bash
python3 scripts/reorder_company_and_feeds.py --input-dir json
```

## Report format (`--report-file`)
Generated report shape:
```json
{
  "generated_at": "2026-02-20T14:22:06.176394+00:00",
  "method_map": { "0": "blocked", "1": "httpx_basic", "2": "httpx_rss_headers", "3": "httpx_browser_referer" },
  "dry_run": false,
  "input_dir": "json",
  "files": [
    {
      "file": "BBC_News.json",
      "fetchprotection": 1,
      "urls_total": 8,
      "urls_blocked": 0,
      "max_url_level": 1,
      "url_results": []
    }
  ]
}
```

`url_results[].attempts[]` includes per-attempt diagnostics (`status_code`, `content_type`, `elapsed_ms`, `reason`).

## Typical maintenance flow
1. Update or add feeds in `json/<Source>.json`.
2. Run fetch detection and generate a report:
   ```bash
   python3 scripts/detect_fetchprotection.py --input-dir json --report-file fetchprotection_report.json
   ```
3. Normalize key order:
   ```bash
   python3 scripts/reorder_company_and_feeds.py --input-dir json
   ```

## Notes
- Both scripts default `--input-dir` to repository root. For source files, use `--input-dir json`.
- Files ending with `_test.json` are ignored by default; pass `--include-test-files` to include them.

## License
MIT. See `LICENSE`.
