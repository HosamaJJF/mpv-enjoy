#!/usr/bin/env python3
"""Collect an MSYS2-built mpv.exe and all non-system DLL dependencies."""

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable, List, Optional, Set


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CollectionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def parse_ldd_references(output: str) -> Iterable[str]:
    for line in output.splitlines():
        if "=>" not in line:
            continue
        reference = line.split("=>", 1)[1].strip()
        reference = re.sub(r"\s+\(0x[0-9a-fA-F]+\)\s*$", "", reference)
        if reference and reference.lower() != "not found":
            yield reference


def resolve_dependency(reference: str) -> Optional[Path]:
    candidate = Path(reference)
    if candidate.is_file():
        return candidate
    filename = reference.replace("\\", "/").rsplit("/", 1)[-1]
    located = shutil.which(filename)
    if located is not None and Path(located).is_file():
        return Path(located)
    return None


def ldd_paths(binary: Path) -> Iterable[Path]:
    result = subprocess.run(
        ["ldd", str(binary)], text=True, capture_output=True, check=True
    )
    output = result.stdout + "\n" + result.stderr
    references = list(parse_ldd_references(output))
    if not references:
        raise CollectionError("ldd returned no dependency paths:\n{}".format(output.strip()))
    for reference in references:
        normalized = reference.replace("\\", "/").lower()
        if "/windows/system32/" in normalized or normalized.startswith("/c/windows/"):
            continue
        path = resolve_dependency(reference)
        if path is None:
            raise CollectionError("Could not locate DLL dependency: {}".format(reference))
        yield path


def assert_safe_output(path: Path) -> None:
    resolved = path.resolve()
    project = PROJECT_ROOT.resolve()
    if project not in resolved.parents or "build" not in resolved.relative_to(project).parts:
        raise CollectionError("Output must be below this project's build directory")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--console-binary", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.binary.is_file():
            raise CollectionError("mpv binary does not exist: {}".format(args.binary))
        assert_safe_output(args.output)
        if args.output.exists():
            if not args.force:
                raise CollectionError("Output already exists: {}".format(args.output))
            shutil.rmtree(str(args.output))
        args.output.mkdir(parents=True)
        shutil.copy2(str(args.binary), str(args.output / "mpv.exe"))
        if args.console_binary and args.console_binary.is_file():
            shutil.copy2(str(args.console_binary), str(args.output / "mpv.com"))

        copied: Set[str] = set()
        for dependency in ldd_paths(args.binary):
            destination = args.output / dependency.name
            key = dependency.name.lower()
            if key in copied:
                if digest(destination) != digest(dependency):
                    raise CollectionError("Conflicting DLL names: {}".format(dependency.name))
                continue
            shutil.copy2(str(dependency), str(destination))
            copied.add(key)
        if not copied:
            raise CollectionError("ldd reported no non-system DLL dependencies")
    except (CollectionError, OSError, subprocess.CalledProcessError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    print("Collected mpv.exe and {} DLLs into {}".format(len(copied), args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
