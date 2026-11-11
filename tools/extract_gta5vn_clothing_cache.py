#!/usr/bin/env python3
"""Map GTA5VN clothing resources in a local FiveM server cache."""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from pathlib import Path, PurePosixPath


RESOURCE_URL = re.compile(
    rb"https?://(?P<host>[A-Za-z0-9.-]+)/files/"
    rb"(?P<path>[^?\x00\s\"<>]+)\?hash=(?P<hash>[0-9a-f]{40})",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read the local server-cache LevelDB index and write a CSV mapping "
            "resource paths to cached blobs. No network access is performed."
        )
    )
    parser.add_argument(
        "cache_root",
        type=Path,
        help="FiveM/GTA5VN data/server-cache directory",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("gta5vn-clothing-cache-map.csv"),
        help="CSV output path (default: %(default)s)",
    )
    parser.add_argument(
        "--resource-prefix",
        default="gta5vn_addoncloth/",
        help="Only include resource paths with this prefix (default: %(default)s)",
    )
    parser.add_argument(
        "--all-resources",
        action="store_true",
        help="Include every indexed resource, not just the clothing prefix",
    )
    parser.add_argument(
        "--extract-dir",
        type=Path,
        help="Optionally copy present cache blobs into this directory using their resource paths",
    )
    return parser.parse_args()


def index_files(index_dir: Path) -> list[Path]:
    if not index_dir.is_dir():
        raise FileNotFoundError(f"Cache index directory not found: {index_dir}")
    return sorted(
        path
        for path in index_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".ldb", ".log"}
    )


def read_resources(index_dir: Path, prefix: str, all_resources: bool) -> dict:
    resources = {}
    for index_path in index_files(index_dir):
        try:
            data = index_path.read_bytes()
        except OSError as exc:
            print(f"warning: cannot read {index_path}: {exc}", file=sys.stderr)
            continue
        for match in RESOURCE_URL.finditer(data):
            resource_path = match.group("path").decode("utf-8", "replace")
            if not all_resources and not resource_path.lower().startswith(prefix.lower()):
                continue
            cache_hash = match.group("hash").decode("ascii").lower()
            host = match.group("host").decode("ascii", "replace")
            resources[(resource_path, cache_hash)] = host
    return resources


def cached_blob(cache_root: Path, cache_hash: str) -> Path | None:
    filename = f"cache_{cache_hash}"
    for candidate in (cache_root / filename, cache_root / "unconfirmed" / filename):
        if candidate.is_file():
            return candidate
    return None


def export_blob(source: Path, resource_path: str, output_dir: Path) -> None:
    relative = PurePosixPath(resource_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe resource path in cache index: {resource_path}")
    destination = output_dir.joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def main() -> int:
    args = parse_args()
    cache_root = args.cache_root.resolve()
    resources = read_resources(
        cache_root / "db",
        args.resource_prefix.strip("/"),
        args.all_resources,
    )
    if not resources:
        raise RuntimeError(
            "No matching resource references found in the cache index. "
            "The resource may not have been streamed to this client."
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    extracted = 0
    for (resource_path, cache_hash), host in sorted(resources.items()):
        blob = cached_blob(cache_root, cache_hash)
        present = blob is not None
        size = blob.stat().st_size if blob else ""
        signature = ""
        if blob:
            with blob.open("rb") as stream:
                signature = stream.read(4).decode("ascii", "replace")
            if args.extract_dir:
                export_blob(blob, resource_path, args.extract_dir)
                extracted += 1
        rows.append(
            {
                "original_resource_path": resource_path,
                "cache_hash": cache_hash,
                "cache_filename": f"cache_{cache_hash}",
                "present": "yes" if present else "no",
                "size_bytes": size,
                "signature": signature,
                "source_host": host,
            }
        )

    columns = [
        "original_resource_path",
        "cache_hash",
        "cache_filename",
        "present",
        "size_bytes",
        "signature",
        "source_host",
    ]
    with args.output.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    present_count = sum(row["present"] == "yes" for row in rows)
    print(
        f"Wrote {len(rows)} indexed resources ({present_count} cached) to {args.output}"
    )
    if args.extract_dir:
        print(f"Copied {extracted} cached blobs to {args.extract_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
