"""`explain` and `check` over HTTP, and who is allowed to ask.

The two diagnostics that answer the question every configuration bug
starts with — *which layer set this, and would it load at all* — are also
the two that read values back out. They redact what the schema declared
secret, and they are still the most sensitive routes an adapter can offer.

So two rules, both enforced here rather than recommended:

**A guard is required.** No adapter registers these routes unless it is
given one. Not registered-and-403: a 403 tells a scanner the route exists,
and the default should leave nothing to find.

**They go to a thread.** Unlike `status()`, these re-read the sources —
files opened, environment parsed, a remote layer merged — so on an event
loop they are real work. The async twins exist for exactly that, and the
ASGI adapters use them.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import DynamicConfig

__all__ = [
    "Guard",
    "check",
    "check_async",
    "explain",
    "explain_async",
    "never",
    "token_guard",
]

#: What decides whether a request may ask. Takes whatever the framework
#: calls a request — this package never inspects it, the guard does.
Guard = Callable[[Any], bool]


def never(request: Any) -> bool:
    """Refuses everything. The default, and the reason routes stay unbuilt."""
    del request

    return False


def token_guard(token: str, header: str = "x-config-token") -> Guard:
    """A guard that compares a header against a shared secret.

        setup(app, config, guard=token_guard(os.environ["CONFIG_TOKEN"]))

    Deliberately the *simplest* thing that is not "anyone": a service with
    real authentication should pass a guard that asks its own — this is
    for the internal tool, the staging environment and the operator with
    `curl`.

    The comparison is constant-time, and a missing or empty configured
    token refuses everything rather than accepting everything, which is
    the failure mode a `getenv` typo would otherwise produce.
    """
    import hmac

    if not token:
        return never

    # Compared as bytes, not as `str`. `hmac.compare_digest` raises
    # `TypeError` on a `str` containing a non-ASCII character, and the
    # offered value comes straight off the wire — Django decodes header
    # bytes as latin-1, so `x-config-token: \xff` would otherwise turn an
    # unauthenticated 401 into a 500. Encoding both sides first makes a
    # hostile byte a mismatch, which is what it is.
    wanted = token.encode("utf-8")

    def allowed(request: Any) -> bool:
        offered = _header(request, header)

        if offered is None:
            return False

        return hmac.compare_digest(offered.encode("utf-8", "surrogateescape"), wanted)

    return allowed


def _header(request: Any, name: str) -> str | None:
    """One header out of whatever object a framework calls a request.

    Six frameworks, five spellings of "the headers": a mapping on
    `.headers` (Starlette, Flask, Litestar, Quart), Django's
    `HttpRequest.headers`, and Robyn's plain dict. Rather than an
    isinstance ladder over types this package must not import, it asks the
    object what it has.
    """
    headers = getattr(request, "headers", None)

    if headers is None:
        return None

    getter = getattr(headers, "get", None)

    if getter is None:
        return None

    value = getter(name)

    if value is None:
        # Django exposes `HTTP_X_CONFIG_TOKEN` in `META` for anything its
        # `headers` mapping does not normalise the same way.
        meta = getattr(request, "META", None)

        if isinstance(meta, dict):
            value = meta.get("HTTP_" + name.upper().replace("-", "_"))

    return None if value is None else str(value)


def explain(config: DynamicConfig[Any], path: str) -> str:
    """Every layer's answer for one dotted path, rendered as a table.

    Secrets render as `***`: the engine redacts what the schema declared,
    and this adds nothing to that — but it is why the guard exists, since
    a value that is *not* declared secret is printed in full.

    Re-reads the sources. Off an event loop, use :func:`explain_async`.
    """
    return str(config.explain(path))


async def explain_async(config: DynamicConfig[Any], path: str) -> str:
    """:func:`explain`, on a worker thread."""
    return await asyncio.to_thread(explain, config, path)


def check(config: DynamicConfig[Any]) -> dict[str, Any]:
    """Would it load, and does anything supply a key it does not declare.

    The body is the report's fields rather than its rendering: a JSON
    endpoint is read by a program more often than by a person, and the
    rendered table is there under `rendered` for the person.

    Two questions, not one. The engine's own `check()` resolves the layers
    and compares the keys — it does not build the model, so a document
    whose `port` is the string `"8080"` passes it and then refuses to load.
    So this also calls `load()`, which validates and installs nothing, and
    reports that under `loads`. `clean` means both.
    """
    report = config.check()
    failure = report.failure
    loads = True

    try:
        config.load()
    except Exception as refused:
        loads = False
        # The engine's own failure if it had one, and otherwise the
        # validation error, which is the half `check()` cannot see.
        failure = failure or f"{type(refused).__name__}: {refused}"

    return {
        "key": report.key,
        "clean": report.is_clean and loads,
        # Whether the document would actually build the model, as against
        # whether its keys resolve.
        "loads": loads,
        # `False` here means the report compared nothing, which an empty
        # `unknown` would otherwise read as an all-clear it never earned.
        "unknown_checked": report.unknown_checked,
        "resolved": [
            # Paths and origins, never values — the same table `str(report)`
            # prints, in the shape a program reads.
            {"path": item.path, "origin": str(item.origin)}
            for item in report.resolved
        ],
        "unknown": [
            {"path": key.path, "suggestion": key.suggestion} for key in report.unknown
        ],
        "failure": failure,
        "rendered": str(report),
    }


async def check_async(config: DynamicConfig[Any]) -> dict[str, Any]:
    """:func:`check`, on a worker thread."""
    return await asyncio.to_thread(check, config)
