# Maintenance & Contributing

Before changing the middleware, the log queue, redaction, the trust checks or the OpenTelemetry wiring, read the matching [design decision](decisions/index.md). Each one lists the tests that guard it.

Changelog entries say what changed and what users have to do, in one or two sentences. The reasoning goes in the pull request, and for design choices in a decision record.


## OTel Test Coverage

Repository integration tests include:
- `tests/test_otel_log_correlation.py`: verifies trace/span IDs in logs map to real spans.
- `tests/test_otlp_export_integration.py`: validates OTLP HTTP export with local collector fixtures.

---

## Benchmarking

Reproducible benchmark harness and methodology:
- Guide: [`benchmarks.md`](benchmarks.md)
- Apps: `examples/benchmarks/app.py`
- Runner: `examples/benchmarks/harness.py`

---

## Runtime Stress Validation

For high-risk changes in logging lifecycle, queue pressure behavior, fork safety,
or FastAPI lifespan teardown, run the runtime stress harness:

```bash
uv run python scripts/runtime_stress.py --profile quick
uv run python scripts/runtime_stress.py --profile standard --json
```

Profiles:
- `quick`: fast local sanity pass.
- `standard`: recommended pre-merge/pre-release validation.
- `deep`: heavy soak run for major refactors.

Implementation note:
- Fork replay verification is executed in an isolated subprocess so the main harness run stays clean on Python versions that warn about multi-threaded `os.fork()`.

CI automation:
- Manual workflow: `.github/workflows/runtime-stress.yml` (`workflow_dispatch`)
- Weekly scheduled quick stress run

---

## Changelog Policy

Breaking changes must be listed under a `Breaking Changes` section in `CHANGELOG.md`.

---

## Releasing (Maintainers)

Releases are published from CI with PyPI Trusted Publishing; no token is stored anywhere.

1. Set the version in `src/fastapiobserver/_version.py` and date its `CHANGELOG.md` section in a `release/vX.Y.Z` pull request, or in the feature PR being released. Update `SECURITY.md` when the supported minor version changes.
2. After it merges, tag the merge commit and push the tag:

    ```bash
    git tag vX.Y.Z
    git push origin vX.Y.Z
    ```

On a tag push, `.github/workflows/release.yml` verifies that the tag and
checked-out commit agree, then calls the same validation workflow as pull
requests. It builds the distributions once, tests that wheel, runs the dependency
matrix, quality/audit, runtime budgets and Compose backend checks, then publishes
the stored artifact with attestations. The publisher checks its commit and file
hashes instead of rebuilding it. CPU comparisons between versions don't gate a
release; run them by hand as described in
[benchmarks](benchmarks.md#manual-version-comparison).

Experimental versions such as `2.3.0a1` use the same gates and tag convention.
Their GitHub releases are marked as prereleases and do not replace the latest
stable release. Keep the package's Beta classification until stability evidence
supports a separate promotion decision.

Keep published tags immutable. After a partial publication, use **Re-run failed
jobs** on the original workflow run so the original distribution artifact is
reused. Existing PyPI filenames must have identical SHA-256 hashes;
only files absent from PyPI are staged for upload. The publisher does not use
`skip-existing`, so a concurrent conflicting upload fails visibly. Publication
jobs for the same tag are serialized. If the original artifact is unavailable,
prepare a new patch version.

For a full manual run, select the original tag:

```bash
gh workflow run release.yml --ref vX.Y.Z -f tag=vX.Y.Z
```

Selecting a branch whose commit differs from the requested tag now fails before
validation/publication. Code changes require a new version and tag. Before
announcing completion, verify the GitHub Release, both PyPI distributions and a
fresh installation from PyPI. Standard stress is a required shared validation
gate; use `uv run python scripts/runtime_stress.py --profile standard` locally too.

---

## Local Git Hook (Recommended)

```bash
git config core.hooksPath .githooks
```

The pre-push hook runs:
- `uv run ruff check`
- `uv run mypy src`
- `uv run pytest -q --cov`, which fails below 90% branch coverage, as CI does

---

## Documentation Site

The site at [vitaee.github.io/FastapiObserver](https://vitaee.github.io/FastapiObserver/) is built from `docs/` and `mkdocs.yml` with [Zensical](https://zensical.org/). The `Docs` workflow builds it in strict mode on pull requests and deploys it to GitHub Pages on pushes to `main`.

Preview locally:

```bash
uvx zensical==0.0.73 serve
```

New pages must be added to `nav` in `mkdocs.yml`.

---

## Roadmap Tracking

See [ROADMAP.md](https://github.com/Vitaee/FastapiObserver/blob/main/ROADMAP.md) for planned work.
