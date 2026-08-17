# Robyn

> **Experimental.** Robyn's server is Rust, its workers are processes it
> starts itself, and its middleware pipeline is not a place a `contextvars`
> token survives. The adapter passes eleven of the twelve conformance cases —
> the twelfth is skipped for a reason given below — and it is the one whose
> shape may still change. See [Stability](stability.md).

```sh
pip install "dynamic-config-py[robyn]"
```

```python
from robyn import Robyn
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.robyn import setup, scoped, snapshot

config = DynamicConfig(Database, key="db").file("config.toml").env("APP_")
app = Robyn(__file__)

setup(app, config, guard=token_guard(os.environ.get("CONFIG_TOKEN", "")))


@app.get("/")
@scoped
async def index(request):
    db = snapshot()
    return {"host": db.host, "pool": db.pool.max_size}
```

[`examples/06_robyn.py`](https://github.com/dynamic-config-rs/dynamic-config-python-web/blob/main/examples/06_robyn.py)
runs all of it.

## `@scoped` is not optional

Every other adapter here opens the request scope in middleware, because in
every other framework the middleware and the handler share a context. Robyn
calls its before-request middleware and then calls the handler: a
`ContextVar` set in the first is **not** visible in the second.

A scope opened there would silently not be there, so this adapter puts it
where it does hold — around the handler itself.

```python
@app.get("/")     # the route decorator on top…
@scoped           # …and this underneath, so Robyn registers the wrapper
async def index(request): ...
```

A handler that forgets it raises `OutsideRequestScopeError` from
`snapshot()`, rather than quietly reading a different generation halfway
through. That is the trade this package makes everywhere: a loud absence
over a quiet inconsistency.

`@scoped` handles sync and async handlers alike.

## What `setup` does

| | |
|---|---|
| **`startup_handler`** | loads with `init_async` and starts the watcher — in each worker process. |
| **`shutdown_handler`** | stops it. |
| **`SubRouter`** | `/healthz`, `/readyz`, `/metrics` and the guarded pair, included into the app. |

It answers the `Wiring`, and registers it as this process's — which is what
`@scoped` and `snapshot()` read, since a Robyn handler is given a request and
nothing else.

## Processes

`app.start(processes=N)` starts N worker processes and each runs the startup
handler, so each ends up watching its own files. That is what you want, and
the lease makes it safe; under a pre-forking start the wiring re-arms in the
child.

## What the conformance suite skips

One case — *a scoped read outside a request is refused automatically* —
asserts that the adapter can enforce the scope for the caller. Robyn's
adapter cannot, because the enforcement lives on a decorator the caller
applies. The suite records that as `scope_is_automatic = False` and skips
that one case by name. The eleven that describe what a request actually sees
all run.
