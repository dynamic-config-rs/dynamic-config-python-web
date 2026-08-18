"""One configuration per request, taken once and held for its length.

The rule every integration in the Python book states, and the only one it
could not enforce:

    read `current()` once per request and use that value for the whole
    request — a reload landing halfway through would otherwise show one
    request two configurations.

Written as advice it is followed until somebody adds a second
`config.current()` three call frames down. Written here it is checked: an
adapter opens a scope when the request begins, and
:func:`current` reads out of that scope rather than out of the engine.
A read with no scope around it raises instead of quietly answering with
whatever is installed at that instant.

`contextvars` rather than thread-local storage, because all three of the
shapes this package has to serve propagate it: an asyncio task, a worker
thread (WSGI, and FastAPI's `def` endpoints), and a Django sync view. A
task started inside a request inherits the snapshot; a task started before
it does not, which is the correct answer in both cases.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, TypeVar

from ._errors import OutsideRequestScopeError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dynamic_config import ConfigGroup, DynamicConfig

M = TypeVar("M")

__all__ = ["active", "current", "enter", "get", "latest", "leave", "scope"]

#: The snapshot this request began with: every configuration it may read,
#: by the object itself and by its key.
#:
#: `None` rather than an empty mapping for "no request", because the two
#: are different: a request that reads nothing still has a scope, and a
#: read outside one is a mistake worth naming.
_SNAPSHOT: ContextVar[_Snapshot | None] = ContextVar(
    "dynamic_config_web.snapshot", default=None
)


class _Snapshot:
    """What one request may read, keyed two ways.

    By object for :func:`current`, which has the configuration in hand;
    by key for :func:`get`, which has only the name — a template, a log
    line, a framework that passes strings around.
    """

    __slots__ = ("_by_key", "_by_object")

    def __init__(self, models: Mapping[Any, Any], by_key: Mapping[str, Any]) -> None:
        self._by_object = dict(models)
        self._by_key = dict(by_key)

    def of(self, config: Any) -> Any:
        return self._by_object.get(config, _MISSING)

    def by_key(self, key: str) -> Any:
        return self._by_key.get(key, _MISSING)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._by_key)


#: Distinguishes "this configuration is not in the scope" from a
#: configuration whose model legitimately is `None`.
_MISSING = object()


def _members(targets: Iterable[Any]) -> list[Any]:
    """Every configuration in `targets`, groups flattened."""
    configs: list[Any] = []

    for target in targets:
        # A `ConfigGroup` is iterable over its members and a
        # `DynamicConfig` is not; duck-typing rather than an isinstance
        # keeps this module free of an import it would otherwise need at
        # runtime.
        members = getattr(target, "configs", None)

        if members is None:
            configs.append(target)
        else:
            configs.extend(members)

    return configs


#: How many times :func:`enter` re-reads when a reload lands mid-read.
#: The same constant, for the same reason, as the Rust web core's
#: `ATTEMPTS`: past this, the last read is served — no worse than not
#: checking, which is what every caller had before the check existed.
_ATTEMPTS = 8


def _read_once(configs: list[Any]) -> _Snapshot:
    """One `try_current()` per configuration, into a snapshot."""
    by_object: dict[Any, Any] = {}
    by_key: dict[str, Any] = {}

    for config in configs:
        model = config.try_current()
        by_object[config] = model
        by_key[config.key] = model

    return _Snapshot(by_object, by_key)


def _generations(configs: list[Any]) -> tuple[int, ...]:
    """Every configuration's install counter, in list order.

    The engine bumps the counter *after* the model is readable, so a
    counter can lag its model but never lead it — the comparison in
    :func:`enter` therefore errs only toward a harmless extra read,
    never toward accepting a torn one.
    """
    return tuple(config.generation for config in configs)


def enter(targets: Iterable[DynamicConfig[Any] | ConfigGroup]) -> Token[Any]:
    """Opens a scope holding one read of each configuration in `targets`.

    Every configuration is read **once**, here, with `try_current()` — a
    configuration that has not loaded yet is in the scope as `None` rather
    than raising, because a request arriving before the first load is a
    503 the health surface should answer, not an exception from the
    middleware that opened the scope.

    With more than one configuration the reads have to *agree*: each has
    its own atomic cell and the engine keeps no epoch across them, so N
    reads are N independent loads, and a reload landing between two of
    them would put two generations in one scope — exactly the tear this
    module exists to prevent, one level up. So the counters are read
    before and after, and the read starts over if anything moved. The
    Rust web core's `Sections::take` makes the same check with the same
    retry budget.

    Answers the token :func:`leave` restores.
    """
    configs = _members(targets)

    # A single configuration cannot straddle anything.
    if len(configs) < 2:
        return _SNAPSHOT.set(_read_once(configs))

    for _ in range(_ATTEMPTS):
        before = _generations(configs)
        snapshot = _read_once(configs)

        if _generations(configs) == before:
            return _SNAPSHOT.set(snapshot)

    # Reloading faster than a read completes, eight times running. The
    # last read is served: no worse than not checking.
    return _SNAPSHOT.set(_read_once(configs))


def leave(token: Token[Any]) -> None:
    """Closes the scope `token` opened. Safe in a `finally`."""
    _SNAPSHOT.reset(token)


@contextmanager
def scope(*targets: DynamicConfig[Any] | ConfigGroup) -> Iterator[None]:
    """:func:`enter` and :func:`leave` as a block.

        with scope(database, cache):
            handle(request)

    What an adapter uses when the framework gives it a place to wrap the
    whole request; the token form is for the frameworks that only offer a
    pair of hooks.
    """
    token = enter(targets)

    try:
        yield
    finally:
        leave(token)


def active() -> bool:
    """Whether a request scope is open on this task or thread."""
    return _SNAPSHOT.get() is not None


def current(config: DynamicConfig[M]) -> M:
    """The model this request began with.

    The read a handler makes, and the reason this package exists: it
    answers the same object for the whole request however many times it is
    called, so a reload landing mid-request cannot show one request two
    configurations.

    Raises :class:`OutsideRequestScopeError` when there is no request — see
    :func:`latest` for the read that is deliberately not scoped.
    """
    snapshot = _SNAPSHOT.get()

    if snapshot is None:
        raise OutsideRequestScopeError(
            f"there is no request scope here, so {config.key!r} has no "
            "snapshot to read. Inside a request, the adapter opens one; "
            "outside one — a startup task, a command, a consumer — use "
            "`latest(config)`, which says that is what you meant."
        )

    model = snapshot.of(config)

    if model is _MISSING:
        raise OutsideRequestScopeError(
            f"{config.key!r} is not in this request's scope. The adapter "
            "opens a scope over the configurations it was set up with; a "
            "configuration read here has to be one of them."
        )

    if model is None:
        # In the scope, but nothing had loaded when the request began.
        # `current()` on the engine raises the same way, and this keeps
        # the two indistinguishable to a handler.
        return config.current()

    return model  # type: ignore[no-any-return]


def get(key: str, default: Any = _MISSING) -> Any:
    """The model this request began with, by section key.

    For the code that has the name and not the object — a template, a log
    line, a framework that hands strings to a view. Raises
    :class:`OutsideRequestScopeError` outside a request, and `KeyError` for a
    key the scope does not carry unless a default is given.
    """
    snapshot = _SNAPSHOT.get()

    if snapshot is None:
        raise OutsideRequestScopeError(
            f"there is no request scope here, so {key!r} has no snapshot to read."
        )

    model = snapshot.by_key(key)

    if model is _MISSING:
        if default is not _MISSING:
            return default

        raise KeyError(
            f"{key!r} is not in this request's scope; it carries "
            f"{', '.join(repr(name) for name in snapshot.keys) or 'nothing'}"
        )

    return model


def latest(config: DynamicConfig[M]) -> M:
    """The model installed *now*, request or no request.

    The escape hatch, named so that reaching for it is a decision: a
    startup task, a management command and a background consumer all read
    outside a request and are right to. Inside a handler this is the bug
    :func:`current` prevents — two reads, two configurations, one
    response.
    """
    return config.current()
