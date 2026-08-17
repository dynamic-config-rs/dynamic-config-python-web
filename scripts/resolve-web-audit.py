"""This package's *runtime* dependencies, as one requirements file to audit.

No lockfile is committed here, and none should be: a library that pinned its
dependencies would pin its users'. What can still be audited is what a user
installing today actually resolves — so the extras are read out of
`pyproject.toml`, installed into a throwaway environment, and the frozen
result is what the scanner sees.

Seven frameworks is a wide surface, and that is the point of scanning it:
this distribution is the one place in the organisation that pulls in
somebody else's web framework.

`dev` and `test` are excluded — those are a contributor's machine. `all` is
excluded because it is self-referential, and its members are here on their
own. Extras whose marker excludes the running interpreter resolve to nothing
and are skipped with a note rather than failing the run.
"""

import subprocess
import sys
from pathlib import Path

import tomllib

SKIP = {"all", "dev", "test"}
#: This organisation's own distributions. Auditing ourselves against a
#: public advisory database answers nothing.
OURS = {"dynamic-config-py", "dynamic-config-py-remote", "dynamic-config-py-web"}


def wanted(manifest: Path) -> list[str]:
    """Every runtime requirement, the framework extras included."""
    with manifest.open("rb") as handle:
        project = tomllib.load(handle)["project"]

    requirements = list(project.get("dependencies", []))

    for extra, entries in project.get("optional-dependencies", {}).items():
        if extra not in SKIP:
            requirements.extend(entries)

    return sorted(
        entry
        for entry in set(requirements)
        if not any(entry.startswith(name) for name in OURS)
    )


def main() -> None:
    """Resolves them into `venv` and writes the frozen set to `out`."""
    venv, out = Path(sys.argv[1]), Path(sys.argv[2])
    requirements = wanted(Path("pyproject.toml"))

    print("resolving:", ", ".join(requirements))

    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)

    pip = str(venv / "bin" / "pip")
    installed: list[str] = []

    # One at a time, deliberately. A marker that excludes this interpreter
    # — `django-bolt` below 3.12, Robyn below 3.10 — makes pip resolve
    # nothing for that entry, and a single batched install would take the
    # whole audit down with it. Which framework was skipped is printed, so a
    # thin report is visible rather than quiet.
    for requirement in requirements:
        finished = subprocess.run(
            [pip, "install", "--quiet", requirement], capture_output=True, text=True
        )

        if finished.returncode == 0:
            installed.append(requirement)
        else:
            why = finished.stderr.strip().splitlines()[-1]
            print(f"  skipped {requirement}: {why}")

    frozen = subprocess.run(
        [pip, "freeze"], check=True, capture_output=True, text=True
    ).stdout

    kept = [
        line
        for line in frozen.splitlines()
        if line and not any(line.startswith(name) for name in OURS)
    ]

    out.write_text("\n".join(kept) + "\n")
    print(
        f"{len(installed)}/{len(requirements)} requirements, "
        f"{len(kept)} packages -> {out}"
    )


if __name__ == "__main__":
    main()
