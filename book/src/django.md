# Django

```sh
pip install "dynamic-config-py[django]"
```

Four lines in a project, and no more:

```python
# settings.py
INSTALLED_APPS = [..., "dynamic_config_web.django"]
MIDDLEWARE = [
    "dynamic_config_web.django.middleware.DynamicConfigMiddleware",
    ...,
]
DYNAMIC_CONFIG = {"target": "myproject.config:database"}

# urls.py
urlpatterns = [path("", include("dynamic_config_web.django.urls")), ...]
```

```python
# views.py
from dynamic_config_web.django import snapshot


def index(request):
    db = snapshot()                      # or request.dynamic_config["db"]
    return JsonResponse({"host": db.host, "pool": db.pool.max_size})
```

[`examples/05_django.py`](https://github.com/dynamic-config-rs/dynamic-config-python-web/blob/main/examples/05_django.py)
runs all of it, DRF half included.

## Configuration does not go in `settings`

Django loads its settings module once and caches it for the life of the
process. A value copied in at startup is frozen there — the file reloads,
the engine installs a new model, and `settings.DATABASE_HOST` keeps answering
with the old one.

`DYNAMIC_CONFIG` therefore holds a **pointer**, never a value:

```python
DYNAMIC_CONFIG = {
    "target": "myproject.config:database",   # dotted path, resolved at ready()
    "watch": True,
    "debounce": 0.25,
    "poll_interval": None,                   # seconds, to poll instead
    "guard": "myproject.config:only_operators",
    "stale_after": 300.0,
    "metrics": True,
    "diagnostics_prefix": "_config",
}
```

The short form `DYNAMIC_CONFIG = "myproject.config:database"` means the same
thing with every default. Both `target` and `guard` accept
`package.module:attribute` and `package.module.attribute`; they are resolved
in `ready()` rather than in `settings.py`, because importing project code
from a settings module while Django is still starting is a well-known way to
produce a circular import.

A project that would rather wire in Python calls
`dynamic_config_web.django.configure(config)` from its own `AppConfig.ready`
and leaves `DYNAMIC_CONFIG` unset.

## Which processes watch, and which fail

`AppConfig.ready()` runs in every process Django starts, and they do not all
want the same thing. The adapter asks two questions:

**Should this process watch files?** — `should_watch()`

| process | watches |
|---|---|
| gunicorn, uvicorn, mod_wsgi, granian | yes, one watcher per worker |
| `runserver` — the autoreloader's parent | no (`RUN_MAIN` is unset there) |
| `runserver` — the child that serves | yes |
| `runserver --noreload` | yes, there is only one process |
| `migrate`, `shell`, `collectstatic`, `configcheck` | no — it loads, but a watcher would only delay the exit |

**Should a broken document stop it?** — `serving_process()`

A **serving** process that cannot load its configuration does not start. That
is the whole value of loading in `ready()`: the failure lands in the deploy,
where a rollback is one command, instead of in a worker that has already
taken traffic.

A **management command** is the exception. `configcheck` is the
tool you reach for *because* configuration is broken, and a `migrate` that
cannot run during an outage is a worse problem than the outage. Commands load
if they can, log a warning if they cannot, and carry on.

## The middleware

```python
MIDDLEWARE = [
    "dynamic_config_web.django.middleware.DynamicConfigMiddleware",
    ...,
]
```

**Put it first, or close to it.** Everything below it — every other
middleware, every view — then reads the same snapshot. Something listed
*above* it reads outside the scope and raises, which is a clearer failure
than reading a different generation.

It is sync- and async-capable in one class, so a project serving both under
ASGI does not get wrapped in `sync_to_async` — a wrapper that would run the
view in another thread and could leave the scope open on the wrong one.

It also attaches `request.dynamic_config`:

```python
db = request.dynamic_config["db"]
db = request.dynamic_config.one()     # when there is only one
```

which is there for the code that has a `request` in hand. `snapshot()` is the
same values without one.

## `manage.py configcheck`

```console
$ python manage.py configcheck
[db]
  host                         in /etc/myapp/config.toml
  port                         in APP_PORT

  would load
ok — Database would load

$ python manage.py configcheck --explain database.port
$ python manage.py configcheck --config db --strict
```

Exits non-zero when a configuration would not load, which is what makes it
useful in a container's start script and in CI: the failure happens in a
command whose output you read, rather than in a worker whose first request
you do not.

`--strict` fails on unknown keys as well as on a refusal.

## Django REST Framework

```sh
pip install "dynamic-config-py[drf]"
```

```python
from dynamic_config_web.django.drf import urls as config_urls

urlpatterns = [path("internal/", include(config_urls())), ...]
```

The plain views already work in a DRF project — they are just views. The DRF
set exists for the project that wants its *diagnostics* inside its own
permission scheme rather than behind a shared token:

```python
from dynamic_config_web.django.drf import ConfigCheckView

class OurCheckView(ConfigCheckView):
    permission_classes = [IsAdminUser]
```

`ConfigDiagnosticsPermission` — the default — defers to the installation's
guard, so `guard=` covers both sets. Readiness and metrics stay open, because
a probe and a scrape are not authenticated callers and neither renders a
value.

A request these refuse gets **403**, where the plain views answer 404 — DRF's
convention, and what a project's own permission classes produce anyway. With
no guard configured, neither set of diagnostics routes is built at all.

## django-ninja

```sh
pip install "dynamic-config-py[ninja]"
```

```python
from ninja import NinjaAPI
from dynamic_config_web.django.ninja import router

api = NinjaAPI()
api.add_router("/", router())

urlpatterns = [path("api/", api.urls), ...]
```

django-ninja is a Django application, so everything that is not routing is
already in place: `AppConfig.ready` loads and watches, and the middleware
opens the request scope. `router()` adds the operations, and an operation
reads with `snapshot()` like any other view:

```python
@api.get("/")
def index(request):
    db = snapshot()
    return {"host": db.host, "pool": db.pool.max_size}
```

Every operation `router()` registers carries `auth=None` except the two
diagnostics ones. That matters in a project that sets an API-wide default:

```python
api = NinjaAPI(auth=django_auth)
```

Without the explicit `auth=None`, `/healthz` would inherit that default and
report the authentication backend's health rather than the process's.

A refused diagnostics request gets **401**, which is django-ninja's
convention. Across the three Django route sets, then: 404 from the plain
views, 403 from DRF, 401 from Ninja — and in all three, no guard means no
routes.

## Async views

```python
async def index(request):
    db = snapshot()
    ...
```

`contextvars` is what Django's async path propagates, so the scope reaches an
`async def` view, a sync view running in a thread, and a `sync_to_async`
call inside either.
