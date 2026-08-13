"""Confirm backend/ai/ never imports backend/valuation/ (the deterministic
calculation engine) at module load time. AI output must only ever reach a
calculation via a typed proposal + human-approval path (Phase 7), never a
direct import that could let AI text/numbers flow straight into arithmetic.
"""

from __future__ import annotations

import ast
from pathlib import Path

AI_DIR = Path(__file__).resolve().parents[1] / "ai"


def _imports_valuation(py_file: Path) -> bool:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("backend.valuation"):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("backend.valuation"):
                return True
    return False


def test_ai_package_does_not_import_valuation():
    offenders = []
    for py_file in AI_DIR.rglob("*.py"):
        if _imports_valuation(py_file):
            offenders.append(str(py_file))
    assert offenders == [], f"backend/ai/ files import backend/valuation/ directly: {offenders}"
