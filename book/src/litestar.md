# Litestar

```sh
pip install "dynamic-config-py[litestar]"
```

```python
from litestar import Litestar, get
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.litestar import DynamicConfigPlugin, NamedDependency

config = DynamicConfig(Database, key="db").file("config.toml").env("APP_")


@get("/")
async def index(db: NamedDependency[Database]) -> dict[str, object]:
    return {"host": db.host, "pool": db.pool.max_size}


app = Litestar(
    [index],
    plugins=[
        DynamicConfigPlugin(
            config, guard=token_guard(os.environ.get("CONFIG_TOKEN", ""))
        )
    ],
)
```

[`examples/02_litestar.py`](https://github.com/dynamic-config-rs/dynamic-config-python-web/blob/main/examples/02_litestar.py)
runs all of it.

## What the plugin does

`InitPlugin.on_app_init` is the seam Litestar gives a library, and the plugin
adds four things through it:

| | |
|---|---|
| **Lifespan** | appended to `app_config.lifespan`. Loads with `init_async`, watches, and stops on the way out. |
| **Dependency** | one `Provide` per configuration, keyed by the configuration's own key — or by `dependency_key=` if you would rather name it. |
| **Routes** | a `Router` carrying `/healthz`, `/readyz`, `/metrics` and the guarded pair, all `include_in_schema=False`. |
| **Middleware** | one raw-ASGI middleware that opens the request scope. |

## The dependency arrives by name

`NamedDependency[Database]` on a parameter called `db` asks for the
dependency named `db` — which is what the plugin registered for a
configuration whose key is `db`.

A bare `db: Database` still resolves, and Litestar 2.23 and later print a
deprecation warning about the inferred form; Litestar 3 removes it. The
marker is Litestar's own, re-exported from the adapter so one import line
covers both halves — and on a Litestar older than 2.23 the name is a no-op
annotation, so the same handler works either way.

## `use_cache=False`

The provider the plugin registers is built with `use_cache=False`, and that
is load-bearing rather than cautious.

Litestar's dependency cache is per **application**, not per request. A cached
provider would be called once and its value reused for the life of the
process — which is exactly the frozen-configuration bug this package exists
to prevent, and it would look like it worked. Litestar calls an uncached
provider once per request already, so there is nothing to gain and a
guarantee to lose.

## Several configurations

```python
group = ConfigGroup(database_config, cache_config)

app = Litestar([index], plugins=[DynamicConfigPlugin(group)])
```

Each member gets its own dependency, under its own key:

```python
@get("/")
async def index(
    db: NamedDependency[Database], cache: NamedDependency[Cache]
) -> dict[str, object]: ...
```

`/readyz` reports each by key, `/metrics` labels each with its own, and
`/_config/explain` takes `?config=cache` to say which one it means.

## Where the routes go

`path="/"` by default, so the health routes sit at the root. Under a prefix:

```python
DynamicConfigPlugin(config, path="/internal")
```

gives `/internal/healthz` and the rest.
