# Diagnostics Behind a Token

**The situation.** A value in production is not what you expect. You want
to know which layer set it — the file, the environment, a `.env`, an
override — without shelling into the container, and without exposing
configuration to anyone who finds the URL.

## Turn them on

```python
from dynamic_config_web import token_guard

setup(app, config, guard=token_guard(os.environ["CONFIG_TOKEN"]))
```

Without a `guard=`, the two routes below **are not registered at all**. Not
registered-and-403: a 403 tells a scanner the route exists.

```console
$ curl -H "x-config-token: $CONFIG_TOKEN" \
    https://api.internal/_config/explain/database.pool.max_size
database.pool.max_size = 32

layer        source                              value
default                                          8
file         in /etc/myapp/config.toml           16
env          APP_DATABASE_POOL_MAX_SIZE          32   ← winner
```

The answer is the last column: an environment variable is overriding the
file, and the deployment that set it is the thing to look at.

## The other question

```console
$ curl -H "x-config-token: $CONFIG_TOKEN" https://api.internal/_config/check
{"db": {"clean": true, "loads": true, "unknown": [], "failure": null}}
```

`check` answers *would this load at all*, and reads both halves: the keys
resolve, **and** the model builds. A document whose `port` is the string
`"8080"` resolves fine and then refuses to load, so `loads` is the field
that catches it before a reload does.

## What a token is and is not

`token_guard` compares a header in constant time, and an empty configured
token refuses everything rather than accepting it — the failure mode a
`getenv` typo would otherwise produce.

It is the simplest thing that is not "anyone". A service with
real authentication should pass a guard that asks its own:

```python
def only_operators(request) -> bool:
    return request.user.is_authenticated and request.user.has_perm("ops.debug")

setup(app, config, guard=only_operators)
```

In a DRF or django-ninja project the framework's own layer does this, and
the guard is what it consults — see [Django, DRF &
Ninja](../django.md).

## Secrets

`explain` renders values, and that is what it is for. It redacts what the
schema declared secret, and **only** that:

```python
@dataclass
class Database:
    host: str
    password: str = field(metadata={"secret": True})
```

A field nobody marked is printed in full. That, rather than the redaction,
is why the guard exists.
