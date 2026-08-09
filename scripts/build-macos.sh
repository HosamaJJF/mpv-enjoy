#!/usr/bin/env bash
set -euo pipefail

MPV_ENJOY_PLATFORM=${1:-}
case "$MPV_ENJOY_PLATFORM" in
    macos-arm64)
        MPV_ENJOY_HOST_ARCH=arm64
        MPV_ENJOY_MACHO_ARCH=arm64
        ;;
    macos-x64)
        MPV_ENJOY_HOST_ARCH=x86_64
        MPV_ENJOY_MACHO_ARCH=x86_64
        ;;
    *)
        echo "Usage: $0 <macos-arm64|macos-x64>" >&2
        exit 1
        ;;
esac

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "$MPV_ENJOY_HOST_ARCH" ]]; then
    echo "$MPV_ENJOY_PLATFORM must be built natively on a $MPV_ENJOY_HOST_ARCH Mac." >&2
    exit 1
fi

MPV_ENJOY_PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MPV_ENJOY_BUILD_DIR="$MPV_ENJOY_PROJECT_DIR/build"
MPV_ENJOY_SOURCE_DIR="$MPV_ENJOY_BUILD_DIR/sources/mpv"
MPV_ENJOY_HOME_SOURCE_DIR="$MPV_ENJOY_BUILD_DIR/sources/mpv-enjoy-home"
MPV_ENJOY_HOME_APP="$MPV_ENJOY_BUILD_DIR/home/$MPV_ENJOY_PLATFORM/mpv-enjoy.app"
MPV_ENJOY_HOME_METADATA="$MPV_ENJOY_BUILD_DIR/home/$MPV_ENJOY_PLATFORM/metadata"
MPV_ENJOY_MPV_BUILD_DIR="$MPV_ENJOY_BUILD_DIR/mpv-$MPV_ENJOY_PLATFORM"
MPV_ENJOY_RELEASE_DIR="$MPV_ENJOY_BUILD_DIR/release/mpv-enjoy-1.2.1-$MPV_ENJOY_PLATFORM"
MPV_ENJOY_DIST_DIR="$MPV_ENJOY_PROJECT_DIR/dist"
MPV_ENJOY_MANIFEST="$MPV_ENJOY_BUILD_DIR/$MPV_ENJOY_PLATFORM-build-dependencies.txt"
MPV_ENJOY_APP="$MPV_ENJOY_RELEASE_DIR/mpv-enjoy.app"

export SOURCE_DATE_EPOCH=1766344646
export MACOSX_DEPLOYMENT_TARGET=14.0
export HOMEBREW_NO_AUTO_UPDATE=1
cd "$MPV_ENJOY_PROJECT_DIR"

for MPV_ENJOY_TOOL in brew python3 node npm rustc cargo meson ninja go clang codesign hdiutil
do
    if ! command -v "$MPV_ENJOY_TOOL" >/dev/null 2>&1; then
        echo "Missing required tool: $MPV_ENJOY_TOOL" >&2
        exit 1
    fi
done

MPV_ENJOY_LIBARCHIVE_PREFIX=$(brew --prefix libarchive)
export PKG_CONFIG_PATH="$MPV_ENJOY_LIBARCHIVE_PREFIX/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

python3 scripts/fetch_dependencies.py \
    --all \
    --platform "$MPV_ENJOY_PLATFORM" \
    --extract "mpv=$MPV_ENJOY_SOURCE_DIR" \
    --extract "mpv_enjoy_home=$MPV_ENJOY_HOME_SOURCE_DIR" \
    --force-extract

python3 scripts/build_home.py \
    --platform "$MPV_ENJOY_PLATFORM" \
    --source "$MPV_ENJOY_HOME_SOURCE_DIR" \
    --output "$MPV_ENJOY_HOME_APP" \
    --metadata-output "$MPV_ENJOY_HOME_METADATA"

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
    "-Dswift-flags=-target $MPV_ENJOY_MACHO_ARCH-apple-macosx14.0"
)

if [[ -d "$MPV_ENJOY_MPV_BUILD_DIR/meson-private" ]]; then
    meson setup --wipe "${MPV_ENJOY_MESON_ARGS[@]}"
else
    meson setup "${MPV_ENJOY_MESON_ARGS[@]}"
fi
meson compile -C "$MPV_ENJOY_MPV_BUILD_DIR"
meson compile -C "$MPV_ENJOY_MPV_BUILD_DIR" macos-bundle

python3 scripts/capture_build_manifest.py \
    --platform "$MPV_ENJOY_PLATFORM" \
    --output "$MPV_ENJOY_MANIFEST"

python3 scripts/assemble.py \
    --platform "$MPV_ENJOY_PLATFORM" \
    --home "$MPV_ENJOY_HOME_APP" \
    --home-metadata "$MPV_ENJOY_HOME_METADATA/THIRD-PARTY-LICENSES.json" \
    --mpv "$MPV_ENJOY_MPV_BUILD_DIR/mpv.app" \
    --output "$MPV_ENJOY_RELEASE_DIR" \
    --build-manifest "$MPV_ENJOY_MANIFEST" \
    --force

for MPV_ENJOY_BINARY in \
    "$MPV_ENJOY_APP/Contents/MacOS/mpv-enjoy-home" \
    "$MPV_ENJOY_APP/Contents/MacOS/mpv-bin" \
    "$MPV_ENJOY_APP/Contents/MacOS/mpv-player" \
    "$MPV_ENJOY_APP/Contents/MacOS/yt-dlp" \
    "$MPV_ENJOY_APP/Contents/Resources/config-template/scripts/uosc/bin/ziggy-darwin" \
    "$MPV_ENJOY_APP/Contents/Resources/config-template/scripts/uosc_videotogether/bin/uosc-videotogether-agent-darwin"
do
    MPV_ENJOY_DESCRIPTION=$(/usr/bin/file "$MPV_ENJOY_BINARY")
    if [[ "$MPV_ENJOY_DESCRIPTION" != *"Mach-O 64-bit executable $MPV_ENJOY_MACHO_ARCH"* ]] || \
       [[ "$MPV_ENJOY_DESCRIPTION" == *"universal binary"* ]]; then
        echo "Unexpected architecture for $MPV_ENJOY_BINARY: $MPV_ENJOY_DESCRIPTION" >&2
        exit 1
    fi
done

/usr/bin/codesign --force --sign - --timestamp=none \
    "$MPV_ENJOY_APP/Contents/Resources/config-template/scripts/uosc/bin/ziggy-darwin"
/usr/bin/codesign --force --sign - --timestamp=none \
    "$MPV_ENJOY_APP/Contents/Resources/config-template/scripts/uosc_videotogether/bin/uosc-videotogether-agent-darwin"
/usr/bin/codesign --force --deep --sign - --timestamp=none "$MPV_ENJOY_APP"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$MPV_ENJOY_APP"

python3 scripts/write_checksums.py "$MPV_ENJOY_RELEASE_DIR"
python3 scripts/verify_release.py \
    --platform "$MPV_ENJOY_PLATFORM" \
    --release "$MPV_ENJOY_RELEASE_DIR"

/bin/mkdir -p "$MPV_ENJOY_DIST_DIR"
/usr/bin/hdiutil create \
    -volname mpv-enjoy \
    -srcfolder "$MPV_ENJOY_RELEASE_DIR" \
    -ov \
    -format UDZO \
    "$MPV_ENJOY_DIST_DIR/mpv-enjoy-1.2.1-$MPV_ENJOY_PLATFORM.dmg"
/usr/bin/hdiutil verify "$MPV_ENJOY_DIST_DIR/mpv-enjoy-1.2.1-$MPV_ENJOY_PLATFORM.dmg"
python3 scripts/write_checksums.py "$MPV_ENJOY_DIST_DIR"

echo "macOS package: $MPV_ENJOY_DIST_DIR/mpv-enjoy-1.2.1-$MPV_ENJOY_PLATFORM.dmg"
