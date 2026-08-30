"""Deployment artifacts.

There is no container runtime on the build machine, so the image itself is
unbuilt and untested. These checks cover the failure this would otherwise
cause first: an import that works from the repo venv and is missing from the
image because nothing pinned it.
"""
from __future__ import annotations
import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

# Distribution name -> the top-level module(s) it provides.
PROVIDES = {
    "google-cloud-firestore": {"google"},
    "google-cloud-pubsub": {"google"},
    "google-adk": {"google"},
    "fastapi": {"fastapi"},
    "uvicorn": {"uvicorn"},
    "pydantic": {"pydantic"},
    "starlette": {"starlette"},
    "httpx": {"httpx"},
}


def third_party_imports() -> set[str]:
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    stdlib = set(sys.stdlib_module_names)
    return {m for m in found if m not in stdlib and m != "app"}


def pinned_modules() -> set[str]:
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    dists = {re.split(r"[=<>!~\[; ]", line.strip())[0].lower()
             for line in text.splitlines()
             if line.strip() and not line.startswith("#")}
    modules = set()
    for dist in dists:
        modules.update(PROVIDES.get(dist, {dist.replace("-", "_")}))
    return modules


def test_every_third_party_import_is_pinned():
    """The image installs only requirements.txt; an unpinned import breaks it."""
    missing = third_party_imports() - pinned_modules()
    assert not missing, (
        f"app/ imports {sorted(missing)} which requirements.txt does not pin. "
        f"The container would fail at import time."
    )


def test_dockerfile_copies_what_the_app_needs():
    d = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements.txt" in d
    assert "COPY app ./app" in d
    assert "USER harbor" in d, "container should not run as root"
    assert "${PORT}" in d, "Cloud Run injects PORT"


def test_dockerignore_excludes_secrets_and_venv():
    ig = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in (".venv", ".env", "*credentials*.json", ".git"):
        assert pattern in ig, f"{pattern} must not enter the image"


def test_service_is_pinned_to_one_instance():
    """Demo-integrity decision, documented in deploy/service.yaml."""
    y = (ROOT / "deploy" / "service.yaml").read_text(encoding="utf-8")
    assert 'minScale: "1"' in y and 'maxScale: "1"' in y


def test_deploy_scripts_fail_on_missing_config():
    for name in ("deploy.sh", "pubsub.sh"):
        s = (ROOT / "deploy" / name).read_text(encoding="utf-8")
        assert "set -euo pipefail" in s
        assert ":?" in s, f"{name} must fail loudly on an unset required variable"


def test_pubsub_wiring_creates_a_dead_letter_topic():
    """The push endpoint 400s malformed messages; without a DLQ they loop."""
    s = (ROOT / "deploy" / "pubsub.sh").read_text(encoding="utf-8")
    assert "--dead-letter-topic" in s and "--max-delivery-attempts" in s
