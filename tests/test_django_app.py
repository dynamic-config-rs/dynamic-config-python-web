"""The half of the Django adapter the conformance suite cannot reach.

The conformance driver calls `configure()` and `start()` directly, because
twelve cases in one process need twelve different configurations and Django
settings are configured once. What that skips is the path a real project
takes — `INSTALLED_APPS`, `DYNAMIC_CONFIG`, `AppConfig.ready()` — so it is
exercised here, in a subprocess with a settings module of its own.

Also here: the rule that decides which processes watch files. It is a pure
function of `sys.argv` and the environment, which is what makes the
autoreloader's two processes, gunicorn's workers and `manage.py migrate`
testable at all without starting any of them.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("django")

from dynamic_config_web.django import should_watch

PROJECT = '''
"""A settings module the way a project writes one."""

SECRET_KEY = "test"
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = ["dynamic_config_web.django"]
MIDDLEWARE = ["dynamic_config_web.django.middleware.DynamicConfigMiddleware"]
ROOT_URLCONF = "project_urls"
DATABASES = {}
USE_TZ = True

DYNAMIC_CONFIG = {"target": "project_config:database", "watch": False}
'''

CONFIG = '''
"""The configuration the settings module points at."""

from dataclasses import dataclass

from dynamic_config import DynamicConfig


@dataclass
class Database:
    host: str = "localhost"
    port: int = 5432


database = DynamicConfig(Database, key="db").file("config.toml")
'''

URLS = '''
"""The health routes, included the way the book says to."""

from django.urls import include, path

urlpatterns = [path("", include("dynamic_config_web.django.urls"))]
'''

DOCUMENT = '[db]\nhost = "from-settings"\nport = 5432\n'


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A directory holding a settings module, a config module and a file."""
    (tmp_path / "project_settings.py").write_text(PROJECT)
    (tmp_path / "project_config.py").write_text(CONFIG)
    (tmp_path / "project_urls.py").write_text(URLS)
    (tmp_path / "config.toml").write_text(DOCUMENT)

    return tmp_path


def run(project: Path, script: str) -> subprocess.CompletedProcess[str]:
    """Runs `script` in `project`, with Django pointed at its settings."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin",
            "DJANGO_SETTINGS_MODULE": "project_settings",
            "PYTHONPATH": str(project),
            "HOME": str(project),
        },
        timeout=120,
    )


def command(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Runs a management command the way `manage.py` would."""
    return subprocess.run(
        [sys.executable, "-m", "django", *arguments],
        capture_output=True,
        text=True,
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin",
            "DJANGO_SETTINGS_MODULE": "project_settings",
            "PYTHONPATH": str(project),
            "HOME": str(project),
        },
        timeout=120,
    )


def test_the_installed_app_loads_the_configuration(project: Path) -> None:
    """`django.setup()` is enough: no other call is needed to be serving."""
    finished = run(
        project,
        """
        import django

        django.setup()

        from dynamic_config_web import latest
        from dynamic_config_web.django import installed, wiring

        print(installed())
        print(wiring().started)
        print(latest(wiring().configs[0]).host)
        """,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.split() == ["True", "True", "from-settings"]


def test_a_request_goes_through_the_middleware_and_the_routes(project: Path) -> None:
    """The whole project shape, end to end, in one process."""
    finished = run(
        project,
        """
        import django

        django.setup()

        from django.test import Client

        client = Client()

        print(client.get("/healthz").status_code)
        print(client.get("/readyz").status_code)
        print(client.get("/metrics").status_code)
        # No guard in DYNAMIC_CONFIG, so the diagnostics are not routed.
        print(client.get("/_config/check").status_code)
        """,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.split() == ["200", "200", "200", "404"]


def test_a_missing_setting_says_what_to_write(project: Path) -> None:
    """The error names the setting and both ways to satisfy it."""
    settings, _, _ = PROJECT.partition("DYNAMIC_CONFIG")
    (project / "project_settings.py").write_text(settings)

    finished = run(project, "import django; django.setup()")

    assert finished.returncode != 0
    assert "DYNAMIC_CONFIG" in finished.stderr
    assert "configure(" in finished.stderr, "and the other way to satisfy it"


def test_a_target_that_does_not_exist_names_the_path(project: Path) -> None:
    """A typo in the dotted path fails at startup, not at the first request."""
    (project / "project_settings.py").write_text(
        PROJECT.replace("project_config:database", "project_config:databse")
    )

    finished = run(project, "import django; django.setup()")

    assert finished.returncode != 0
    assert "databse" in finished.stderr


def test_configcheck_reports_and_passes(project: Path) -> None:
    """`manage.py configcheck` on a project whose configuration is fine."""
    finished = command(project, "configcheck")

    assert finished.returncode == 0, finished.stderr
    assert "ok — Database" in finished.stdout


def test_configcheck_fails_on_a_document_that_would_not_load(project: Path) -> None:
    """And the non-zero exit is the point: it belongs in a start script.

    Note what is *not* happening here: `django.setup()` ran even though the
    document is invalid. A command is the tool you reach for when
    configuration is broken, so a broken document must not be able to stop
    it — see :meth:`DynamicConfigAppConfig.ready`.
    """
    (project / "config.toml").write_text('[db]\nhost = "h"\nport = "not a number"\n')

    finished = command(project, "configcheck")

    assert finished.returncode != 0
    assert "would not load" in finished.stderr
    assert "port" in finished.stderr, "and it says which key"


def test_a_serving_process_refuses_to_start_on_a_broken_document(
    project: Path,
) -> None:
    """The other half: a worker fails the deploy rather than taking traffic."""
    (project / "config.toml").write_text('[db]\nhost = "h"\nport = "not a number"\n')

    finished = run(project, "import django; django.setup()")

    assert finished.returncode != 0
    assert "port" in finished.stderr


def test_configcheck_explains_one_path(project: Path) -> None:
    """`--explain` prints the layers, which is the other half of the command."""
    finished = command(project, "configcheck", "--explain", "port")

    assert finished.returncode == 0, finished.stderr
    assert "5432" in finished.stdout


def test_a_command_does_not_start_a_watcher(project: Path) -> None:
    """A short-lived process loads configuration and watches nothing."""
    finished = command(project, "configcheck")

    assert finished.returncode == 0, finished.stderr
    # The watcher would have kept the process alive past the command; that
    # it exited at all is half the assertion, and `should_watch` below is
    # the other half.
    assert "ok" in finished.stdout


@pytest.mark.parametrize(
    ("argv", "environment", "expected"),
    [
        # A server: gunicorn, uvicorn, mod_wsgi. No `manage.py` in sight.
        (["gunicorn", "project.wsgi"], {}, True),
        (["/usr/bin/uvicorn", "project.asgi:application"], {}, True),
        # `runserver`, the autoreloader's parent: it starts the child and
        # serves nothing.
        (["manage.py", "runserver"], {}, False),
        # …and its child, which is the one serving.
        (["manage.py", "runserver"], {"RUN_MAIN": "true"}, True),
        # One process, and it is the server.
        (["manage.py", "runserver", "--noreload"], {}, True),
        # A command: loads, does not watch.
        (["manage.py", "migrate"], {}, False),
        (["django-admin", "shell"], {}, False),
        (["/venv/bin/django-admin", "configcheck"], {}, False),
    ],
)
def test_which_processes_watch_files(
    argv: list[str],
    environment: dict[str, str],
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule, as a truth table rather than as a paragraph."""
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delenv("RUN_MAIN", raising=False)

    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert should_watch() is expected


def test_a_mounted_ninja_router_follows_a_revoked_guard(
    dynamic_config_wiring: Callable[..., Any], config: Any
) -> None:
    """A router built with a guard must not keep answering without one.

    The conformance suite cannot see this: it builds a fresh `NinjaAPI` per
    case, so a router is never older than the installation it serves. In a
    real project the router is built once, at import, and `configure()` may
    be called again afterwards.
    """
    import importlib.util

    if importlib.util.find_spec("ninja") is None:  # pragma: no cover
        pytest.skip("django-ninja is not installed")

    import django_bootstrap

    django_bootstrap.bootstrap()

    from django.test import Client
    from ninja import NinjaAPI

    import dynamic_config_web.django as dj
    from dynamic_config_web import token_guard
    from dynamic_config_web.django.ninja import router as ninja_router

    wiring = dynamic_config_wiring(config, watch=False, start=False)

    try:
        dj.configure(wiring, guard=token_guard("s3cret"))
        dj.start()

        api = NinjaAPI(urls_namespace="revocation")
        api.add_router("/", ninja_router())

        from django.urls import path as route

        django_bootstrap.route([route("", api.urls)])

        client = Client()
        allowed = {"x-config-token": "s3cret"}

        assert client.get("/_config/check", headers=allowed).status_code == 200

        # The same router, a new installation with no guard.
        dj.configure(wiring, guard=None)

        refused = client.get("/_config/check", headers=allowed)

        assert refused.status_code in (401, 404), (
            "a router built with a guard kept answering after it was revoked"
        )
    finally:
        dj.reset()
