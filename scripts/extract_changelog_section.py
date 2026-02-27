#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


def _extract_section(changelog_path: Path, version: str) -> str:
    lines = changelog_path.read_text(encoding="utf-8").splitlines()
    heading_pattern = re.compile(rf"^## \[{re.escape(version)}\](?:\s*-\s*.+)?\s*$")

    start_index: int | None = None
    for i, line in enumerate(lines):
        if heading_pattern.fullmatch(line):
            start_index = i
            break
    if start_index is None:
        raise ValueError(f"Version {version} not found in {changelog_path}")

    end_index = len(lines)
    for i in range(start_index + 1, len(lines)):
        if re.match(r"^## \[", lines[i]):
            end_index = i
            break

    section = "\n".join(lines[start_index:end_index]).strip()
    if not section:
        raise ValueError(f"Version {version} section in {changelog_path} is empty")
    return section


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "Usage: extract_changelog_section.py <version> [changelog_path]",
            file=sys.stderr,
        )
        return 1

    version = sys.argv[1].strip().lstrip("v")
    changelog_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("CHANGELOG.md")

    try:
        print(_extract_section(changelog_path, version))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
