#!/bin/sh
set -eu

MPV_LAZY_ENJOY_MACOS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MPV_LAZY_ENJOY_RESOURCES_DIR="$MPV_LAZY_ENJOY_MACOS_DIR/../Resources"
MPV_LAZY_ENJOY_DATA_ROOT=${MPV_LAZY_ENJOY_HOME:-"$HOME/Library/Application Support/mpv-lazy-enjoy"}
MPV_LAZY_ENJOY_CONFIG_DIR="$MPV_LAZY_ENJOY_DATA_ROOT/config"
MPV_LAZY_ENJOY_TEMPLATE_DIR="$MPV_LAZY_ENJOY_RESOURCES_DIR/config-template"

/bin/mkdir -p "$MPV_LAZY_ENJOY_CONFIG_DIR/scripts" "$MPV_LAZY_ENJOY_CONFIG_DIR/fonts" "$MPV_LAZY_ENJOY_CONFIG_DIR/script-opts"

# Script code and fonts are package-managed and refreshed on every launch.
/usr/bin/ditto "$MPV_LAZY_ENJOY_TEMPLATE_DIR/scripts" "$MPV_LAZY_ENJOY_CONFIG_DIR/scripts"
/usr/bin/ditto "$MPV_LAZY_ENJOY_TEMPLATE_DIR/fonts" "$MPV_LAZY_ENJOY_CONFIG_DIR/fonts"

# User-editable configuration is only seeded when absent.
for MPV_LAZY_ENJOY_CONFIG_FILE in mpv.conf input.conf profiles.conf platform.conf user.conf
do
    if [ ! -e "$MPV_LAZY_ENJOY_CONFIG_DIR/$MPV_LAZY_ENJOY_CONFIG_FILE" ]; then
        /bin/cp "$MPV_LAZY_ENJOY_TEMPLATE_DIR/$MPV_LAZY_ENJOY_CONFIG_FILE" "$MPV_LAZY_ENJOY_CONFIG_DIR/$MPV_LAZY_ENJOY_CONFIG_FILE"
    fi
done

for MPV_LAZY_ENJOY_OPTION_FILE in uosc.conf uosc_danmaku.conf thumbfast.conf
do
    if [ ! -e "$MPV_LAZY_ENJOY_CONFIG_DIR/script-opts/$MPV_LAZY_ENJOY_OPTION_FILE" ]; then
        /bin/cp "$MPV_LAZY_ENJOY_TEMPLATE_DIR/script-opts/$MPV_LAZY_ENJOY_OPTION_FILE" "$MPV_LAZY_ENJOY_CONFIG_DIR/script-opts/$MPV_LAZY_ENJOY_OPTION_FILE"
    fi
done

export PATH="$MPV_LAZY_ENJOY_MACOS_DIR:/usr/bin:/bin:/usr/sbin:/sbin"
exec "$MPV_LAZY_ENJOY_MACOS_DIR/mpv-bin" --config-dir="$MPV_LAZY_ENJOY_CONFIG_DIR" "$@"
