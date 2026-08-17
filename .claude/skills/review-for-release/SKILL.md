---
name: review-for-release
description: Use before cutting a release of dynamic-config-py-web, or when asked to review the whole repository — the checks that `just check` does not do, in the order that finds problems fastest.
---

# Reviewing before a release

Run `just check` first — ruff, `mypy --strict`, the core suite, every
adapter whose framework is installed, every example. Everything below is
what that does not catch.

## The checks that have caught real things here

**Run `just adapters` with every framework installed, not just the one you
touched.** The conformance suite is shared: a change to `suite.py` or to
`_scope`/`_lease` is a change to eight drivers at once, and the one that
breaks is rarely the one being worked on.

**Grep the adapters for `BaseHTTPMiddleware`, and for any middleware base
class that runs the downstream app in its own task.** A `ContextVar` set
there does not reach the handler, so the scope would be opened and quietly
not be there. Every ASGI adapter uses raw ASGI middleware for that reason.

**Check that no probe renders a value.** `readiness()` and `metrics_body()`
carry generations, kinds, counts and paths. A new field that reads a
*model* is the failure this repository is most exposed to, and
`case_metrics_render_and_carry_no_value` only checks the strings it knows
about.

**Check that a new route behind a guard is not mounted without one.** The
rule is identity — `guard is None or guard is never` — because a caller's
own `def never(request)` is a decision, not the default. Both diagnostics
conformance cases exist for this.

**Read every `except Exception` in an adapter.** The only one that belongs
is in `_diagnostics.check()`, where any refusal *is* the answer. Anywhere
else it hides a framework changing under the adapter.

**Ask what each Experimental adapter has actually been run against.** Robyn
and django-bolt move; the extras pin what was tested (`robyn>=0.88`,
`django-bolt>=0.10,<1`). If the CI row still passes against a newer version
than the floor claims, say so in the changelog rather than widening
silently.

## Packaging, which is easy to break invisibly

**`import dynamic_config_web` must import no framework.** `just core` in an
environment with the frameworks *installed* still proves it, because the
check is a subprocess `sys.modules` read — but only if the new adapter has
a row in `tests/test_packaging.py::ADAPTERS`.

**Every extra in this `pyproject.toml` needs a matching name in
`dynamic-config-python`'s**, or `dynamic-config-py[newframework]` resolves
to nothing.

**Raising an extra's floor past a release people are on is breaking.** That
is a minor bump pre-1.0 and a line in the changelog, not a patch.

## The release itself

`RELEASING.md` is the procedure. The one thing it cannot check for you: the
`dynamic-config-py>=…` floor has to be *on PyPI already*.
`./scripts/release-web.sh --check minor` refuses that case by name.
