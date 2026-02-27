#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env}"
REPOSITORY="pypi"
ALLOW_DIRTY=false
SKIP_CHECKS=false
PUSH_TAG=false
TAG=""

usage() {
  cat <<'USAGE'
Usage: scripts/deploy_pypi.sh [options]

Release flow:
1) Validate clean git state (unless --allow-dirty)
2) Run quality gates (ruff, mypy, pytest)
3) Build distributions (uv build)
4) Validate dists (twine check)
5) Upload to PyPI/TestPyPI (token from .env)
6) Optionally create/push a git tag

Options:
  --testpypi      Upload to TestPyPI instead of production PyPI
  --skip-checks   Skip ruff/mypy/pytest quality gates
  --allow-dirty   Allow running with uncommitted git changes
  --tag <tag>     Create git tag after successful upload (e.g. v1.3.2)
  --push-tag      Push the created tag to origin (requires --tag)
  -h, --help      Show this help message
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --testpypi)
      REPOSITORY="testpypi"
      shift
      ;;
    --skip-checks)
      SKIP_CHECKS=true
      shift
      ;;
    --allow-dirty)
      ALLOW_DIRTY=true
      shift
      ;;
    --tag)
      TAG="${2:-}"
      if [[ -z "$TAG" ]]; then
        echo "Error: --tag requires a value."
        exit 1
      fi
      shift 2
      ;;
    --push-tag)
      PUSH_TAG=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if $PUSH_TAG && [[ -z "$TAG" ]]; then
  echo "Error: --push-tag requires --tag <value>."
  exit 1
fi

if [[ ! -f "src/fastapiobserver/_version.py" ]]; then
  echo "Error: src/fastapiobserver/_version.py not found."
  exit 1
fi

PACKAGE_VERSION="$(awk -F '"' '/__version__/ { print $2; exit }' src/fastapiobserver/_version.py)"
if [[ -z "$PACKAGE_VERSION" ]]; then
  echo "Error: could not resolve package version from src/fastapiobserver/_version.py."
  exit 1
fi
EXPECTED_TAG="v${PACKAGE_VERSION}"

if [[ -n "$TAG" && "$TAG" != "$EXPECTED_TAG" ]]; then
  echo "Error: tag '$TAG' does not match package version '$PACKAGE_VERSION' (expected '$EXPECTED_TAG')."
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: $ENV_FILE not found. Create it and set PYPI_TOKEN first."
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

if [[ "$REPOSITORY" == "pypi" ]]; then
  TOKEN_VALUE="${PYPI_TOKEN:-}"
  REPOSITORY_URL="https://upload.pypi.org/legacy/"
else
  TOKEN_VALUE="${TEST_PYPI_TOKEN:-${PYPI_TOKEN:-}}"
  REPOSITORY_URL="https://test.pypi.org/legacy/"
fi

if [[ -z "$TOKEN_VALUE" ]]; then
  echo "Error: Missing token. Set PYPI_TOKEN (and optionally TEST_PYPI_TOKEN) in $ENV_FILE."
  exit 1
fi

if ! $ALLOW_DIRTY; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Error: git working tree is dirty. Commit/stash changes or use --allow-dirty."
    git status -sb
    exit 1
  fi
fi

echo "[1/6] Verifying toolchain"
uv run python --version >/dev/null
uv run twine --version >/dev/null

echo "[2/6] Validating release metadata"
uv run python scripts/extract_changelog_section.py "$PACKAGE_VERSION" >/dev/null

if ! $SKIP_CHECKS; then
  echo "[3/6] Running quality gates"
  uv run ruff check .
  uv run mypy src
  uv run pytest -q
else
  echo "[3/6] Skipping quality gates (--skip-checks)"
fi

echo "[4/6] Building distributions"
rm -rf dist build *.egg-info
uv build
uv run twine check dist/*

echo "[5/6] Uploading distributions to $REPOSITORY"
TWINE_USERNAME="__token__" \
TWINE_PASSWORD="$TOKEN_VALUE" \
uv run twine upload --repository-url "$REPOSITORY_URL" dist/*

if [[ -n "$TAG" ]]; then
  echo "[6/6] Creating git tag: $TAG"
  if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "Error: git tag '$TAG' already exists."
    exit 1
  fi
  git tag "$TAG"

  if $PUSH_TAG; then
    echo "Pushing tag '$TAG' to origin"
    git push origin "$TAG"
  fi
else
  echo "[6/6] Skipping git tag creation (no --tag provided). Expected release tag is '$EXPECTED_TAG'."
fi

echo "Release deployment flow finished successfully."
