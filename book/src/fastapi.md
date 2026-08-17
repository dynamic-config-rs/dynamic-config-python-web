# FastAPI

```sh
pip install "dynamic-config-py[fastapi]"
```

```python
from fastapi import Depends, FastAPI
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.fastapi import config_dependency, setup

config = DynamicConfig(Database, key="db").file("config.toml").env("APP_")
app = FastAPI()

setup(app, config, guard=token_guard(os.environ.get("CONFIG_TOKEN", "")))
database = config_dependency(config)


@app.get("/")
def index(db: Database = Depends(database)):
    return {"host": db.host, "pool": db.pool.max_size}
```

[`examples/01_fastapi.py`](https://github.com/dynamic-config-rs/dynamic-config-python-web/blob/main/examples/01_fastapi.py)
runs all of it.

## What `setup` does

| | |
|---|---|
| **Lifespan** | wraps the application's existing one — `setup` never replaces a lifespan you wrote. Loads with `init_async` and starts the watcher with `watch_async`, because the loop that starts a service is the loop that will answer its requests. Stops on the way out. |
| **Middleware** | one raw-ASGI middleware that opens the request scope. |
| **Routes** | `/healthz`, `/readyz`, `/metrics`, and the guarded pair — all `include_in_schema=False`, so they stay out of your OpenAPI document. |

It answers the `Wiring`, so a caller can inspect the lifecycle or stop it by
hand. `setup(app, wiring)` takes one you built yourself.

## Raw ASGI middleware, not `BaseHTTPMiddleware`

This is the one implementation detail worth knowing, because getting it wrong
looks like it works.

`BaseHTTPMiddleware` runs the downstream application in a task of its own. A
`ContextVar` set in such a middleware does **not** reach the endpoint — so
the scope would be opened, the handler would read straight through to the
engine, and every read would be a fresh one. Nothing would fail; the
guarantee would just be gone.

A raw ASGI middleware runs in the endpoint's own task, so the scope reaches
it. And a `def` endpoint, which FastAPI runs on a worker thread, sees it too
because `anyio` copies the context into the thread. Both are asserted in the
conformance suite.

## The dependency

```python
database = config_dependency(config)
```

A plain `def`: FastAPI runs a `def` dependency on a worker
thread and an `async def` one on the loop, and this is a dictionary lookup
either way — sync means a sync endpoint pays no loop round trip.

It answers **the same object every time** for one configuration, which is
what makes the test override work from a module that never saw the
application being built:

```python
app.dependency_overrides[config_dependency(config)] = lambda: Database(host="fixture")
```

## Several configurations

```python
group = ConfigGroup(database_config, cache_config)

setup(app, group)

db = config_dependency(database_config)
cache = config_dependency(cache_config)
```

Every member is in the request scope, `/readyz` reports each by key, and
`/metrics` labels each with its own. `/_config/explain` then needs to be
told which one: `?config=cache`.

## uvicorn, and `--reload`

`uvicorn --reload` rebuilds the application on every edit, and each build
runs the lifespan again — the previous watcher is stopped before the next one
starts, because the lease counts holders rather than assuming there is one.
Nothing to configure.

With `--workers N`, uvicorn forks: each worker runs its own lifespan, so each
gets its own watcher. Under `gunicorn -k uvicorn.workers.UvicornWorker` the
same is true, `--preload` included — see [Deployment](deployment.md).
