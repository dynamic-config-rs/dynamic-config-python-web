# Limitations

What this package does not do, and why — so the absence is a decision you
can read rather than a gap you discover.

## A long-lived connection is not a request

WebSockets and server-sent events get no scope. A connection that lives for
an hour pinned to the configuration it opened with would be the *opposite*
of what this package is for, and pinning per message is a decision only the
application can make. `latest(config)` is the honest read there, and
`stream_events(config)` is how a connection follows reloads.

## A refused reload cannot wake anything

The engine bumps a generation when a document *installs*. A load that
installed nothing does not, so nothing can be notified of it: a `/readyz`
scrape notices a refusal because it reads `status()`, and an event stream
notices it only if it was given `failure_poll`. There is no push.

That is the engine's shape rather than this package's, and it is documented
there too.

## No `/config/snapshot`

`explain` renders one path and redacts a declared secret; a whole-document
route would render everything, and the redaction surface is much larger. The
payoff — seeing the resolved document — is real, and small next to the risk
of one field nobody marked secret.

## No metrics ecosystem

`metrics_body` answers a string. It does not register anything with
`prometheus_client`, `opentelemetry` or a statsd client, because choosing one
for the service that imports this would be choosing for its whole process.
The engine makes the same choice for the same reason.

## Frameworks that are not here

Starlette without FastAPI, aiohttp, Sanic, Tornado, Bottle, Pyramid, Falcon.
Each would be another adapter with another conformance driver, and the nine
here already cover what the request volume asks for. The shared core is
public and small — `Wiring`, `scope`, `readiness`, `metrics_body` — so an
integration for anything else is about forty lines, and one that proves
itself is welcome upstream.

## Robyn cannot enforce the scope for you

Every other adapter opens the request scope in middleware. Robyn calls its
before-request middleware and then calls the handler, and a `ContextVar` set
in the first is not visible in the second — so [its adapter](robyn.md) puts
the scope on a `@scoped` decorator the handler carries, and a handler that
forgets it raises rather than reading unscoped.

This is the one conformance case that is skipped rather than passed, and it
is skipped by name (`scope_is_automatic = False`) so that the suite records
which framework cannot do it.

## Flask arms its watcher on the first request

WSGI has no moment that is both after a fork and before the first request, so
the [Flask](flask.md) extension arms there. The consequence: a Flask worker
whose configuration will not load **starts** and then answers 503 on
`/readyz`, where an ASGI worker would have failed to start at all. Where that
matters, `start="manual"` from a `post_fork` hook gets the earlier failure
back.

## Per-tenant configuration

Choosing *which* configuration a request reads, from the request itself, is
routing rather than configuration, and the shapes people want are too
different to guess at. The scope takes the configurations the wiring was
given; a multi-tenant service can open its own scope per request with the
tenant's configuration in it.
