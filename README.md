# rss_feeds

Curated RSS feed catalog with one JSON file per source, plus helper scripts to normalize files and detect fetch accessibility.

## Repository structure
- `json/`: source files (`<Source>.json`)
- `img/`: SVG logos referenced by `img`
- `scripts/detect_fetchprotection.py`: compute and write `fetchprotection`
- `scripts/reorder_company_and_feeds.py`: reorder root keys (`company` first, `feeds` last)
- `fetchprotection_report.json`: optional generated report
- `README.md`, `LICENSE`

## JSON schema (current)
Each file is a root object:
- `company`: source display name
- `img`: logo filename (stored in `img/`)
- `country`: country code (`fr`, `uk`, `eu`, ...)
- `language`: language code (`fr`, `en`, ...)
- `fetchprotection`: global accessibility level for that file
- `feeds`: list of feed entries

Each entry in `feeds` contains:
- `url`: feed URL
- `title`: feed title
- `tags`: list of tags

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

## fetchprotection levels
- `0`: blocked (no method returned valid XML)
- `1`: `httpx_basic`
- `2`: `httpx_rss_headers`
- `3`: `httpx_browser_referer`

`fetchprotection` is computed per file as the maximum successful level among all URLs in `feeds`.

## Scripts

### `detect_fetchprotection.py`
Checks feed URLs and updates `fetchprotection` in each JSON file.

Behavior:
- Tests methods progressively from level `1` to `3`
- Tries URL candidates for known edge cases (BBC legacy feeds, RTVE variants)
- If an HTTPS attempt fails with a certificate-related error, retries HTTP at the same level
- Can emit a detailed JSON report with all attempts

Main options:
- `--input-dir`
- `--pattern`
- `--include-test-files`
- `--timeout`
- `--concurrency`
- `--max-urls-per-file`
- `--dry-run`
- `--report-file`

Example:
```bash
python3 scripts/detect_fetchprotection.py --input-dir json --report-file fetchprotection_report.json
```

### `reorder_company_and_feeds.py`
Reorders root keys for consistency:
- `company` first (if present)
- `feeds` last (if present)
- keeps all other keys in their existing relative order

Main options:
- `--input-dir`
- `--pattern`
- `--include-test-files`
- `--dry-run`

Example:
```bash
python3 scripts/reorder_company_and_feeds.py --input-dir json
```

## Typical maintenance flow
1. Update or add feeds in `json/<Source>.json`
2. Run fetch detection:
```bash
python3 scripts/detect_fetchprotection.py --input-dir json --report-file fetchprotection_report.json
```
3. Normalize key order:
```bash
python3 scripts/reorder_company_and_feeds.py --input-dir json
```

## Notes
- Scripts default to the repository root as `--input-dir`; for source files, use `--input-dir json`.
- `detect_fetchprotection.py` supports legacy list-based JSON and rewrites it into the current object structure.

## License
MIT. See `LICENSE`.
