"""The packaging metadata must match what is actually in the repository.

A stale `package-dir` entry does not fail at import time — it fails when
somebody (or CI) runs `pip install -e .`, which is the first thing the
release pipeline does. These checks catch that locally, in a second.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_every_package_dir_exists():
    """Each mapped package must still be a directory in the tree."""
    package_dir = _pyproject().get("tool", {}).get("setuptools", {}).get("package-dir", {})
    assert package_dir, "pyproject.toml maps no packages"
    for name, relative in package_dir.items():
        assert (ROOT / relative).is_dir(), (
            f"package {name!r} points at {relative!r}, which does not exist — "
            f"`pip install -e .` would fail"
        )


def test_every_mapped_package_is_importable_by_its_name():
    """The mapped directory must actually contain the package of that name."""
    package_dir = _pyproject().get("tool", {}).get("setuptools", {}).get("package-dir", {})
    for name, relative in package_dir.items():
        assert (ROOT / relative / "__init__.py").is_file(), (
            f"{relative!r} is not a package named {name!r}"
        )


def test_console_scripts_point_at_real_entry_points():
    """Every [project.scripts] target must be a callable in the named module."""
    scripts = _pyproject().get("project", {}).get("scripts", {})
    assert scripts
    package_dir = _pyproject()["tool"]["setuptools"]["package-dir"]
    for command, target in scripts.items():
        module, _, attr = target.partition(":")
        package, _, submodule = module.partition(".")
        assert package in package_dir, f"{command}: {package!r} is not a mapped package"
        relative = f"{submodule.replace('.', '/')}.py" if submodule else "__init__.py"
        path = ROOT / package_dir[package] / relative
        assert path.is_file(), f"{command}: {path} does not exist"
        source = path.read_text(encoding="utf-8")
        assert f"def {attr}(" in source, f"{command}: {module}:{attr} is not defined"


def test_ci_installs_the_layout_dependency():
    """The diagram's layout step needs Graphviz; CI has to install it."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "npm ci" in ci, "CI does not install the node dependencies the diagram needs"
    assert (ROOT / "package-lock.json").is_file(), "npm ci needs a lock file"
