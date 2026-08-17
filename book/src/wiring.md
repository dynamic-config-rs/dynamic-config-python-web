# Wiring & Lifetime

```python
from dynamic_config_web import Wiring

wiring = Wiring(config, debounce=0.25)

wiring.start()      # or `await wiring.start_async()`
...
wiring.stop()
```

`Wiring` is load-watch-stop for one configuration or a whole `ConfigGroup`.
Every adapter builds one; `setup(app, config)` builds it for you and hands it
back, and `setup(app, wiring)` takes one you already have — which is what two
applications sharing a lifecycle, and a test that wants to inspect it, both
need.

## Idempotent at both ends

Starting a wiring that is running does nothing. Stopping one that is not is
not an error. That is what makes it safe to call from a hook that fires more
than once — Django's `AppConfig.ready()` under the autoreloader, a Flask app
whose first two requests race — and it is why `stop()` belongs in a
`finally`.

## `watch=False` still loads

For a serverless function, where nothing lives long enough to watch, and for
a test that reloads by hand. The configuration is loaded and the health
surface answers; nothing observes the filesystem.

## The lease

The engine refuses a second watcher **per configuration**, so counting per
*application* would not be enough: two apps over one configuration is an
ordinary thing (a service and its admin panel; a test client opened inside
another's block). So the count lives with the configuration. The first
holder starts the watcher, the last one to leave stops it, and
`dynamic_config_web._lease.holders(config)` is what a lifetime test asserts.

## Forking

A watcher is a thread. `fork()` does not copy threads — but it does copy
memory, and the engine's "this configuration is already watched"
registration is memory. A child would therefore be refused a fresh watcher
while nothing at all was watching, and would serve the snapshot it was
forked with, silently, for ever.

Every lease re-arms itself in the child through `os.register_at_fork`: it
drops the inherited handle, which frees the registration, and starts a
watcher on this process's own thread. Nothing to configure, and it is why
`gunicorn --preload` works.

`fork()` exists on POSIX and not on Windows, so neither does the hazard.

## Where each framework starts it

| Framework | Where |
|---|---|
| FastAPI, Litestar, Quart | the lifespan — one place, paired with shutdown |
| Flask | the first request, once per process, which is after the fork |
| Django, DRF, django-ninja | `AppConfig.ready()` loads; the middleware arms the watcher |
| Robyn, django-bolt | the startup event, per process |

The deployment page has the gunicorn and uWSGI hooks for the cases where you
would rather be explicit than lazy.
