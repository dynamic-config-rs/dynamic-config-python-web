# Stability & Production Use

**Beta, and one distribution — with two adapters that are not.**

`dynamic-config-py-web` is Beta, like everything else in this organisation:
pre-1.0 a breaking change bumps the minor version, a patch never breaks, and
what breaks is called out in the changelog with what to change.

Seven adapters are Beta: **FastAPI, Litestar, Flask, Quart, Django** and
its two route sets, **Django REST Framework** and **django-ninja**. Their
seams are first-class, documented parts of frameworks with long release
histories.

Two are **Experimental**: **Robyn** and **django-bolt**. Both frameworks are
young — django-bolt classifies itself Alpha and requires Python 3.12 — and
what is most likely to move under them is exactly the part an adapter needs:
the process model and the request lifecycle. Concretely, for those two:

- the surface may change in a **minor** release of this package rather than
  waiting for a major one;
- `[all]` does not install them;
- the django-bolt extra is pinned `<1`, which is wrong for every other
  dependency here and right for this one: its only contract is its
  documentation.

## What the promise covers

The public surface is what `dynamic_config_web` exports plus each adapter
module's own names. A leading underscore — `_wiring`, `_lease`, `_scope` —
means the file may be reorganised: import from the package, not from those.

The **behavioural** promise is the conformance suite. Twelve cases, the same
twelve for every adapter, and a framework that cannot pass one is written
down in [Limitations](limitations.md) rather than skipped quietly.

## Versions

This distribution versions independently of the wheels it sits on. It
depends on `dynamic-config-py>=0.2` with a floor rather than a pin, for the
reason the remote wheel gives: an exact pin would make every engine patch a
forced upgrade of this package.
