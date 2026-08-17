# Testing

Three doors, and none of them writes a configuration file.

## Pin a value

```python
from dynamic_config_web import pinned

with pinned(config, pool__max_size=1):
    assert client.get("/health").json()["pool"] == 1
```

The engine's own override layer, under the name a web test wants. Dotted
paths are spelled with a double underscore, because a keyword argument
cannot carry a dot. The block reloads on the way in and out, so it touches
the sources — which makes it a test tool and not something to call in a
handler.

## Open a scope without a request

```python
from dynamic_config_web import as_request

with as_request(config):
    assert view(None) == "db.internal"
```

For a unit test that calls a handler directly. Without it the scoped read
raises — so this is how a test says
*there is a request here*.

## The framework's own override

Each adapter keeps whatever the framework already has, because that is what
a team's tests are already written against:

```python
app.dependency_overrides[config_dependency(config)] = lambda: Database(host="fixture")
```

`config_dependency(config)` answers the same object every time, which is
what makes that line work from a test that never saw the application being
built.

## The fixtures

The distribution ships a pytest plugin on its own entry point — installing
it is all a suite has to do, and nothing is autouse:

| Fixture | |
|---|---|
| `dynamic_config_wiring` | a factory for wirings that are stopped when the test ends |
| `dynamic_config_pinned` | `pinned`, as a fixture |
| `dynamic_config_request` | `as_request`, as a fixture |
| `dynamic_config_watchers` | how many holders a configuration's watcher has |

`dynamic_config_wiring` is the one worth reaching for: a test that leaves a
watcher running leaves the *next* test a configuration that cannot be
watched, and the failure lands two tests away from its cause.

The base wheel's own plugin supplies `dynamic_config_workspace` and
`dynamic_config_env` alongside these.

## The conformance suite

Every adapter passes the same twelve behavioural cases, written once in
`tests/conformance/suite.py`. An adapter's own test module is a *driver* —
build an app, run its lifetime, issue a request — and nothing else.

That is what makes "every adapter behaves the same" something this
repository checks rather than something its README claims. A framework that
cannot pass a case is written down as not supporting it, here in the book,
rather than quietly skipped.
