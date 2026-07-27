#!/usr/bin/env python3
"""Interactively encode dandanplay credentials in uosc_danmaku's format."""

import argparse
import base64
import getpass
import shutil
import subprocess
import sys
from typing import Optional

from dandanplay_credentials import (
    APP_ID_ENV,
    APP_SECRET_ENV,
    DandanplayCredentials,
    PINNED_UPSTREAM_RUNTIME_AES_KEY,
    credential_fingerprint,
)


AES_KEY = PINNED_UPSTREAM_RUNTIME_AES_KEY
AES_BLOCK_SIZE = 16


class EncodingError(RuntimeError):
    pass


def zero_pad(value: bytes) -> bytes:
    if not value:
        raise EncodingError("Credentials must not be empty")
    padding = (-len(value)) % AES_BLOCK_SIZE
    return value + (b"\0" * padding)


def validate_plaintext(label: str, value: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise EncodingError(label + " must contain ASCII characters only") from error
    if not encoded or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise EncodingError(label + " must contain printable non-whitespace ASCII only")
    if len(encoded) > 128:
        raise EncodingError(label + " must not exceed 128 bytes")
    return encoded


def encrypt_with_openssl(openssl: str, plaintext: bytes) -> str:
    command = [
        openssl,
        "enc",
        "-aes-256-ecb",
        "-e",
        "-K",
        AES_KEY.hex(),
        "-nosalt",
        "-nopad",
    ]
    result = subprocess.run(
        command,
        input=zero_pad(plaintext),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise EncodingError(
            "OpenSSL failed to encode credentials: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return base64.b64encode(result.stdout).decode("ascii")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--openssl",
        help="OpenSSL executable; defaults to the first openssl in PATH",
    )
    args = parser.parse_args(argv)

    openssl = args.openssl or shutil.which("openssl")
    if not openssl:
        print("error: openssl is required", file=sys.stderr)
        return 1

    try:
        app_id = validate_plaintext("AppId", getpass.getpass("dandanplay AppId: "))
        app_secret = validate_plaintext(
            "AppSecret", getpass.getpass("dandanplay AppSecret: ")
        )
        credentials = DandanplayCredentials(
            encrypt_with_openssl(openssl, app_id),
            encrypt_with_openssl(openssl, app_secret),
        )
    except (EncodingError, OSError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1

    print(APP_ID_ENV + "=" + credentials.app_id_aes_b64)
    print(APP_SECRET_ENV + "=" + credentials.app_secret_aes_b64)
    print("DANDANPLAY_CREDENTIAL_FINGERPRINT=" + credential_fingerprint(credentials))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
