# The Request Scope

```python
from dynamic_config_web import current, get, latest

db = current(config)     # the model this request began with
db = get("db")           # the same, by section key
db = latest(config)      # what is installed *now* — unscoped
```

A `contextvars.ContextVar` holds one reading of each configuration for the
length of one request. The adapter opens it; a handler reads out of it.

## Reading through the scope

Because it answers a different question every time it is called. Two reads
either side of a reload are two configurations, and a response built from
both is a response nobody can reproduce. `current(config)` answers the same
object for the whole request, and that is the only difference.

The cost is a dictionary lookup — the engine's own read is an attribute
lookup on a cached model, and this is that model, taken once.

## What happens outside a request

It raises. `OutsideRequestScopeError`, with a message naming the
configuration and pointing at `latest()`.

Answering would mean silently doing the thing the scope
exists to prevent. Code that is genuinely not serving a request — a startup
task, a management command, a consumer — uses `latest(config)`, which says
so at the call site.

## Threads, tasks and Django's sync path

`contextvars` is propagated by all three, which is why it is the mechanism:

- an asyncio task started inside a request inherits the snapshot;
- a `def` FastAPI endpoint, which runs on a worker thread, sees it because
  `anyio` copies the context into the thread;
- a Django sync view sees it because Django's own request handling does the
  same.

A task started *before* the request does not see it, which is the correct
answer: it is not serving that request.

## A configuration the scope does not carry

A scope covers the configurations its wiring was set up with. Reading one
that is not among them raises rather than falling back — a handler reading a
configuration nobody wired is a bug in the wiring, and a fallback would hide
it until it mattered.

## In a test

```python
from dynamic_config_web import as_request

with as_request(config):
    assert view() == "db.internal"
```

`as_request` opens the same scope by hand, for a unit test that calls a
handler directly and has no adapter in the way — and for a background job
that wants one configuration for the length of a unit of work.
