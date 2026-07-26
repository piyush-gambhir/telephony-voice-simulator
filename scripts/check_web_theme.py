"""Reject component-level colors that bypass the web console theme tokens."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "apps" / "web-console"
THEME_FILE = WEB / "app" / "globals.css"

RAW_COLOR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|"
    r"\b(?:rgb|rgba|hsl|hsla|oklch|oklab|lab|lch|color-mix)\("
)
TAILWIND_PALETTE = re.compile(
    r"\b(?:bg|text|border|ring|fill|stroke)-"
    r"(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|"
    r"indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|"
    r"stone|white|black)(?:-\d+)?\b"
)


def main() -> int:
    violations: list[str] = []
    for path in sorted(WEB.rglob("*")):
        if path == THEME_FILE or path.suffix not in {".css", ".ts", ".tsx"}:
            continue
        if any(part in {"node_modules", ".next", "out"} for part in path.parts):
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if RAW_COLOR.search(line) or TAILWIND_PALETTE.search(line):
                violations.append(
                    f"{path.relative_to(REPO)}:{line_number}: {line.strip()}"
                )

    if violations:
        print("Web UI colors must use semantic tokens from app/globals.css:")
        print("\n".join(violations))
        return 1

    print("web theme check passed: components use semantic color tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
