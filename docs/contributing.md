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

1. Open a `release/vX.Y.Z` pull request that sets the version in `src/fastapiobserver/_version.py` and dates its `CHANGELOG.md` section.
2. After it merges, tag the merge commit and push the tag:

    ```bash
    git tag vX.Y.Z
    git push origin vX.Y.Z
    ```

On a tag push, `.github/workflows/release.yml` runs the tests, rejects a tag that doesn't match `_version.py`, builds and checks the distributions, publishes them to PyPI with attestations, and creates the GitHub Release from the matching `CHANGELOG.md` section.

If publishing fails partway, don't re-run the tag's run: it rebuilds files that differ from the ones PyPI already has. Run the workflow by hand instead, which skips files PyPI has:

```bash
gh workflow run release.yml --ref main -f tag=vX.Y.Z
```

For high-risk changes, run the stress harness before tagging:

```bash
uv run python scripts/runtime_stress.py --profile standard
```

---

## Local Git Hook (Recommended)

```bash
git config core.hooksPath .githooks
```

The pre-push hook runs:
- `uv run ruff check`
- `uv run mypy src`
- `uv run pytest -q`

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
