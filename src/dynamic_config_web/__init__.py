"""Native web-framework integrations for `dynamic-config-py`.

Nine adapters, one shape. Whatever the framework calls its startup
hook, its dependency injection and its router, an integration here does
the same five things:

- **loads before the first request and watches while serving**, once per
  process and paired with shutdown, however many times the application
  object is built (`_wiring`);
- **reads the configuration once per request** and hands the same model
  to every line of the handler (`_scope`);
- answers **liveness and readiness** as the two different questions they
  are (`_health`);
- renders the **Prometheus body** the engine already knows how to build
  (`_metrics`);
- offers **`explain` and `check` over HTTP**, to whoever a guard says may
  ask, on a thread because they re-read the sources (`_diagnostics`).

This module is the framework-agnostic half, and importing it imports no
framework at all. Each adapter is a submodule named after its framework
— `dynamic_config_web.fastapi`, `.litestar`, `.flask`, `.quart`,
`.robyn`, `.django`, `.django_bolt` — and importing one is what pulls
that framework in. A framework that is not installed raises
`MissingFrameworkError`, which names the extra that installs it.

    from dynamic_config import DynamicConfig
    from dynamic_config_web.fastapi import setup, config_dependency

    config = DynamicConfig(Database, key="db").file("config.toml")
    app = FastAPI()

    setup(app, config)

    @app.get("/health")
    def health(db: Database = Depends(config_dependency(config))):
        return {"host": db.host}
"""

from __future__ import annotations

#: The distribution's version, and the single place it is written: the
#: build backend reads this file rather than the other way round.
__version__ = "0.2.0"

from ._diagnostics import (
    Guard,
    check,
    check_async,
    explain,
    explain_async,
    never,
    token_guard,
)
from ._errors import MissingFrameworkError, OutsideRequestScopeError
from ._events import log_reloads, stream_events
from ._health import HealthReport, liveness, readiness
from ._metrics import CONTENT_TYPE, metrics_body
from ._scope import active, current, enter, get, latest, leave, scope
from ._testing import as_request, pinned
from ._wiring import Wiring

__all__ = [
    "CONTENT_TYPE",
    "Guard",
    "HealthReport",
    "MissingFrameworkError",
    "OutsideRequestScopeError",
    "Wiring",
    "__version__",
    "active",
    "as_request",
    "check",
    "check_async",
    "current",
    "enter",
    "explain",
    "explain_async",
    "get",
    "latest",
    "leave",
    "liveness",
    "log_reloads",
    "metrics_body",
    "never",
    "pinned",
    "readiness",
    "scope",
    "stream_events",
    "token_guard",
]
