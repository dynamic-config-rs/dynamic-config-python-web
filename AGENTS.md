# Working in this repository

Native web-framework integrations for `dynamic-config-py`. One pure-Python
distribution — `dynamic-config-py-web`, imported as `dynamic_config_web` —
with a framework-agnostic core and one adapter per framework.

```text
src/dynamic_config_web/
  __init__.py     the public surface: re-exports, and no framework anywhere
  _errors.py      MissingFrameworkError, OutsideRequestScopeError
  _lease.py       one watcher per configuration, per process, fork-aware
  _wiring.py      Wiring: load, watch, stop — idempotent at both ends
  _scope.py       the request scope, and the read that is refused outside one
  _health.py      liveness and readiness, which are different questions
  _metrics.py     the Prometheus body, over a configuration or a group
  _diagnostics.py explain/check, their async twins, and the Guard
  _events.py      reloads into `logging`, and the event stream
  _testing.py     pinned() and as_request()
  pytest.py       the pytest11 plugin — pytest, stdlib, and this package
  fastapi.py      one module per framework, named after it
  litestar.py     a plugin, because that is what Litestar gives a library
  flask.py        an extension object, and a watcher that arms late
  quart.py        Flask's API over `while_serving`, which WSGI has not
  django/         an app, a middleware, a URLconf, `configcheck`, and DRF
  robyn.py        Experimental — the scope is a decorator, and why
  django_bolt.py  Experimental — a factory, because its seams are arguments
tests/
  helpers.py      the model and the writer every test shares
  test_core.py    the shared half, with no framework installed
  test_packaging.py   the purity claims, in subprocesses
  test_django_app.py  the settings/ready()/manage.py half, in subprocesses
  django_bootstrap.py one Django configuration for the whole session
  conformance/    the contract every adapter passes, and one driver each
examples/         one runnable script per framework, all run in CI
book/             the book published at dynamic-config-rs.github.io/web/
```

## The four rules this package exists to enforce

Each of them is a sentence from the engine's own book that used to be advice.
If a change would weaken one, it is the wrong change.

1. **One reading per request.** A handler reads the model the request began
   with, however many times it asks. `_scope` is that, and a scoped read
   outside a request raises — `latest()` is the deliberate escape.
2. **One watcher per configuration, paired with the app.** A second
   `watch()` is `AlreadyExists`; `uvicorn --reload` and a test suite both
   build the app repeatedly. `_lease` counts holders, and re-arms in a
   forked child because a watcher is a thread and a thread does not survive
   `fork()`.
3. **Liveness is not readiness.** `/healthz` never fails on configuration: a
   process that cannot reload should stop receiving traffic, not be
   restarted into reading the same broken file.
4. **No value leaves through a probe.** Health bodies and metrics carry
   generations, kinds, counts and paths — never a configured value. Only
   `explain`/`check` render values, and only behind a `Guard`, and they are
   not mounted at all without one.

## What must move together

Adding or changing an adapter means **all** of these:

1. `src/dynamic_config_web/<framework>.py` — the adapter.
2. `tests/conformance/test_<framework>.py` — a driver, and nothing else; the
   assertions are shared.
3. `tests/test_packaging.py` — a row in `ADAPTERS`, so the missing-framework
   error is checked.
4. `examples/NN_<framework>.py` — runnable, run in CI.
5. `book/src/<framework>.md` and the `SUMMARY.md` line.
6. `pyproject.toml` — the extra, with a floor that says which release the
   seam arrived in, and a marker if the framework needs a newer Python than
   this package's floor.
7. `CHANGELOG.md`, under `## [Unreleased]`.

Adding a case to `tests/conformance/suite.py` is a change to every adapter:
that is the point of the file.

## The gate

```sh
just check          # ruff, mypy --strict, the suite, the examples
just core           # the shared half only, with no framework installed
just book
```

`mypy --strict` over `src/`, and the frameworks are `ignore_missing_imports`
in `pyproject.toml` — which applies **only** when the module is genuinely
absent, so an installed framework is still checked against the real thing.

## Things that have already bitten

- **`BaseHTTPMiddleware` breaks the request scope.** Starlette runs the
  downstream app in a task of its own, and a `ContextVar` set in that
  middleware does not reach the endpoint. Every ASGI adapter uses raw ASGI
  middleware. The conformance case `a_request_never_tears_across_a_reload`
  is what catches a regression.
- **A `def` FastAPI endpoint runs on a worker thread**, and the scope
  reaches it because `anyio` copies the context. That is asserted in
  `test_core.py`, not assumed.
- **`guard=None` and `guard=never` both mean "do not mount"**, and the check
  is identity: a caller's own `def never(request)` is a decision, not the
  default.
- **The engine takes a schema *class*, not a validator function** — unlike
  the Node binding. The tests use a plain dataclass, which is also what
  keeps them runnable in the bare environment.
- **Robyn's middleware and handler do not share a context.** A token set in
  `before_request` is invisible to the handler, so that adapter's scope is a
  `@scoped` decorator and its driver sets `scope_is_automatic = False`. The
  suite skips exactly one case, by name.
- **django-bolt validates a response against the handler's annotation**, so
  `-> Response` makes it check the *body* against the `Response` class and a
  dict body fails. Its routes are annotated `-> Any`.
- **Litestar's `Provide` cache is per application, not per request.**
  `use_cache=False` is what keeps a provider from pinning one snapshot for
  the life of the process — the exact bug this package prevents.
- **Django settings are configured once per process**, which is why the
  conformance driver calls `configure()`/`start()` itself and the app-config
  path is tested in a subprocess instead.
- **The engine's `check()` does not build the model.** It merges layers and
  compares field names; a document whose `port` is `"8080"` passes it and
  then refuses to load. `_diagnostics.check()` calls `load()` as well and
  reports `loads` — do not drop that call.

## What this repository never does

Commit, push, tag or publish on its own. `scripts/` prepares; a person runs
it. The book is published from `dynamic-config-rs.github.io`, which checks
this repository out at `main`.
