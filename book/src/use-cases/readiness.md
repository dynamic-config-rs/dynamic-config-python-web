# Readiness a Load Balancer Can Use

**The situation.** A rolling deployment. Kubernetes will send traffic to a
pod as soon as its readiness probe passes, and take it out again when the
probe fails. You want that decision to track whether the pod can actually
serve — not whether its process is running.

## Wire both probes at different things

```python
setup(app, config, stale_after=3600)
```

```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 8000 }
  periodSeconds: 10
readinessProbe:
  httpGet: { path: /readyz, port: 8000 }
  periodSeconds: 5
```

`/healthz` never fails on configuration. That is the property that matters
here: a pod whose reloads are failing is serving the last good document
perfectly well, and restarting it would only make it read the same broken
file again. Point liveness at it and a configuration problem never turns
into a crash loop.

`/readyz` fails on three things, and each maps to a decision the load
balancer should make:

| `status` | Code | What happened | What the LB does |
|---|---|---|---|
| `unavailable` | 503 | the first load never succeeded | never sends traffic to this pod |
| `degraded` | 503 | reloads are failing, or nothing has landed for `stale_after` | stops sending, keeps the pod |
| `ok` | 200 | serving, and the reloads are working | sends traffic |

## What a rolling deploy then does

A pod with a broken `ConfigMap` fails its readiness probe from the start,
never receives a request, and the rollout stalls with the old pods still
serving. That is the outcome you want: the deploy stops, and nothing has
served a 500.

Under an ASGI adapter the pod does not even get that far — the worker fails
to start, because the load happens in the lifespan. Flask's arms on the
first request instead, so a Flask pod *starts* and answers `unavailable`
until the file is fixed. Both are safe; they differ in where you read the
failure. See [Deployment](../deployment.md).

## Do not put configuration in a probe body

The bodies carry generations, failure kinds, counts and paths. They never
carry a configured value, and neither should anything you add beside them —
a probe endpoint is unauthenticated by design, and a load balancer's logs
are not a place to put a database host.

To read values over HTTP, use the [diagnostics
routes](diagnostics.md), which are guarded.

## Several configurations

```python
group = ConfigGroup(database, cache, queue)

setup(app, group)
```

`/readyz` reports each member by key, and the worst one decides the status
code. A service is not ready because two of its three configurations
loaded.
