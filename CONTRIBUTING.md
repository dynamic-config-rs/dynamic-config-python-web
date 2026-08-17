# Contributing

Thank you for looking. This repository is pure Python over a compiled wheel,
so there is no toolchain to install beyond an interpreter.

## The gate

```sh
uv venv && uv pip install -e '.[dev,all]'
just check
```

`just check` is what CI runs: `ruff check`, `ruff format --check`,
`mypy --strict` over the package, the core suite, every adapter whose
framework is installed, and every example. `just book` needs
[mdbook](https://rust-lang.github.io/mdBook/) and is checked separately.

Working on one framework? `just adapter fastapi` runs that adapter's
conformance run alone. A framework that is not installed skips itself — CI
is what proves the skip did not hide anything.

## Adding a framework

Seven are here. An eighth is welcome, and it is mostly a driver:

1. `src/dynamic_config_web/<framework>.py` — the adapter. Read an existing
   one first: they are deliberately the same shape, and the shared core
   (`Wiring`, the scope, `readiness`, `metrics_body`, the diagnostics) is
   where the behaviour lives.
2. `tests/conformance/test_<framework>.py` — a driver and nothing else. The
   assertions are shared, and all twelve must pass; a case a framework
   genuinely cannot satisfy goes in `book/src/limitations.md` rather than
   being skipped quietly.
3. A row in `tests/test_packaging.py`'s `ADAPTERS`, so the
   missing-framework error is checked.
4. `examples/NN_<framework>.py`, runnable, no setup, run in CI.
5. `book/src/<framework>.md` and its `SUMMARY.md` line.
6. The extra in `pyproject.toml`, with a floor naming the release the seam
   arrived in — and an environment marker if the framework needs a newer
   Python than this package's floor.
7. A `CHANGELOG.md` entry under `## [Unreleased]`.

`AGENTS.md` has the four rules an adapter must not weaken, and the list of
things that have already bitten.

## What the review will ask

- **Does the request scope reach the handler?** For an ASGI framework that
  means raw ASGI middleware, not `BaseHTTPMiddleware` — a `ContextVar` set
  in the latter does not cross into the endpoint, and nothing fails loudly
  when it does not.
- **Is the watcher paired with the application?** One place that starts it,
  one that stops it, and idempotent at both ends.
- **Can a probe leak a value?** Health and metrics bodies carry paths,
  kinds, counts and seconds. Nothing else.
- **Does it work under `--reload` and behind a fork?** The conformance suite
  asks the first; `tests/test_core.py` forks for real.

## Branches, commits, releases

Work lands on `dev` through pull requests; `main` takes no direct pushes.
Merging a version bump into `main` *is* the release — there is no tag to
push by hand. Commit messages are sentences about behaviour, in the present
tense, without a prefix taxonomy.

Nothing in this repository commits, pushes, tags or publishes on your
behalf. `scripts/` prepares; you run it.
