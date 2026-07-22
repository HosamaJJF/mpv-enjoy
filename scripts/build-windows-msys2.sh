#!/usr/bin/env bash
set -euo pipefail

if [[ "${MSYSTEM:-}" != "CLANG64" ]]; then
    echo "Run this script from an MSYS2 CLANG64 shell." >&2
    exit 1
fi

MPV_ENJOY_PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MPV_ENJOY_BUILD_DIR="$MPV_ENJOY_PROJECT_DIR/build"
MPV_ENJOY_SOURCE_DIR="$MPV_ENJOY_BUILD_DIR/sources/mpv"
MPV_ENJOY_MPV_BUILD_DIR="$MPV_ENJOY_BUILD_DIR/mpv-windows"
MPV_ENJOY_RUNTIME_DIR="$MPV_ENJOY_BUILD_DIR/mpv-runtime/windows-x64"
MPV_ENJOY_RELEASE_DIR="$MPV_ENJOY_BUILD_DIR/release/mpv-enjoy-1.0.0-windows-x64"
MPV_ENJOY_DIST_DIR="$MPV_ENJOY_PROJECT_DIR/dist"
MPV_ENJOY_MANIFEST="$MPV_ENJOY_BUILD_DIR/windows-build-dependencies.txt"

export SOURCE_DATE_EPOCH=1766344646
cd "$MPV_ENJOY_PROJECT_DIR"

python3 scripts/fetch_dependencies.py \
    --all \
    --platform windows-x64 \
    --extract "mpv=$MPV_ENJOY_SOURCE_DIR" \
    --force-extract

MPV_ENJOY_MESON_ARGS=(
    "$MPV_ENJOY_MPV_BUILD_DIR"
    "$MPV_ENJOY_SOURCE_DIR"
    --buildtype=release
    --wrap-mode=nodownload
    -Dbuild-date=false
    -Dlibmpv=false
    -Dtests=false
    -Dlua=luajit
    -Djavascript=disabled
    -Dvapoursynth=disabled
    -Dcuda-hwaccel=disabled
    -Dcuda-interop=disabled
    -Dcdda=disabled
    -Ddvdnav=disabled
    -Dlibbluray=disabled
    -Drubberband=disabled
    -Dlcms2=enabled
    -Dlibarchive=enabled
    -Duchardet=enabled
    -Dd3d11=enabled
    -Dd3d-hwaccel=enabled
    -Dwasapi=enabled
    -Dvulkan=enabled
)

if [[ -d "$MPV_ENJOY_MPV_BUILD_DIR/meson-private" ]]; then
    meson setup --wipe "${MPV_ENJOY_MESON_ARGS[@]}"
else
    meson setup "${MPV_ENJOY_MESON_ARGS[@]}"
fi
meson compile -C "$MPV_ENJOY_MPV_BUILD_DIR"

python3 scripts/collect_windows_runtime.py \
    --binary "$MPV_ENJOY_MPV_BUILD_DIR/mpv.exe" \
    --console-binary "$MPV_ENJOY_MPV_BUILD_DIR/mpv.com" \
    --output "$MPV_ENJOY_RUNTIME_DIR" \
    --force

python3 scripts/capture_build_manifest.py \
    --platform windows-x64 \
    --output "$MPV_ENJOY_MANIFEST"

python3 scripts/assemble.py \
    --platform windows-x64 \
    --mpv "$MPV_ENJOY_RUNTIME_DIR" \
    --output "$MPV_ENJOY_RELEASE_DIR" \
    --build-manifest "$MPV_ENJOY_MANIFEST" \
    --force

python3 scripts/verify_release.py \
    --platform windows-x64 \
    --release "$MPV_ENJOY_RELEASE_DIR"
python3 scripts/write_checksums.py "$MPV_ENJOY_RELEASE_DIR"

/bin/mkdir -p "$MPV_ENJOY_DIST_DIR"
python3 scripts/make_zip.py \
    --source "$MPV_ENJOY_RELEASE_DIR" \
    --output "$MPV_ENJOY_DIST_DIR/mpv-enjoy-1.0.0-windows-x64.zip"
python3 scripts/write_checksums.py "$MPV_ENJOY_DIST_DIR"

echo "Windows package: $MPV_ENJOY_DIST_DIR/mpv-enjoy-1.0.0-windows-x64.zip"
