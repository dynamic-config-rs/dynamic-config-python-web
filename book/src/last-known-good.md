# The Last-Known-Good Cache

A worker that cannot load its configuration does not start. That is the
right answer, and [the rules](rules.md) say why — but it has a cost that
shows up at exactly the wrong moment: a node reboots, the volume holding
`config.toml` has not mounted yet, and the pod crash-loops until a person
looks at it.

The engine's cache is the answer to that one case, and it is configured on
the configuration rather than on the adapter:

```python
config = (
    DynamicConfig(Database, key="db")
    .file("/etc/myapp/config.toml")
    .env("APP_")
    .cache("/var/lib/myapp/last.json", mode="redacted")
)
```

`setup(app, config)` needs no argument for it. The cache is written after
every clean load and read back only when a **cold start** fails.

## What it changes for a web service

| | without a cache | with one |
|---|---|---|
| Sources unreadable at startup | the worker fails to start | it starts on the cached document, and logs a warning |
| `/readyz` after such a start | never answers — nothing is serving | `200`, because something *is* installed |
| A reload failing later | the previous snapshot keeps serving | unchanged: a failed reload never reads the cache |

The third row is the one to hold on to. A running process already has
something better than the file on disk — the snapshot it is currently
serving — so recovery is a cold-start mechanism only.

## Redaction, and what reaches the disk

`mode="redacted"` is the default and the one to use unless you have a
reason. It writes every value the schema did *not* mark secret, and omits
the ones it did; a recovered start then has its non-secret configuration and
fails on the secrets, which is a clearer failure than a stale credential.

`mode="full"` writes everything, including secrets, and is a file with your
database password in it. `mode="fingerprint"` writes no values at all — only
enough to say *what* would have loaded — which gives the diagnosis without
the recovery.

The engine's [Last-Known-Good
Cache](https://dynamic-config-rs.github.io/last-known-good.html) chapter has
the full table and the file format.

## Readiness after a recovery

A recovered process is serving, so `/readyz` answers `200` — it is not
lying, and there is no third state between ready and not. What tells you a
recovery happened is the warning in the log, and `stale_after`:

```python
setup(app, config, stale_after=3600)
```

With that set, a process serving a document nothing has refreshed for an
hour reports `degraded`. A cold start from cache that never manages a real
load will cross it and start failing readiness, which is usually what you
want an hour into an outage.
