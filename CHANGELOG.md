# Changelog

All notable changes to `dynamic-config-py-web` are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Before 1.0, a breaking change bumps the **minor** version and anything else
bumps the patch. A change to the minimum supported Python version is
breaking; so is dropping a framework, or raising a framework's floor past a
release people are on.

This distribution versions independently of `dynamic-config-py`: it depends
on the wheel rather than shipping it, so an engine release has nothing in it
for an adapter and an adapter fix should not drag the wheels behind it.

<!-- Keep this template. Add entries under `Unreleased` as you go, and move
     the whole block under a new version heading at release time.

## [_Unreleased_]

### Added
### Changed
### Deprecated
### Removed
### Fixed
### Security

-->

## [Unreleased]

### Added

- **The shared core.** `Wiring` (load, watch, stop — idempotent, leased per
  configuration and re-armed after `fork()`), the request scope
  (`scope`/`current`/`get`/`latest`, with a read outside a request raising
  rather than answering), `liveness`/`readiness` as the two different
  questions they are, `metrics_body` over a configuration or a whole
  `ConfigGroup`, `explain`/`check` with async twins and a `Guard`,
  `log_reloads`/`stream_events`, and the `pinned`/`as_request` test doors.

- **The FastAPI adapter.** `setup(app, config_or_group_or_wiring)` wraps the
  application's lifespan, installs a pure-ASGI request-scope middleware, and
  mounts `/healthz`, `/readyz` and `/metrics` — plus `/_config/explain` and
  `/_config/check` when, and only when, a guard is given.
  `config_dependency(config)` is the `Depends` provider, and it answers the
  same object every time so a test can override it by name.

- **The Litestar adapter.** `DynamicConfigPlugin` adds a lifespan, a
  `Provide` per configuration, the health routes and the scope middleware
  through `InitPlugin.on_app_init`. The provider is built `use_cache=False`
  deliberately: Litestar's cache is per application, so a cached one would
  pin a single snapshot for the life of the process. `NamedDependency` is
  re-exported, so a handler written against it neither warns on Litestar
  2.23+ nor breaks on anything older.

- **The Flask adapter.** `DynamicConfigExtension` in `app.extensions`, a
  blueprint of health routes, `before_request`/`teardown_appcontext` for the
  scope, and `snapshot()` as the read — with nothing written into
  `app.config`, because a copy never reloads. WSGI has no lifespan, so the
  watcher arms on the first request each process serves, which is after a
  fork by construction; `start="eager"` and `start="manual"` are there for
  deployments that know better.

- **The Quart adapter.** The Flask extension's API over Quart's
  `while_serving`, which is the moment WSGI cannot offer: the watcher starts
  after the workers exist and before the first request, so a worker that
  cannot load its configuration fails to start rather than serving 503s.

- **The Django adapter.** An installed app whose `ready()` reads
  `DYNAMIC_CONFIG` — a pointer, never a value, because Django caches
  settings for the life of the process — a sync-and-async middleware that
  opens the scope and attaches `request.dynamic_config`, a URLconf to
  `include`, and `manage.py configcheck` (with `--explain` and `--strict`).
  Two rules decide what each process does: `should_watch()` keeps a watcher
  out of `runserver`'s autoreloader parent and out of management commands,
  and `serving_process()` makes a broken document fail a *worker's* startup
  while letting a command run — because `configcheck` is the tool you reach
  for when configuration is broken.

- **The Django REST Framework layer.** The same surface as `APIView`s, with
  `ConfigDiagnosticsPermission` deferring to the installation's guard so a
  project can put diagnostics inside its own permission scheme. Readiness
  and metrics stay open; a refused diagnostics request gets DRF's 403 rather
  than the plain views' 404, and with no guard neither set is mounted.

- **The django-ninja adapter.** `router()` mounts the health, metrics and
  diagnostics operations on a `NinjaAPI`. Nothing else was needed: django-ninja
  is a Django application, so `AppConfig.ready` already loads and watches and
  the middleware already opens the request scope. Every operation registers
  with `auth=None` except the two diagnostics ones, so a project that sets an
  API-wide `auth=` does not end up with a liveness probe that reports its
  authentication backend's health.

  A refused diagnostics request gets **401** here, against the plain views'
  404 and DRF's 403 — each adapter keeps its framework's convention, and the
  conformance suite accepts all three.

- **The Robyn adapter** (Experimental). `setup(app, config)` registers the
  startup and shutdown handlers and a `SubRouter` of health routes. The
  request scope is a `@scoped` decorator rather than middleware, and that is
  a finding rather than a shortcut: a `ContextVar` set in Robyn's
  before-request middleware is not visible in the handler, so a scope opened
  there would silently not be there. A handler that forgets the decorator
  raises instead of reading unscoped.

- **The django-bolt adapter** (Experimental). `api(config, …)` builds a
  `BoltAPI` with the lifespan, the scope middleware and the health routes
  already on it, passing every other keyword through — a factory, because
  django-bolt takes both seams as constructor arguments and reaching past
  them into private attributes is not a thing to build on a 0.10 library.
  `lifespan()`, `ScopeMiddleware` and `router()` are public for an
  application that must build its own.

- **`check()` answers whether the document would load**, not only whether
  its keys resolve. The engine's own `check()` merges the layers and
  compares the field names; it does not build the model, so a document whose
  `port` is the string `"8080"` passed it and then refused to load. The
  report now carries `loads`, `clean` means both, and `failure` carries the
  validation error when that is what went wrong — which is what
  `/_config/check` and `manage.py configcheck` report.

- **A pytest plugin**, on its own `pytest11` entry point:
  `dynamic_config_wiring`, `dynamic_config_pinned`, `dynamic_config_request`
  and `dynamic_config_watchers`. Nothing is autouse, and the module imports
  no framework.

- **A conformance suite.** Twelve behavioural cases every adapter must pass,
  written once and parametrised by a per-framework driver: a request never
  tears across a reload, the watcher is paired with the app, building the app
  twice does not collide, `/healthz` stays 200 while `/readyz` goes 503, no
  value reaches a metrics body, the diagnostics routes are absent without a
  guard.

  All nine drivers run it — FastAPI, Litestar, Flask, Quart, Django, DRF,
  django-ninja, Robyn and django-bolt — and the one case Robyn cannot answer
  is skipped by name (`scope_is_automatic = False`) rather than quietly, so
  the suite records which framework cannot do what.
