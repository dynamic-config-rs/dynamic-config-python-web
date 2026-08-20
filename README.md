<div align="center">

# dynamic-config-py-web

**Native web-framework integrations for [`dynamic-config-py`](https://github.com/dynamic-config-rs/dynamic-config-python): the wiring, the request scope, the health surface and the test doors — already written.**

[**The Book**](https://dynamic-config-rs.github.io/web/) · [The engine](https://github.com/dynamic-config-rs/dynamic-config) · [The Python binding](https://github.com/dynamic-config-rs/dynamic-config-python)

</div>

---

```sh
pip install "dynamic-config-py[fastapi]"      # or [litestar] [flask] [quart]
pip install "dynamic-config-py[django]"       # …[drf] and [ninja] for its API layers
pip install "dynamic-config-py[robyn]"        # Experimental
pip install "dynamic-config-py[django-bolt]"  # Experimental, Python 3.12+
```

```python
from fastapi import Depends, FastAPI
from dynamic_config import DynamicConfig
from dynamic_config_web.fastapi import config_dependency, setup

config = DynamicConfig(Database, key="db").file("config.toml").env("APP_")
app = FastAPI()

setup(app, config)
database = config_dependency(config)


@app.get("/")
def index(db: Database = Depends(database)):
    return {"host": db.host, "pool": db.pool_size}
```

That is the whole integration. `setup` loads before the first request,
watches while serving and stops on the way out; `/healthz`, `/readyz` and
`/metrics` are mounted; and every handler reads the configuration the
request began with.

## What it does that a dependency function does not

**One reading per request, enforced.** The engine's own book states the
rule — *read `current()` once per request and use that value for the whole
request* — because a reload landing mid-request would otherwise show one
request two configurations. Here it is a `contextvars` scope opened by the
adapter: a second read is the same object, and a read outside a request
raises instead of quietly answering with whatever is installed at that
instant.

**One watcher, however many times the app is built.** A second `watch()`
on one configuration is `AlreadyExists`. `uvicorn --reload` rebuilds the
app on every edit and a test suite builds one per client, so the watcher
is leased and counted: the first holder starts it and the last one stops
it.

**A watcher in every worker.** A watcher is a thread, and a thread does
not survive `fork()` — but the engine's registration does, so a
`gunicorn --preload` worker would be refused a new one while nothing was
watching. Every lease re-arms itself in the child.

**Liveness and readiness as different questions.** `/healthz` never fails
on configuration: a process that cannot reload should stop receiving
traffic, not be restarted into reading the same broken file. `/readyz`
is where *nothing installed*, *reloads failing* and *too stale* answer 503.

**Diagnostics only when you say so.** `/_config/explain` and
`/_config/check` are not mounted at all without a guard — not
mounted-and-403, which would tell a scanner they exist.

## The seven

| Framework | Extra | Seam | Tier |
|---|---|---|---|
| FastAPI | `[fastapi]` | `lifespan`, `Depends`, `APIRouter`, ASGI middleware | Beta |
| Litestar | `[litestar]` | `InitPlugin.on_app_init` — lifespan, `Provide`, routes, middleware | Beta |
| Flask | `[flask]` | extension in `app.extensions`, `Blueprint`, `before_request` | Beta |
| Quart | `[quart]` | the async twin, on `while_serving` | Beta |
| Django | `[django]` | `AppConfig.ready()`, middleware, `urls`, a management command | Beta |
| Django REST Framework | `[drf]` | `APIView` + a permission class | Beta |
| django-ninja | `[ninja]` | a `Router`, and the operation `auth=` | Beta |
| Robyn | `[robyn]` | startup/shutdown events, `@scoped` handlers | **Experimental** |
| django-bolt | `[django-bolt]` | `BoltAPI(lifespan=…, middleware=…)`, `Router` | **Experimental** |

Each has a page or a section in
[the book](https://dynamic-config-rs.github.io/web/) and a runnable example in
[`examples/`](https://github.com/dynamic-config-rs/dynamic-config-python-web/tree/main/examples);
all nine answer the same twelve conformance cases, and where one cannot —
Robyn's request scope — the suite says so by name rather than skipping
quietly.

## What this package is not

It is **not** a second configuration API. Every value still comes from
`dynamic-config-py`, every diagnostic is the engine's, and the adapters
add no cache, no copy and no second source of truth — `db.current()` is
still the read path, and `current()` here is that read taken once per
request.

What you may build on and find unchanged tomorrow is written down: the [Compatibility Contract](https://dynamic-config-rs.github.io/compatibility.html).

## License

MIT.
