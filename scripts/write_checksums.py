#!/usr/bin/env python3
"""Write recursive SHA256SUMS for a release directory."""

import argparse
from pathlib import Path
from typing import List, Optional

from fetch_dependencies import sha256_file


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    args = parser.parse_args(argv)
    output = args.release / "SHA256SUMS"
    if not args.release.is_dir():
        parser.error("release must be a directory")
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for path in sorted(args.release.rglob("*")):
            if path.is_file() and path != output:
                handle.write("{}  {}\n".format(sha256_file(path), path.relative_to(args.release)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
