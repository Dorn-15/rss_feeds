#!/usr/bin/env python3
"""Reorder root keys in RSS JSON files.

Goal:
- put `company` first (if present)
- put `feeds` last (if present)
- keep all other root keys in their current order
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_json(file_path: Path) -> Any:
    with file_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _write_json(file_path: Path, payload: dict[str, Any]) -> None:
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _reorder_root_keys(payload: Any) -> tuple[Any, bool, str]:
    if not isinstance(payload, dict):
        return payload, False, "root is not an object"

    reordered: dict[str, Any] = {}
    if "company" in payload:
        reordered["company"] = payload["company"]

    for key, value in payload.items():
        if key in {"company", "feeds"}:
            continue
        reordered[key] = value

    if "feeds" in payload:
        reordered["feeds"] = payload["feeds"]

    changed = list(payload.keys()) != list(reordered.keys())
    return reordered, changed, "ok"


def _iter_json_files(input_dir: Path, pattern: str, include_test_files: bool) -> list[Path]:
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
        description="Put company first and feeds last in JSON root objects."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=default_input_dir,
        help=f"Directory containing JSON files (default: {default_input_dir})",
    )
    parser.add_argument(
        "--pattern",
        default="*.json",
        help="Glob pattern for files (default: *.json)",
    )
    parser.add_argument(
        "--include-test-files",
        action="store_true",
        help="Also process files ending with _test.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir: Path = args.input_dir

    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {input_dir}")

    json_files = _iter_json_files(
        input_dir=input_dir,
        pattern=args.pattern,
        include_test_files=args.include_test_files,
    )
    if not json_files:
        print("No JSON files matched.")
        return

    written_count = 0
    unchanged_count = 0
    skipped_count = 0

    for file_path in json_files:
        try:
            payload = _read_json(file_path)
            reordered, changed, message = _reorder_root_keys(payload)
            if message != "ok":
                skipped_count += 1
                print(f"{file_path.name}: skipped ({message})")
                continue

            if not changed:
                unchanged_count += 1
                print(f"{file_path.name}: unchanged")
                continue

            if not args.dry_run:
                _write_json(file_path, reordered)
            written_count += 1
            print(f"{file_path.name}: {'would update' if args.dry_run else 'updated'}")
        except Exception as exception:
            skipped_count += 1
            print(f"{file_path.name}: skipped (error: {exception})")

    print(
        f"\nDone. Updated: {written_count} | Unchanged: {unchanged_count} | "
        f"Skipped: {skipped_count} | Scanned: {len(json_files)}"
    )


if __name__ == "__main__":
    main()
