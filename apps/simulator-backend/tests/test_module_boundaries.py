"""Keep reusable domain and persistence code independent of application entry points."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "telephony_voice_simulator"
PACKAGE = SOURCE.name


def _internal_imports(path: Path):
    package = path.parent.relative_to(SOURCE).parts
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".") for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = list(package[:len(package) - node.level + 1])
                prefix += node.module.split(".") if node.module else []
                names = [[PACKAGE, *prefix, alias.name] for alias in node.names]
            else:
                prefix = (node.module or "").split(".")
                names = [[*prefix, alias.name] for alias in node.names]
        else:
            continue
        for name in names:
            if len(name) > 1 and name[0] == PACKAGE:
                yield node.lineno, name[1]


@pytest.mark.parametrize("layer,forbidden", [
    ("domains", {"api", "cli", "service"}),
    ("storage", {"api", "cli", "service"}),
    ("personas", {"api", "cli", "service"}),
    ("grading", {"api", "cli", "service"}),
    ("telephony", {"api", "cli"}),
])
def test_module_dependency_direction(layer, forbidden):
    violations = [
        f"{path.relative_to(SOURCE)}:{line} imports {target}"
        for path in sorted((SOURCE / layer).rglob("*.py"))
        for line, target in _internal_imports(path)
        if target in forbidden
    ]
    assert not violations, "\n".join(violations)
