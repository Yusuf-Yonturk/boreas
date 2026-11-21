#!/usr/bin/env python3
"""Prints the CHANGELOG.md section of one version, for the body of its GitHub Release.

    python3 tools/release-notes.py 0.1.8 > notes.md
    gh release create v0.1.8 --verify-tag --title "Boreas 0.1.8" --notes-file notes.md

A leading "v" is accepted. Exits with status 1 if the version has no section or the section is empty.
"""

from __future__ import annotations

import os
import re
import sys

CHANGELOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CHANGELOG.md")


def section(text: str, version: str) -> str:
    """The lines under "## <version>" up to the next "## " heading, without the heading itself."""
    version = version.removeprefix("v")
    found = re.search(rf"^## {re.escape(version)}[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return found.group(1).strip() if found else ""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    with open(CHANGELOG, encoding="utf-8") as fh:
        notes = section(fh.read(), sys.argv[1])
    if not notes:
        print(f"CHANGELOG.md has no notes for {sys.argv[1]}", file=sys.stderr)
        return 1
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
