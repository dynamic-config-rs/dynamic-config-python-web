# Quick Start

```sh
pip install "dynamic-config-py[fastapi]"
```

```python
from dataclasses import dataclass

from fastapi import Depends, FastAPI

from dynamic_config import DynamicConfig
from dynamic_config_web.fastapi import config_dependency, setup


@dataclass
class Database:
    host: str = "localhost"
    port: int = 5432


config = DynamicConfig(Database, key="db").file("config.toml").env("APP_")

app = FastAPI()
setup(app, config)                     # lifecycle + request scope + routes
database = config_dependency(config)


@app.get("/")
def index(db: Database = Depends(database)) -> dict[str, str]:
    return {"host": db.host}
```

Run it — `uvicorn main:app` — and you have:

| | |
|---|---|
| `GET /` | your handler, reading one pinned snapshot per request |
| `GET /healthz` | 200 while the process lives |
| `GET /readyz` | 200 serving, 503 when nothing loaded or reloads are failing |
| `GET /metrics` | the engine's series, Prometheus text |

`setup` did four things: loaded before the first request (a broken
document fails startup, not traffic), started the watcher and stops it on
shutdown, opened a request scope around every request, and mounted the
routes above. Every adapter here is those same four things
through its own framework's seams — the [Introduction](introduction.md)
has the table of nine.

Edit `config.toml` while it serves: the *next* request answers with the
new document, and no request ever straddles the change — that is the
request scope, and [The Rules](rules.md) is the page that spells out
what it promises and what it refuses to.

Diagnostics (`/_config/explain`, `/_config/check`) exist only when you
pass a guard: `setup(app, config, guard=token_guard("s3cret"))` — see
[Diagnostics](diagnostics.md).
