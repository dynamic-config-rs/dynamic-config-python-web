# Releasing

One distribution — `dynamic-config-py-web` — as one sdist and one
pure-Python wheel. Nothing here is compiled, so there is no wheel matrix
and no platform to strand.

The version lives in **one place**: `__version__` in
`src/dynamic_config_web/__init__.py`, which `[tool.hatch.version]` reads.
`scripts/release-web.sh` moves it and rotates the changelog in one commit.

## What this releases against

This package *depends* on `dynamic-config-py` rather than shipping it, and
the two version independently: an engine release has nothing in it for an
adapter, and an adapter fix should not drag the wheels behind it.

The floor is a `>=`, not a pin, for the reason the remote wheel gives — an
exact pin makes every engine patch a forced upgrade of this package.

**One ordering rule follows from that.** The floor has to be *resolvable*
when this ships: `dynamic-config-py-web` requiring `dynamic-config-py>=0.2`
while PyPI has 0.1.3 means `pip install dynamic-config-py[fastapi]` resolves
to nothing for everybody. `release-web.sh --check` refuses that case by
name, and the fix is to release the base wheel first.

## The branch model

Work lands on `dev`. `main` is production: it accepts no direct pushes —
not even from admins — only pull requests whose gates ("CI is green",
"Security is green") have passed, merged with a linear history.

**Merging a version bump into `main` is the release.** There is no tag to
push by hand: `release.yml` runs on every push to `main`, checks whether the
version is new, and — only then — builds, publishes, and mints the tag and
the GitHub release itself, at the merge commit.

## The lifecycle, step by step

1. **Land the work on `dev`** through pull requests, entries accumulating
   under `## [Unreleased]` in `CHANGELOG.md`.
2. **Pre-flight.** `just check` on `dev` — lint, `mypy --strict`, the core
   suite, every adapter whose framework is installed, and every example.
   A framework you did not install skips itself locally; the CI matrix is
   what covers all nine.
3. **`./scripts/release-web.sh --check minor`** — the target, not the
   version the tree is on. That one has already been released, so a bare
   `--check` always reports it as taken, which says nothing about the
   release being planned. It refuses a version already on PyPI, refuses a
   `dynamic-config-py` floor PyPI cannot satisfy, and refuses an empty
   `## [Unreleased]`.

   Optional, strictly: step 4 runs the same check on the same target before
   it changes a file, and stops there if anything fails.
4. **`./scripts/release-web.sh patch`** (or `minor`; pre-1.0 a breaking
   change is `minor`). Bumps `__version__`, rotates the changelog, and makes
   one local commit — no push, no tag, no publish.

   **The first release is `./scripts/release-web.sh 0.1.0`.** The module
   already says 0.1.0 and nothing is published, so naming the version
   explicitly rotates the changelog and leaves the version where it is,
   which is what `release.yml` then sees as new.
5. **Read the commit.** `git show --stat HEAD`: one module, one changelog.
   Exactly one heading per version, entries under the new one, `Unreleased`
   empty again.
6. **`./scripts/promote.sh`.** Pushes `dev`, opens or updates the pull
   request, arms auto-merge and waits; when both gates pass, the
   squash-merge lands — **that merge is the release**.
7. **`./scripts/watch-release.sh`.** Follows the run the merge set off: the
   sdist and wheel, an install of the wheel into an empty environment to
   prove it imports no framework, `twine upload --skip-existing`, then the
   tag, the SBOM and the GitHub release.

`./scripts/release-web.sh --publish` exists for the case where a release has
to go out from a commit that is already on `main` — it dispatches the same
workflow by hand.

## What an operator has to have ready

**No token.** The publish job authenticates through PyPI's Trusted
Publishing (OIDC): the one-time console entry — PyPI →
`dynamic-config-py-web` → Settings → Publishing → *Add a trusted
publisher* — names owner `dynamic-config-rs`, repository
`dynamic-config-python-web`, workflow `release.yml`. A leftover
`PYPI_TOKEN` secret is inert and should be revoked.
Nothing else.

## Afterwards

Check that PyPI shows the new version, and that the two claims this package
makes actually hold from the outside, in a clean environment:

```sh
pip install "dynamic-config-py[fastapi]"
python -c "import dynamic_config_web, fastapi; print(dynamic_config_web.__version__)"

pip install dynamic-config-py-web
python -c "import sys, dynamic_config_web; print([m for m in sys.modules if m.startswith('fastapi')])"   # []
```

The first is the whole reason the base wheel carries the extras; the second
is the purity claim, and the release job asserts it too.

## Version policy

- **Pre-1.0, a breaking change bumps the minor version** and everything else
  the patch.
- A change to the minimum supported Python version is breaking.
- **Dropping a framework is breaking. So is raising an extra's floor past a
  release people are on** — `flask>=2.2` becoming `flask>=3` is not a patch,
  however good the reason.
- The two **Experimental** adapters — Robyn and django-bolt — may change
  surface in a *minor* release rather than waiting for a major one. That is
  stated in the book's stability page and in the changelog entry that does
  it.
- The engine floor moving is not by itself breaking: what matters is whether
  *this* package's surface moved.
