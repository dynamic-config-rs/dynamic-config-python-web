# django-bolt

> **Experimental.** django-bolt is 0.10.x, classified Alpha by its authors,
> needs Python 3.12, and serves from Rust. The adapter passes all twelve
> conformance cases. Its surface is small — the three seams django-bolt
> documents, and nothing private — and its extra carries `<1`.
> See [Stability](stability.md).

```sh
pip install "dynamic-config-py[django-bolt]"        # Python 3.12+
```

```python
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.django_bolt import api, snapshot

config = DynamicConfig(Database, key="db").file("config.toml").env("APP_")

bolt = api(config, guard=token_guard(os.environ.get("CONFIG_TOKEN", "")))


@bolt.get("/")
async def index():
    db = snapshot()
    return {"host": db.host, "pool": db.pool.max_size}
```

[`examples/07_django_bolt.py`](https://github.com/dynamic-config-rs/dynamic-config-python-web/blob/main/examples/07_django_bolt.py)
runs all of it.

## A factory, not `setup(app, config)`

django-bolt takes its lifespan and its middleware as **constructor
arguments**. A wiring added after the `BoltAPI` exists would have to reach
past the API into private attributes, which is not a thing to build on a
0.10 library — so `api()` builds the `BoltAPI` for you and passes every other
keyword straight through:

```python
bolt = api(config, prefix="/v1", django_middleware=True, middleware=[Timing])
```

For an application that must build its own, the three pieces are public and
composing them by hand is exactly what `api()` does:

```python
from django_bolt import BoltAPI
from dynamic_config_web.django_bolt import ScopeMiddleware, lifespan, router

bolt = BoltAPI(lifespan=lifespan(config), middleware=[ScopeMiddleware])
bolt.include_router(router(wiring, guard=guard))
```

## Django first

django-bolt is a Django application: `settings` must be configured and
`django.setup()` called before a `BoltAPI` exists. That is django-bolt's
requirement, not this adapter's, and it is why the example configures Django
at the top.

The [Django adapter](django.md) and this one are independent — a project can
use either, or both, and each keeps its own wiring.

## The middleware

django-bolt's middleware is Django's shape: `get_response` in, a response
out, awaited inside the middleware's own coroutine. A `contextvars` token set
there **is** visible to the handler, so the scope goes where it goes in every
other adapter — in middleware, with nothing for the caller to remember.
([Robyn](robyn.md) is the one framework where that does not hold.)

## Response annotations

The routes this adapter registers are annotated `-> Any`.
django-bolt validates a response against its handler's return annotation, and
`-> Response` makes it check the *body* against the `Response` class — which
a dict body then fails. `Any` is the annotation that says "this handler
builds its own response".

Worth knowing if you write your own health route beside these.
