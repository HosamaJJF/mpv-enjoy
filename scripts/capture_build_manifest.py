#!/usr/bin/env python3
"""Capture concrete package and compiler versions used by a native build."""

import argparse
from pathlib import Path
import platform
import subprocess
from typing import List, Optional, Sequence, Tuple


def capture(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError as error:
        return "[unavailable] {}: {}".format(command[0], error)
    output = result.stdout.strip()
    if result.stderr.strip():
        output += "\n[stderr]\n" + result.stderr.strip()
    if result.returncode:
        output += "\n[exit code {}]".format(result.returncode)
    return output


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", required=True, choices=["windows-x64", "macos-arm64", "macos-x64"]
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    commands: List[Tuple[str, Sequence[str]]] = [
        ("Python", ["python3", "--version"]),
        ("Node.js", ["node", "--version"]),
        ("npm", ["npm", "--version"]),
        ("Rust", ["rustc", "--version"]),
        ("Cargo", ["cargo", "--version"]),
        ("Meson", ["meson", "--version"]),
        ("Ninja", ["ninja", "--version"]),
        ("Go", ["go", "version"]),
        ("Compiler", ["clang", "--version"]),
    ]
    if args.platform == "windows-x64":
        commands.append(("MSYS2 packages", ["sh", "-lc", "pacman -Q"]))
    else:
        commands.append(("Homebrew packages", ["brew", "list", "--versions"]))
        commands.append(("Xcode", ["xcodebuild", "-version"]))
    lines = [
        "mpv-enjoy native build dependency manifest",
        "platform={}".format(args.platform),
        "host={}".format(platform.platform()),
        "machine={}".format(platform.machine()),
    ]
    for title, command in commands:
        lines.extend(["", "[{}]".format(title), capture(command)])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
