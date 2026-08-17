# Health, Readiness & Metrics

Three routes, three questions, and the reason they are three: an operator
who cannot tell them apart either restarts a process that was serving fine
or keeps routing to one that has been stale since Tuesday.

## `/healthz` — liveness

Always 200. Configuration has no say in it.

A configuration that will not reload is a reason to stop *routing* to a
process, never a reason to have it killed: the restart reads the same broken
file, fails to start at all, and turns a degraded service into an outage.

## `/readyz` — readiness

```json
{
  "status": "degraded",
  "configs": {
    "db": {
      "installed": true,
      "generation": 4,
      "healthy": false,
      "consecutive_failures": 3,
      "stale_for": 812.4,
      "last_reason": "file changed",
      "last_failure": {"kind": "invalid", "path": "pool.max_size", "seconds_ago": 12.1},
      "ready": false
    }
  },
  "problems": ["db: 3 reloads refused"]
}
```

Three conditions, in the order they matter:

| Condition | `status` | Code |
|---|---|---|
| nothing installed — the first load never succeeded | `unavailable` | 503 |
| installed, and the reloads since have been failing | `degraded` | 503 |
| installed, healthy, but no install for `stale_after` seconds | `degraded` | 503 |
| otherwise | `ok` | 200 |

`stale_after` is off by default: a configuration nobody edits is not a
problem, and a service whose settings change quarterly should not page
anybody at midnight.

A group reports every member by key, and the worst member decides the code —
so a page says *which* configuration, which is the difference between an
alert somebody can act on and one they have to investigate.

## `/metrics` — the engine's own series

```python
from dynamic_config_web import CONTENT_TYPE, metrics_body

body = metrics_body(group)          # or metrics_body(db, cache)
```

Twelve series per configuration that has a remote store, six for one that
does not, with the names the engine defines — they end up in dashboards and
alert rules, so nothing here renames or filters them. Built per scrape and
thrown away; the numbers are atomic loads, so a handler needs no cache in
front of it.

`metrics_body` takes a `ConfigGroup` as well as configurations, which the
engine's `Exposition` does not: it iterates the members and labels each with
its own key.

## What none of them carries

A value. Generations, counts, kinds, paths, seconds and reasons — nothing
out of the document, not even in `last_failure`, whose message routinely
would. `/healthz` and `/readyz` are unauthenticated in most deployments and
`/metrics` is scraped by something that keeps everything for a year.

Values live in `explain` and `check`, behind a guard, on the next page.
