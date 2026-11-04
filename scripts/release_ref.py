"""Refuse publication unless HEAD is the requested version's immutable Git tag."""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE,
    ).strip()


def resolve(repo: Path, tag: str | None, expected_commit: str | None = None) -> dict[str, str]:
    commit = git(repo, "rev-parse", "HEAD")
    if expected_commit is not None and expected_commit != commit:
        raise ValueError("Checked-out commit differs from the workflow commit")
    tree = ast.parse(git(repo, "show", f"{commit}:src/fastapiobserver/_version.py"))
    version = next(
        ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets)
    )
    expected_tag = f"v{version}"
    tag = tag or expected_tag
    tag = tag if tag.startswith("v") else f"v{tag}"
    if tag != expected_tag:
        raise ValueError(f"Tag must match package version: {expected_tag}")
    tagged_commit = git(repo, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if tagged_commit != commit:
        raise ValueError(f"Requested tag {tag} identifies a different commit from HEAD")
    return {"tag": tag, "version": str(version), "commit": commit,
            "prerelease": "true" if re.search(r"(?:a|b|rc|dev)\d", version) else "false"}


def verify_existing_files(directory: Path, published: dict[str, str]) -> list[Path]:
    files = [path for path in directory.iterdir() if path.is_file()]
    if not files:
        raise ValueError("No validated distributions found")
    for path in files:
        if path.name in published and sha256(path.read_bytes()).hexdigest() != published[path.name]:
            raise ValueError(
                f"PyPI already has {path.name} with different bytes; reuse the original artifact"
            )
    return [path for path in files if path.name not in published]


def published_hashes(version: str) -> dict[str, str]:
    try:
        endpoint = f"https://pypi.org/pypi/fastapi-observer/{version}/json"
        with urlopen(endpoint, timeout=10) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    return {entry["filename"]: entry["digests"]["sha256"] for entry in payload["urls"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--tag")
    parser.add_argument("--expected-commit")
    parser.add_argument("--distributions", type=Path)
    parser.add_argument("--upload-directory", type=Path)
    args = parser.parse_args()
    try:
        metadata = resolve(args.repo, args.tag, args.expected_commit)
        if args.distributions is not None:
            pending = verify_existing_files(
                args.distributions, published_hashes(metadata["version"]),
            )
            if args.upload_directory is not None:
                args.upload_directory.mkdir(parents=True)
                for path in pending:
                    shutil.copyfile(path, args.upload_directory / path.name)
                metadata["upload_required"] = "true" if pending else "false"
        print(json.dumps(metadata))
    except (OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError) as exc:
        print(f"Release refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
