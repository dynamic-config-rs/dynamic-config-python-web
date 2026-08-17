# Flask

```sh
pip install "dynamic-config-py[flask]"
```

```python
from flask import Flask
from dynamic_config import DynamicConfig
from dynamic_config_web import token_guard
from dynamic_config_web.flask import DynamicConfigExtension, snapshot

config = DynamicConfig(Database, key="db").file("config.toml").env("APP_")
app = Flask(__name__)

DynamicConfigExtension(
    app, config, guard=token_guard(os.environ.get("CONFIG_TOKEN", ""))
)


@app.get("/")
def index():
    db = snapshot()
    return {"host": db.host, "pool": db.pool.max_size}
```

[`examples/03_flask.py`](https://github.com/dynamic-config-rs/dynamic-config-python-web/blob/main/examples/03_flask.py)
runs all of it.

## The habit this replaces

```python
# Don't.
app.config.update(DATABASE_HOST=config.current().host)
```

A value copied into `app.config` at startup is frozen at the moment it was
copied. The file reloads, the engine installs a new model, and `app.config`
keeps answering with the old one — forever, and silently.

**The extension writes nothing into `app.config`.** `snapshot()`
is the read, and it answers the model *this request* began with.

## What the extension does

| | |
|---|---|
| **`before_request`** | opens the request scope, and arms the watcher on the first request this process serves. |
| **`teardown_appcontext`** | closes the scope, whatever the view did. |
| **Blueprint** | `/healthz`, `/readyz`, `/metrics` and the guarded pair, registered on the application. |

The factory shape works too:

```python
configuration = DynamicConfigExtension(target=config)


def create_app():
    app = Flask(__name__)
    configuration.init_app(app)

    return app
```

## When the watcher starts

WSGI has no lifespan. There is no moment that is *after the fork* and *before
the first request* for a library to hook — so the extension arms on the first
request in each process, which is after the fork by construction.

That is what makes it correct under `gunicorn --preload`, uWSGI without
`lazy-apps`, and `flask run` alike, with nothing to configure. The cost is
one attribute read per request after the first.

Two other choices, for deployments that know better:

```python
DynamicConfigExtension(app, config, start="eager")   # single process only
DynamicConfigExtension(app, config, start="manual")  # you call .start()
```

`"eager"` arms in `init_app`, which is **wrong** under `--preload` — the
master would be the one watching, and the workers would inherit a dead
handle. `"manual"` is for the gunicorn `post_fork` hook that would rather be
explicit:

```python
# gunicorn.conf.py
def post_fork(server, worker):
    from myapp import configuration

    configuration.start()
```

## Reading in a template, a form, a helper

`snapshot()` needs no `request` argument — it reads through `contextvars`, so
it works the same three frames down, in a Jinja global, or inside a function
a view called. `snapshot("cache")` names one configuration of a group.

Outside a request it raises `OutsideRequestScopeError`. A CLI command, a
Celery task or a startup hook wants `dynamic_config_web.latest(config)`,
which says that is what it meant.
