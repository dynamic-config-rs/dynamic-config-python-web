"""The claim the extras make, asserted rather than documented.

`pip install dynamic-config-py[fastapi]` should give a FastAPI service its
adapter and nothing else. Two halves to that promise:

- importing `dynamic_config_web` imports **no framework at all**, so a
  Django project does not pay for FastAPI and a script that only wants
  `metrics_body` pays for neither;
- importing an adapter whose framework is missing raises an error that
  **names the extra**, rather than a traceback ending in `No module named
  'litestar'`.

Both are checked in a subprocess, because the only honest way to ask "what
did importing this pull in" is an interpreter that has not imported
anything else — and because the second needs a framework to be absent,
which a test cannot arrange by uninstalling one.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap

import pytest

#: Every framework any adapter imports. The purity assertions are written
#: against this list, so adding an adapter and forgetting the list is a
#: failing test rather than a hole.
FRAMEWORKS = (
    "django",
    "django_bolt",
    "fastapi",
    "flask",
    "litestar",
    "quart",
    "robyn",
    "starlette",
    "rest_framework",
)

#: The adapters that exist, and the extra each one names. One row per
#: adapter, added in the phase that adds the adapter — a module here that
#: does not exist is a failing test, which is the point: the list is the
#: contract, not a description of it.
#:
#: `django` is a package rather than a module, and `django.drf` names the
#: `drf` extra rather than its import name — both are rows like any other,
#: because what is being asserted is the message, not the file layout.
ADAPTERS = (
    ("fastapi", "fastapi", "fastapi"),
    ("litestar", "litestar", "litestar"),
    ("flask", "flask", "flask"),
    ("quart", "quart", "quart"),
    ("django", "django", "django"),
    ("django.drf", "rest_framework", "drf"),
    ("django.ninja", "ninja", "ninja"),
    ("robyn", "robyn", "robyn"),
    ("django_bolt", "django_bolt", "django-bolt"),
)


def run(script: str, *, blocked: str = "") -> str:
    """Runs `script` in a fresh interpreter, optionally hiding a module.

    `blocked` installs a `sys.meta_path` finder that refuses one top-level
    name — a fixture rather than an uninstall, which is the only way a
    test suite can ask what happens when a framework is not there.
    """
    preamble = ""

    if blocked:
        preamble = textwrap.dedent(
            f"""
            import sys

            class Absent:
                def find_module(self, name, path=None):
                    return self.find_spec(name, path)

                def find_spec(self, name, path=None, target=None):
                    if name == {blocked!r} or name.startswith({blocked!r} + "."):
                        raise ImportError(f"no module named {{name}} (blocked)")

                    return None

            sys.meta_path.insert(0, Absent())
            for name in list(sys.modules):
                if name == {blocked!r} or name.startswith({blocked!r} + "."):
                    del sys.modules[name]
            """
        )

    finished = subprocess.run(
        [sys.executable, "-c", preamble + textwrap.dedent(script)],
        capture_output=True,
        check=True,
        text=True,
        timeout=120,
    )

    return finished.stdout.strip()


def test_importing_the_package_imports_no_framework() -> None:
    printed = run(
        f"""
        import sys

        import dynamic_config_web

        frameworks = {FRAMEWORKS!r}
        arrived = sorted(
            name for name in sys.modules if name.split(".")[0] in frameworks
        )

        print(arrived or "none")
        print(dynamic_config_web.__version__)
        """
    )

    assert printed.splitlines()[0] == "none", (
        f"importing the package pulled in {printed.splitlines()[0]}"
    )


def test_the_whole_surface_works_with_no_framework_installed() -> None:
    """Not merely importable: the shared half is usable on its own."""
    printed = run(
        """
        import json
        import tempfile
        from dataclasses import dataclass
        from pathlib import Path

        from dynamic_config import DynamicConfig

        import dynamic_config_web as web

        @dataclass
        class Database:
            host: str = "localhost"
            port: int = 5432

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"db": {"host": "here", "port": 1}}))

            config = DynamicConfig(Database, key="db").file(str(path))

            with web.Wiring(config, watch=False):
                with web.scope(config):
                    print(web.current(config).host)

                print(web.readiness(config).status_code)
                print("dynamic_config_installs_total" in web.metrics_body(config))
        """
    )

    assert printed.splitlines() == ["here", "200", "True"]


@pytest.mark.parametrize(("module", "framework", "extra"), ADAPTERS)
def test_an_adapter_without_its_framework_names_the_extra(
    module: str, framework: str, extra: str
) -> None:
    # `django.drf` and `django.ninja` sit *under* the Django adapter, so
    # this row's premise is "Django is here and its API layer is not".
    # Where Django is absent too, the import stops at Django and names
    # `django` — a correct message, and not the one this row is about.
    # The `core` job installs no framework at all, which is exactly that
    # environment; the Django job is where these two rows run.
    parent = module.split(".")[0]

    if parent != module and importlib.util.find_spec(parent) is None:
        pytest.skip(
            f"{parent} is not installed, so the import stops before {framework}"
        )

    printed = run(
        f"""
        from dynamic_config_web import MissingFrameworkError

        try:
            import dynamic_config_web.{module}
        except MissingFrameworkError as absent:
            print("named" if "{extra}" in str(absent) else "unnamed")
            print(isinstance(absent, ImportError))
        else:
            print("imported anyway")
        """,
        blocked=framework,
    )

    assert printed.splitlines() == ["named", "True"]


@pytest.mark.parametrize(
    ("framework", "only_in"),
    [("rest_framework", "drf.py"), ("ninja", "ninja.py")],
)
def test_the_django_adapter_is_a_package_of_its_own(
    framework: str, only_in: str
) -> None:
    """Django's adapter is a package, and each add-on is one module of it.

    Which matters for the promise: a Django project with neither DRF nor
    django-ninja installed must still be able to import the Django adapter.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src" / "dynamic_config_web"
    offenders = [
        source.name
        for source in root.rglob("*.py")
        if framework in source.read_text() and source.name != only_in
    ]

    assert not offenders, f"{framework} reached outside {only_in}: {offenders}"
