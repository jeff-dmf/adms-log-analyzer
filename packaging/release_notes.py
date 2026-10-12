"""Extract the release notes of one version from CHANGELOG.md.

Usage:  python packaging/release_notes.py VERSION [--changelog PATH] [--out PATH]

The section starts at the level-2 heading ``## [VERSION]`` (literal comparison; an optional
`` - date`` suffix is allowed) and ends at the next level-2 heading (any line starting with ``## ``).
Exit 1 when the version has no section, more than one section, or an empty one.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def extract(text: str, version: str) -> str:
    heading = f"## [{version}]"
    sections: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        line = line.rstrip("\r")
        if line == "##" or line.startswith("## "):
            if line == heading or line.startswith(heading + " "):
                current = []
                sections.append(current)
            else:
                current = None
        elif current is not None:
            current.append(line)
    if len(sections) != 1:
        raise ValueError(f"expected exactly one '{heading}' section, found {len(sections)}")
    notes = "\n".join(sections[0]).strip()
    if not notes:
        raise ValueError(f"the '{heading}' section is empty")
    return notes + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract one version's notes from CHANGELOG.md.")
    parser.add_argument("version")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        notes = extract(args.changelog.read_text(encoding="utf-8-sig"), args.version)
    except (OSError, ValueError) as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 1
    if args.out is None:
        sys.stdout.write(notes)
    else:
        args.out.write_text(notes, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
