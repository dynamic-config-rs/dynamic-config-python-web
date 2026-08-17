# Diagnostics & Guards

The two questions every configuration bug starts with — *which layer set
this*, and *would it load at all* — are also the two answers that carry
values. So they are the two routes this package will not mount unless you
say who may call them.

```python
from dynamic_config_web import token_guard
from dynamic_config_web.fastapi import setup

setup(app, config, guard=token_guard(os.environ["CONFIG_TOKEN"]))
```

That mounts `/_config/explain/{path}` and `/_config/check`. Without a
`guard` they **do not exist** — not mounted-and-403, because a 403 tells a
scanner the route is there and the default should leave nothing to find.

## A guard is a callable

```python
Guard = Callable[[Any], bool]
```

It takes whatever the framework calls a request and answers whether it may
ask. This package never inspects the request itself, which is what lets one
signature serve every adapter — and lets you hand it your own
authentication instead:

```python
setup(app, config, guard=lambda request: request.user.is_staff)
```

Two are shipped: `never`, the default, which refuses everything; and
`token_guard(token, header="x-config-token")`, which compares a header with
`hmac.compare_digest`. An **empty** token refuses everything rather than
admitting everyone — the failure mode a `getenv` typo would otherwise have.

## They go to a thread

`explain` and `check` re-read the sources: files opened, environment parsed,
a remote layer merged. On an event loop that is real work, so the async
adapters call `explain_async` / `check_async`, which are `asyncio.to_thread`
around the same functions. `status()` and `current()` are not like this —
they are atomic loads, and a `/metrics` handler may call them per scrape.

## What `explain` shows, and what it hides

Every layer's answer for one dotted path, and which one wins. A field the
schema declared secret renders as `***` — the engine redacts it, and this
adds nothing to that.

Which is exactly why the guard exists: a value that is *not* declared secret
is printed in full, and "not secret" is a judgement the schema made months
ago.
