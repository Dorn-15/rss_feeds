# rss_feeds

A curated catalog of RSS feeds with structured metadata (tags, language, trust score, enablement) and associated icons. Designed to power aggregation engines, dashboards, or news‑monitoring pipelines.

## Contents
- French and international sources.
- One JSON file per source.
- Each feed entry is enriched with actionable metadata.

Available sources (JSON files):
- `FrenchWeb.json`
- `Le_Monde.json`
- `Les_Echos.json`
- `The_Verge.json`
- `The_Wall_Street_Journal.json`
- `Wired.json`

## Repository structure
- `*.json` : feed catalogs by source
- `img/` : SVG icons organized by source
- `README.md`

## Entry schema
Each JSON file contains an array of objects with the following fields:
- `url` : RSS feed URL
- `title` : feed label
- `tags` : list of tags (classification)
- `trust_score` : confidence score between 0 and 1
- `language` : language code (`fr`, `en`, ...)
- `enabled` : feed activation flag (set to true when the XML URL is reachable with a simple httpx request in Python)
- `img` : relative path to the icon (inside `img/`)
- `parsing_config` (optional) : specific parsing config
  - `item_tag` (optional)
  - `custom_fields` (optional)

Example:
```json
{
  "url": "https://example.com/rss",
  "title": "Tech",
  "tags": ["tech", "innovation"],
  "trust_score": 0.95,
  "language": "fr",
  "enabled": true,
  "img": "source/source.svg",
  "parsing_config": {
    "item_tag": "item",
    "custom_fields": {}
  }
}
```

## Quick usage
Load a JSON file and filter feeds:
- Filter by `enabled: true`
- Filter by `language`
- Filter by `tags` based on your use case

## Add a feed
1. Open the JSON file for the target source.
2. Add a new entry following the schema.
3. Add an SVG icon in `img/<source>/` and set `img`.
4. Validate JSON formatting.

## Best practices
- Keep tags short and consistent.
- Align file names and icon paths.
- Set `trust_score` conservatively.

## License
MIT License. See `LICENSE`.
