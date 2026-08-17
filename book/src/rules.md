# The Rules

Four sentences, each of which used to be advice in the engine's own book and
is now something this package checks. They are the reason it exists; an
integration that broke one would be worse than no integration at all.

## Read once per request

> A reload landing halfway through a request would otherwise show one
> request two configurations.

An adapter opens a **request scope** when the request begins and takes one
reading of every configuration it was set up with. `current(config)` reads
out of that scope, so the tenth call in the tenth call frame is the same
object as the first.

A scoped read with no request around it **raises** — `OutsideRequestScopeError`
— rather than quietly answering with whatever is installed at that instant.
Code that really is not serving a request says so with `latest(config)`.

## Never copy configuration into the framework's settings

`app.config.update(...)`, `settings.DATABASES[...] = ...`, a module-level
`DB = config.current()`: each of them is a copy, and a copy never reloads.
No adapter here writes into a framework's settings object, and each
framework's page says what to do instead.

## One watcher, paired with the application

A second `watch()` on one configuration is `AlreadyExists` —
two watchers on one file can only mislead. But an application object is
built more than once: `uvicorn --reload` rebuilds it on every edit, a test
suite builds one per client, and a factory may be called twice.

So the watcher is **leased**: the first holder starts it, the last one to
leave stops it, and a wiring started twice is started once. Under
`gunicorn --preload` the lease re-arms in each forked child, because a
watcher is a thread and a thread does not survive `fork()` — while the
engine's registration does, which is what would otherwise leave a worker
serving one snapshot for ever.

## Liveness is not readiness, and neither carries a value

`/healthz` answers *is this process running*. It never fails because of
configuration: a process that cannot reload should stop receiving traffic,
not be restarted into reading the same broken file.

`/readyz` answers *is it serving, and have the reloads worked*. Three
conditions, in order: nothing installed → `unavailable`; installed but the
reloads are failing → `degraded`; older than `stale_after` → `degraded`.

Neither body carries a configured value, and neither does `/metrics`.
Generations, kinds, counts, paths and seconds — the same rule the engine's
own diagnostics follow, and for the same reason: a probe is the
most-scraped, least-guarded route a service has.
