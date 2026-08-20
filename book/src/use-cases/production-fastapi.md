# A Production FastAPI Service, End to End

Every piece here is documented alone elsewhere; a production service
needs all of them at once, in the right order. This is the
order — a service with two configurations, its own lifespan, a
dedicated executor, guarded diagnostics and the three operational
routes, written once so it can be copied whole.

```python
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

import dynamic_config
from dynamic_config import ConfigGroup, DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.fastapi import config_dependency, setup
from fastapi import Depends, FastAPI


# ── The declarations — plain classes, importable from anywhere ─────────
@dataclass
class Database:
    host: str = "localhost"
    pool_size: int = 8


@dataclass
class Features:
    cache: bool = False


database = DynamicConfig(Database, key="db").file("config.toml").env("APP_")
features = DynamicConfig(Features, key="features").file("config.toml")


# ── Configuration's own thread pool ────────────────────────────────────
#
# By default the blocking half of `init_async` shares the loop's
# executor with everything else in the process. Two named threads mean a
# reload never queues behind an unrelated batch job — and a thread dump
# that says `dynamic-config-blocking-0` answers a question
# `ThreadPoolExecutor-3_0` does not.
dynamic_config.configure_executor(2)


# ── The application's OWN lifespan — setup() wraps it, never replaces it
@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await open_pool(database.current())
    app.state.pool = pool

    # Reconfigure the pool when the values move — on the loop, because
    # the pool is the loop's. (`on_change_async` is a decorator factory;
    # applied inline it answers the guard that unregisters.)
    guard = database.on_change_async("pool_size")(resize(pool))

    yield

    guard.close()
    await pool.close()


app = FastAPI(lifespan=lifespan)

# ── The wiring: loads, watches, routes, scope — one call ───────────────
#
# `setup` composes with the lifespan above: yours runs inside its
# load-then-watch bracket, so `database.current()` is already answering
# by the time `open_pool` reads it. The group makes the two
# configurations one lifecycle; `/readyz` reports per-key.
setup(
    app,
    ConfigGroup(database, features),
    debounce=0.25,
    guard=token_guard(os.environ["CONFIG_TOKEN"]),
)

db = config_dependency(database)
flags = config_dependency(features)


# ── Handlers: a `def` dependency is a dictionary lookup, no round trip ─
@app.get("/")
def index(current: Database = Depends(db), toggles: Features = Depends(flags)):
    return {"pool": current.pool_size, "cache": toggles.cache}
```

What `setup` put on the app without another line:

| Route | Answers |
|---|---|
| `/healthz` | 200, always — liveness has no configuration opinion |
| `/readyz` | ready / degraded per key, LKG-serving counts as ready |
| `/metrics` | the [Metrics Contract](https://dynamic-config-rs.github.io/metrics-contract.html)'s names, Prometheus text |
| `/_config/explain` | per-path provenance, redacted, behind the token guard |

## The deployment two-liner

uvicorn with workers forks; each worker runs the lifespan and `setup`'s
bracket itself, so each owns its watcher —
[four workers are four engines](https://dynamic-config-rs.github.io/python/pre-forking.html):

```console
$ CONFIG_TOKEN=... uvicorn app:app --workers 4
```

gunicorn with the uvicorn worker class is the same story with the
[pre-forking chapter](deployment.md)'s hook.

## Absent on purpose

No `/reload` endpoint (the watcher owns reloads; an endpoint is an
unaudited write path), no configuration values in any route this page
added (paths, kinds, counts — the values stay in the handlers that use
them), and no restart-on-failure (a failing reload leaves last-known-good
serving; see [Readiness a Load Balancer Can Use](readiness.md)).
