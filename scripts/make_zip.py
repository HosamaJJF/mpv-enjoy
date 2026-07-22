#!/usr/bin/env python3
"""Create a deterministic ZIP for the Windows release directory."""

import argparse
from pathlib import Path
import stat
from typing import List, Optional
import zipfile


FIXED_TIMESTAMP = (2026, 7, 22, 0, 0, 0)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        parser.error("source must be a directory")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        str(args.output), mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(args.source.rglob("*")):
            if not path.is_file():
                continue
            relative = Path(args.source.name) / path.relative_to(args.source)
            info = zipfile.ZipInfo(str(relative).replace("\\", "/"), FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IMODE(path.stat().st_mode) & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
