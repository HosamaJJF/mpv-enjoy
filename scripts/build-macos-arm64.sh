#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "This build must run natively on Apple Silicon macOS." >&2
    exit 1
fi

MPV_LAZY_ENJOY_PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MPV_LAZY_ENJOY_BUILD_DIR="$MPV_LAZY_ENJOY_PROJECT_DIR/build"
MPV_LAZY_ENJOY_SOURCE_DIR="$MPV_LAZY_ENJOY_BUILD_DIR/sources/mpv"
MPV_LAZY_ENJOY_MPV_BUILD_DIR="$MPV_LAZY_ENJOY_BUILD_DIR/mpv-macos"
MPV_LAZY_ENJOY_RELEASE_DIR="$MPV_LAZY_ENJOY_BUILD_DIR/release/mpv-lazy-enjoy-0.1.0-dev-macos-arm64"
MPV_LAZY_ENJOY_DIST_DIR="$MPV_LAZY_ENJOY_PROJECT_DIR/dist"
MPV_LAZY_ENJOY_MANIFEST="$MPV_LAZY_ENJOY_BUILD_DIR/macos-build-dependencies.txt"
MPV_LAZY_ENJOY_APP="$MPV_LAZY_ENJOY_RELEASE_DIR/mpv-lazy-enjoy.app"

export SOURCE_DATE_EPOCH=1766344646
export MACOSX_DEPLOYMENT_TARGET=14.0
export HOMEBREW_NO_AUTO_UPDATE=1
cd "$MPV_LAZY_ENJOY_PROJECT_DIR"

for MPV_LAZY_ENJOY_TOOL in brew python3 meson ninja go clang codesign hdiutil
do
    if ! command -v "$MPV_LAZY_ENJOY_TOOL" >/dev/null 2>&1; then
        echo "Missing required tool: $MPV_LAZY_ENJOY_TOOL" >&2
        exit 1
    fi
done

MPV_LAZY_ENJOY_LIBARCHIVE_PREFIX=$(brew --prefix libarchive)
export PKG_CONFIG_PATH="$MPV_LAZY_ENJOY_LIBARCHIVE_PREFIX/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

python3 scripts/fetch_dependencies.py \
    --all \
    --platform macos-arm64 \
    --extract "mpv=$MPV_LAZY_ENJOY_SOURCE_DIR" \
    --force-extract

MPV_LAZY_ENJOY_MESON_ARGS=(
    "$MPV_LAZY_ENJOY_MPV_BUILD_DIR"
    "$MPV_LAZY_ENJOY_SOURCE_DIR"
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
    -Dgl=enabled
    -Dcocoa=enabled
    -Dcoreaudio=enabled
    -Dgl-cocoa=enabled
    -Dvideotoolbox-gl=enabled
    -Dvideotoolbox-pl=enabled
    -Dswift-build=enabled
    -Dmacos-cocoa-cb=enabled
    -Dmacos-media-player=enabled
    -Dmacos-touchbar=enabled
    -Dvulkan=enabled
    -Dswift-flags=-target\ arm64-apple-macosx14.0
)

if [[ -d "$MPV_LAZY_ENJOY_MPV_BUILD_DIR/meson-private" ]]; then
    meson setup --wipe "${MPV_LAZY_ENJOY_MESON_ARGS[@]}"
else
    meson setup "${MPV_LAZY_ENJOY_MESON_ARGS[@]}"
fi
meson compile -C "$MPV_LAZY_ENJOY_MPV_BUILD_DIR"
meson compile -C "$MPV_LAZY_ENJOY_MPV_BUILD_DIR" macos-bundle

python3 scripts/capture_build_manifest.py \
    --platform macos-arm64 \
    --output "$MPV_LAZY_ENJOY_MANIFEST"

python3 scripts/assemble.py \
    --platform macos-arm64 \
    --mpv "$MPV_LAZY_ENJOY_MPV_BUILD_DIR/mpv.app" \
    --output "$MPV_LAZY_ENJOY_RELEASE_DIR" \
    --build-manifest "$MPV_LAZY_ENJOY_MANIFEST" \
    --force

if ! /usr/bin/file "$MPV_LAZY_ENJOY_APP/Contents/MacOS/mpv-bin" | /usr/bin/grep -q arm64; then
    echo "mpv-bin is not arm64" >&2
    exit 1
fi
if ! /usr/bin/file "$MPV_LAZY_ENJOY_APP/Contents/Resources/config-template/scripts/uosc/bin/ziggy-darwin" | /usr/bin/grep -q arm64; then
    echo "ziggy-darwin is not arm64" >&2
    exit 1
fi

/usr/bin/codesign --force --sign - --timestamp=none \
    "$MPV_LAZY_ENJOY_APP/Contents/Resources/config-template/scripts/uosc/bin/ziggy-darwin"
/usr/bin/codesign --force --deep --sign - --timestamp=none "$MPV_LAZY_ENJOY_APP"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$MPV_LAZY_ENJOY_APP"

python3 scripts/write_checksums.py "$MPV_LAZY_ENJOY_RELEASE_DIR"
python3 scripts/verify_release.py \
    --platform macos-arm64 \
    --release "$MPV_LAZY_ENJOY_RELEASE_DIR"

/bin/mkdir -p "$MPV_LAZY_ENJOY_DIST_DIR"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent \
    "$MPV_LAZY_ENJOY_RELEASE_DIR" \
    "$MPV_LAZY_ENJOY_DIST_DIR/mpv-lazy-enjoy-0.1.0-dev-macos-arm64.zip"
/usr/bin/hdiutil create \
    -volname mpv-lazy-enjoy \
    -srcfolder "$MPV_LAZY_ENJOY_RELEASE_DIR" \
    -ov \
    -format UDZO \
    "$MPV_LAZY_ENJOY_DIST_DIR/mpv-lazy-enjoy-0.1.0-dev-macos-arm64.dmg"
python3 scripts/write_checksums.py "$MPV_LAZY_ENJOY_DIST_DIR"

echo "macOS packages are in: $MPV_LAZY_ENJOY_DIST_DIR"
