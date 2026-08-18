#!/usr/bin/env python3
"""Validate local Markdown links without network access."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def main() -> int:
    failures: list[str] = []
    for document in sorted(ROOT.rglob("*.md")):
        if any(part.startswith(".") for part in document.relative_to(ROOT).parts):
            continue
        for line_number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            for raw_target in LINK.findall(line):
                target = raw_target.strip().split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = (document.parent / target).resolve()
                if ROOT not in resolved.parents and resolved != ROOT:
                    failures.append(f"{document.relative_to(ROOT)}:{line_number}: escapes repository: {raw_target}")
                elif not resolved.exists():
                    failures.append(f"{document.relative_to(ROOT)}:{line_number}: missing: {raw_target}")
    if failures:
        print("\n".join(failures))
        return 1
    print("Documentation links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
