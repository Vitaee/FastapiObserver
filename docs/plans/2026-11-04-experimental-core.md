# Experimental Python core implementation plan

**Goal:** preserve the 2.2.1 FastAPI interface while proving a plain Python
operation can reuse structured logging, SQLAlchemy counts and HTTP diagnostics.

**Execution:** complete the tasks sequentially on
`feature/experimental-python-core`, with one pull request and neutral commit
messages. Keep the existing checkout and its local roadmap changes.

## Design and constraints

- Add an opt-in `fastapiobserver.experimental.observe_operation()` boundary,
  supporting `with` and `async with`, using the existing shared request state.
  Reuse client hooks, logging filters, formatting, redaction and queues.
- Keep all 27 stable root exports, their signatures, FastAPI request events,
  timing, configuration defaults and metric semantics unchanged.
- Restore context with ContextVar tokens. Child operations own their dependency
  counts; parent totals exclude those child calls. Closing experimental state
  prevents late callbacks from modifying it and releases retained details.
- An operation borrows application logging. It creates no handlers, provider,
  watcher, signal handler or driver instrumentation. Callers initialize and
  flush their existing logging and attach explicit client hooks themselves.
- Completion events contain an operation name/ID, parent ID where present,
  outcome, duration and observed dependency totals. No statement or exception
  text is added by the operation boundary. Logging failures do not replace an
  application's exception or cancellation.
- Make root exports lazy and remove logging's type-only Starlette imports so
  core imports and operations work without framework packages. Preserve the
  distribution's existing required dependencies for this experiment.
- Package the experiment in the existing distribution; choose a separate
  framework-free distribution only after proving this interface. A no-deps
  installation is a separation check, not a supported installation command.
- Keep Beta. Use a prerelease version and mark upstream prereleases accordingly.
  Do not publish before required validation has passed.
- Real application validation requires a runnable adopter application. Prior
  use is established by the maintainer's report; bundled examples provide
  reproducible integration evidence, not independent adopter evidence.

## Alternatives considered

1. Extend the existing state and logging seams: selected for this experiment;
   avoids duplicate pipelines and permits direct FastAPI parity tests.
2. Move all reusable modules into a new distribution now: defers validation
   while introducing file ownership and dependency migration risk.
3. Implement another recorder/logging pipeline: duplicates behavior and risks
   diverging correlation, redaction and lifecycle semantics.

## Review focus

- A nested operation must restore the surrounding FastAPI or Python context.
- Concurrent tasks and copied thread contexts must retain independent ownership.
- Late streamed HTTP completion must not resurrect a closed operation.
- Application handlers and exceptions must survive diagnostic emission failures.
- Fresh imports must neither require frameworks nor start runtime resources.

## Tasks

- [x] Capture a reproducible 2.2.1 compatibility fixture and claim/evidence,
  ownership and dependency inventories. Correct current documentation claims;
  preserve historical decision records.
- [x] Run the bundled existing-logging application against the baseline and
  candidate; record external application validation separately.
- [x] Write failing tests for framework-free imports and retained root exports;
  implement minimal lazy imports and type-only framework references.
- [x] Write failing operation tests for sync/async, nesting, exceptions,
  cancellation, copied threads, concurrent tasks, late completion and logging
  ownership; implement the boundary using existing state and client hooks.
- [x] Add a runnable Python-only SQLite/HTTP example and integration proof,
  with tracing disabled and enabled. Preserve native/span deduplication.
- [x] Document the experimental contract and limitations, update the roadmap
  sequencing and add an architectural decision in the existing format.
- [ ] Build a prerelease wheel/sdist; validate installed-wheel imports and
  behavior, full tests/coverage, typing/lint, strict documentation, production
  stress, paired overhead and available backend checks. Review the whole diff.
- [ ] Commit and push on the single branch, open one PR, and record release
  readiness and any remaining real application or publication gate honestly.
