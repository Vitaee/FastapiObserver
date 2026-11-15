# Maintenance & Contributing

Before changing the middleware, the log queue, redaction, the trust checks or the OpenTelemetry wiring, read the matching [design decision](decisions/index.md). Each one lists the tests that guard it. Planned work is in the [roadmap](roadmap.md), and the 2.2.1 compatibility probe in the [baseline](baseline.md).

Changelog entries say what changed and what users have to do, in one or two sentences, with breaking changes under a `Breaking Changes` heading. The reasoning goes in the pull request, and for design choices in a decision record.

## Benchmarking

- CPU per request: `examples/benchmarks/request_cpu.py`, described in [benchmarks](benchmarks.md).
- Throughput under load: `python examples/benchmarks/harness.py [SCENARIO ...]` runs
  `examples/benchmarks/app.py` under ApacheBench and writes `examples/benchmarks/results.json`.

## Runtime Stress Validation

For changes to logging lifecycle, queue pressure behavior, fork safety or
FastAPI lifespan teardown, run the runtime stress harness:

```bash
uv run python scripts/runtime_stress.py --profile quick
uv run python scripts/runtime_stress.py --profile standard --json
```

Profiles:
- `quick`: fast local sanity pass.
- `standard`: required in shared validation; run it before merging.
- `deep`: heavy soak run for major refactors.

The workload mixes four request types: a `200` with database and HTTP calls and
redacted fields, a `422` validation failure, a `200` with ten repeated queries
(`db.n_plus_one`) and a held connection (`db.connection_held_during_http`), and a
`500` whose exception carries URL credentials. It also churns lifespans,
overflows the queue under each policy and replays `os.fork()` in a subprocess.

CI runs it from `.github/workflows/runtime-stress.yml`: weekly with `quick`, and
on demand (`workflow_dispatch`).

## Release validation budgets

PRs and releases call the same validation workflow. A release must identify one
immutable tagged commit and publish the stored, hash-checked distributions that
passed its gates. The backend jobs install that wheel into the Compose apps and
run the verifier with tracing enabled and disabled.

| Scenario | Gate |
|---|---|
| SIGTERM with eight concurrent requests | Ordinary responses, streams and background work finish within four seconds with one terminal JSON record per request and no local request-log loss; Uvicorn's request grace is one second |
| Request exceeding that grace | Cancellation is recorded, without a client-disconnect cause |
| Collector unavailable | Ten requests finish within two seconds; process shutdown within four seconds with a 0.2-second OTLP transport timeout; all ten local terminal records survive in `both` mode |
| Blocked sink | A 50 ms drain/stop wait returns within 500 ms; 100 loopback requests keep p95 below 100 ms while queue drops are counted; active outputs stay open for retry |
| Warm worker | After 300 warm-up and 1,000 further distinct-query requests, retained Python heap grows less than 8 MiB, Linux RSS less than 32 MiB, and thread count by at most two |
| Worker replacement | Twelve sequential workers keep one bounded route series and accurate cumulative request/histogram counts |

These budgets apply to the test workload, not to arbitrary applications. The
stress report is kept as a workflow artifact. CPU comparisons between versions
don't gate a release; run them by hand as described in
[benchmarks](benchmarks.md#manual-version-comparison).

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
matrix, quality/audit, the [release budgets](#release-validation-budgets) and
Compose backend checks, then publishes the stored artifact with attestations.
The publisher checks its commit and file hashes instead of rebuilding it.

Prerelease versions such as `X.Y.Za1` use the same gates and tag convention.
Their GitHub releases are marked as prereleases and do not replace the latest
stable release.

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

Selecting a branch whose commit differs from the requested tag fails before
validation and publication. Code changes require a new version and tag. Before
announcing completion, verify the GitHub Release, both PyPI distributions and a
fresh installation from PyPI.

## Local Git Hook (Recommended)

```bash
git config core.hooksPath .githooks
```

The pre-push hook runs:
- `uv run ruff check`
- `uv run mypy src`
- `uv run pytest -q --cov`, which fails below 90% branch coverage, as CI does

## Documentation Site

The site at [vitaee.github.io/FastapiObserver](https://vitaee.github.io/FastapiObserver/) is built from `docs/` and `mkdocs.yml` with [Zensical](https://zensical.org/). The `Docs` workflow builds it in strict mode on pull requests and deploys it to GitHub Pages on pushes to `main`.

Preview locally:

```bash
uvx zensical==0.0.73 serve
```

New pages must be added to `nav` in `mkdocs.yml`.
