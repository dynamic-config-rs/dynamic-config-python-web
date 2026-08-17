# Configuration in a Test

**The situation.** A test needs the service to behave as though `pool_size`
were 1, or as though the database host were a container the test just
started. Writing a configuration file into a fixture directory works and is
slow, and leaks between tests when it fails.

## Pin a value for one block

```python
from dynamic_config_web import pinned


def test_the_pool_is_capped(client):
    with pinned(config, pool_size=1):
        assert client.get("/status").json()["pool"] == 1

    # Outside the block, the real document is back.
    assert client.get("/status").json()["pool"] == 8
```

`pinned` sits on the engine's override layer, which is above every source,
so it wins over a file and an environment variable alike. It is undone on
the way out of the block, including when the block raises.

## The fixtures

The package ships a pytest plugin on its own entry point, so installing it
is all the setup there is:

```python
def test_something(dynamic_config_wiring, dynamic_config_pinned):
    wiring = dynamic_config_wiring(config, watch=False)
    ...
```

| Fixture | What it gives |
|---|---|
| `dynamic_config_wiring` | a started wiring, stopped when the test ends |
| `dynamic_config_pinned` | `pinned`, scoped to the test |
| `dynamic_config_request` | a request scope, for testing code that reads outside a handler |
| `dynamic_config_watchers` | how many watchers are running — for asserting one is not leaked |

`watch=False` is the default worth using: a test that does not edit files
has nothing to watch for, and a watcher thread per test is a thread per
test.

## Testing code that is not a handler

`current()` raises outside a request scope, which makes a unit test of a
helper awkward until you know the door:

```python
from dynamic_config_web import as_request


def test_the_helper():
    with as_request(db=Database(host="fixture")):
        assert build_dsn() == "postgres://fixture/app"
```

## Overriding through the framework

Each adapter also works with its framework's own mechanism, which is what
to reach for when the test is about the handler rather than the
configuration:

```python
# FastAPI
app.dependency_overrides[config_dependency(config)] = lambda: Database(host="fixture")
```

`config_dependency` answers the same object every call, which is what makes
that line work from a module that never saw the application being built.
