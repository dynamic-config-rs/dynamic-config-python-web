# Web Integrations

`dynamic-config-py` resolves configuration and hands a program a validated
model that a file edit can replace while the process serves. Everything a
*web* application needs around that — where the watcher starts, how a
handler reads without tearing, what `/readyz` should say when a reload has
been failing for an hour, how a test pins a value — was prose in that
package's book. This one is that prose, installed:

```sh
pip install "dynamic-config-py[fastapi]"
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
    return {"host": db.host}
```

`setup` is the whole integration. It loads before the first request, watches
while serving and stops on the way out; it gives each request one reading of
the configuration; and it mounts `/healthz`, `/readyz` and `/metrics`.

## What is in the box

| | |
|---|---|
| **Lifecycle** | load, watch, stop — once per process, paired with the application, and re-armed in a forked worker |
| **A request scope** | one reading per request, and a read outside a request that raises rather than answering |
| **Health** | `/healthz` for liveness, `/readyz` for *serving something and the reloads are working* |
| **Metrics** | the engine's twelve series, over one configuration or a whole `ConfigGroup` |
| **Diagnostics** | `explain` and `check` over HTTP, behind a guard, off a thread — and not mounted at all without one |
| **Testing** | `pinned()`, `as_request()`, a pytest plugin, and each framework's own override mechanism |

## The nine adapters

Each is one extra and one module named after it; importing
`dynamic_config_web` imports none of them.

| | install | the seam it uses |
|---|---|---|
| [FastAPI](fastapi.md) | `dynamic-config-py[fastapi]` | lifespan, `Depends`, `APIRouter` |
| [Litestar](litestar.md) | `dynamic-config-py[litestar]` | `InitPlugin.on_app_init` |
| [Flask](flask.md) | `dynamic-config-py[flask]` | extension object, blueprint |
| [Quart](quart.md) | `dynamic-config-py[quart]` | `while_serving`, extension object |
| [Django](django.md) | `dynamic-config-py[django]` | `AppConfig.ready`, middleware, URLconf |
| [Django REST Framework](django.md#django-rest-framework) | `dynamic-config-py[drf]` | `APIView`, a permission class |
| [django-ninja](django.md#django-ninja) | `dynamic-config-py[ninja]` | `Router`, operation `auth=` |
| [Robyn](robyn.md) | `dynamic-config-py[robyn]` | startup/shutdown handlers, `SubRouter` |
| [django-bolt](django-bolt.md) | `dynamic-config-py[django-bolt]` | `BoltAPI(lifespan=…, middleware=…)` |

Robyn and django-bolt are **Experimental** — their frameworks are young and
their process models are the part most likely to move. The other seven are
Beta, like everything else in this organisation.

Django, DRF and django-ninja share one page and one adapter underneath:
Django supplies the lifecycle and the request scope, and the other two are
route sets over it.

## What it is not

Not a second configuration API. Every value still comes from
`dynamic-config-py`; every diagnostic is the engine's; nothing here caches,
copies or re-validates. `config.current()` is still the read — and inside a
request, `dynamic_config_web.current(config)` is that read taken **once**.
