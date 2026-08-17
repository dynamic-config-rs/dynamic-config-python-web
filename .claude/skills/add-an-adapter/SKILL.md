---
name: add-an-adapter
description: Use when adding support for a new web framework to dynamic-config-py-web, or when changing an existing adapter — what an adapter owes the shared contract, what moves with it, and the four seams to find in the framework first.
---

# Adding an adapter

An adapter is a *translation*, not an implementation. Everything that
decides behaviour — the wiring, the scope, health, metrics, diagnostics —
lives in `src/dynamic_config_web/_*.py` and is already tested. If an
adapter is doing something the others are not, that is a finding to write
down, not a feature.

## Find four seams in the framework, in this order

1. **A lifespan** — a place that runs after the workers exist and before
   the first request, and again on the way down. That is where the watcher
   goes. If the framework has none (WSGI does not), the watcher arms on the
   first request instead, which is after a fork by construction. Say which
   in the module docstring.
2. **A per-request hook that shares a context with the handler.** This is
   the one to verify empirically rather than assume: set a `ContextVar` in
   the hook, read it in a handler, and check. Starlette's
   `BaseHTTPMiddleware` fails this; so does Robyn's before-request
   middleware, which is why that adapter has a `@scoped` decorator and its
   driver sets `scope_is_automatic = False`.
3. **A place to mount routes** — a router, a blueprint, a URLconf.
4. **The framework's own injection**, if it has one — `Depends`,
   `Provide`, a `request` attribute. Check its caching semantics: a
   provider cached per *application* rather than per request would pin one
   snapshot for the life of the process, which is the exact bug this
   package prevents.

## What moves together

AGENTS.md lists it, and the hook prints it on every edit. Nothing here is
optional:

`<framework>.py` · `tests/conformance/test_<framework>.py` (a driver, and
nothing else — the assertions are shared) · a row in
`tests/test_packaging.py::ADAPTERS` · `examples/NN_<framework>.py` ·
`book/src/<framework>.md` and the `SUMMARY.md` line · the extra in
`pyproject.toml` · a matrix row in `.github/workflows/ci.yml` · a
`## [Unreleased]` entry.

And, so `dynamic-config-py[newframework]` resolves: the matching extra in
`dynamic-config-python/dynamic-config-python/pyproject.toml`.

## The rules an adapter must not weaken

1. One reading per request, and a scoped read outside a request raises.
2. One watcher per configuration, paired with the app, re-armed after a fork.
3. `/healthz` never fails on configuration; `/readyz` is where it does.
4. No configured value leaves through a probe or a metrics body, and the
   diagnostics routes are **not mounted at all** without a guard.

## When a framework cannot pass a case

Skip it by name in `suite.cases()` — as `scope_is_automatic = False` does
for Robyn — and write the reason in `book/src/limitations.md`. A quietly
skipped case is a promise nobody is keeping.
