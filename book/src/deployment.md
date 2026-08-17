# Deployment

The watcher is a thread, the configuration is a file, and a production
server is several processes. Three facts, and every deployment question here
comes from their combination.

## One watcher per worker, and never in the master

A watcher thread does not survive `fork()`. The engine's registration does —
it is memory — so a naive child is refused a fresh watcher *and* has no
thread: it serves the snapshot it was forked with, for ever, and nothing
says so.

Every lease re-arms itself in the child (`os.register_at_fork`), so this is
handled. What is left is a choice about *when* the first load happens.

## uvicorn

```sh
uvicorn app:app --workers 4
```

Each worker runs the lifespan, so each loads and watches its own. Nothing to
add.

`--reload` rebuilds the application on every edit; each build stops the
previous watcher and starts the next, because the lease counts holders.

## gunicorn

```sh
gunicorn app:app -k uvicorn.workers.UvicornWorker -w 4
```

The same: the ASGI lifespan runs per worker.

```sh
gunicorn app:app -w 4 --preload        # a WSGI app: Flask, Django
```

`--preload` imports the application in the master and forks. That is a
*good* thing for the load — one parse, four workers, and the snapshot is
inherited — and it means the watcher must start after the fork. The Flask
and Django adapters arm theirs on the first request in each worker, which is
after it by construction.

To be explicit instead, a `post_fork` hook is one line:

```python
# gunicorn.conf.py
def post_fork(server, worker):
    from app import wiring

    wiring.start()
```

`Wiring.start()` is idempotent, so a hook that also fires somewhere else
costs nothing.

## uWSGI

```ini
[uwsgi]
master = true
processes = 4
lazy-apps = true
```

`lazy-apps` builds the application in each worker, which sidesteps the fork
question entirely. Without it, `@postfork` is uWSGI's equivalent of the
gunicorn hook above.

## Containers, and filesystems that do not notify

A bind mount, an NFS share and some overlay filesystems register a watch
successfully and then deliver nothing. The failure is silent, so polling is
opt-in:

```python
setup(app, config, poll_interval=2.0)
```

A Kubernetes ConfigMap is the other shape: an update is not a write — the
kubelet builds a new directory and swings a symlink — and the engine watches
the *directory*, which is what makes it visible at all. Nothing extra here.

## Serverless

```python
setup(app, config, watch=False)
```

Nothing lives long enough to watch, so loading is the whole lifecycle. The
health surface still answers, and a cold start still validates.
