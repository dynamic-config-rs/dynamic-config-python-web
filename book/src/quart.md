# Quart

```sh
pip install "dynamic-config-py[quart]"
```

```python
from quart import Quart
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.quart import DynamicConfigExtension, snapshot

config = DynamicConfig(Database, key="db").file("config.toml").env("APP_")
app = Quart(__name__)

DynamicConfigExtension(
    app, config, guard=token_guard(os.environ.get("CONFIG_TOKEN", ""))
)


@app.get("/")
async def index():
    db = snapshot()
    return {"host": db.host, "pool": db.pool.max_size}
```

[`examples/04_quart.py`](https://github.com/dynamic-config-rs/dynamic-config-python-web/blob/main/examples/04_quart.py)
runs all of it.

## The same API as Flask, with one difference

Everything on the [Flask](flask.md) page applies here — the extension object,
`snapshot()`, the blueprint, and above all the rule that **nothing goes into
`app.config`**. The API is identical, so a service migrating
from Flask to Quart changes its `import` line and nothing else.

The difference is where the watcher starts. Quart is ASGI, so it has
`while_serving`: a moment after the workers exist and before the first
request, and again on the way down. That is where the watcher belongs, and it
is the thing WSGI cannot offer.

```python
DynamicConfigExtension(app, config, start="serving")   # the default
```

| `start` | when it arms | for |
|---|---|---|
| `"serving"` | `while_serving`, before the first request | every ordinary deployment |
| `"first-request"` | the first request each process serves | a Quart app mounted inside something that never runs a lifespan |
| `"manual"` | you call `await extension.start()` | a hook that would rather be explicit |

Because the load happens in the lifespan, a Quart worker that cannot load its
configuration **fails to start** rather than serving 503s — which is the
right answer, and the same one FastAPI and Litestar give.

## Sync views

Quart runs a `def` view in a worker thread and copies the context into it, so
`snapshot()` works there as well as in an `async def` one. The scope is
opened in `before_request` either way.
