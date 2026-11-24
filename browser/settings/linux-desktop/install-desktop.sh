#!/usr/bin/env bash
# Install a desktop launcher for this Camouwolf bundle so its logo shows in the
# GNOME dash / Ubuntu dock / app grid (and clicking it launches Camouwolf).
#
# Portable bundles (the .zip you unzipped) can't ship a system launcher — the
# paths aren't known until you unzip. This script wires one up for *this* unzip
# location, into your per-user directories (no root needed):
#
#   ~/.local/share/applications/camouwolf.desktop
#   ~/.local/share/icons/hicolor/<size>/apps/camouwolf.png
#   ~/.local/share/icons/hicolor/scalable/apps/camouwolf.svg
#
# Usage (from the bundle root, where you unzipped it):
#   ./linux-desktop/install-desktop.sh              # install (or refresh)
#   ./linux-desktop/install-desktop.sh --uninstall  # remove it
#
# Re-run it if you move the bundle — it rewrites the paths.

set -euo pipefail

# Resolve the directory this script lives in, following symlinks, so Exec/Icon
# point at the real unzipped location rather than wherever it was invoked from.
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    dir="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$dir/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"

# release-camouwolf.sh copies settings/linux-desktop/ to the bundle root, so we
# normally live one level down at <bundle>/linux-desktop/. Support being run
# from the bundle root too by picking whichever directory holds the binary.
if [[ -e "$SCRIPT_DIR/camouwolf" ]]; then
    BUNDLE_DIR="$SCRIPT_DIR"
else
    BUNDLE_DIR="$(dirname "$SCRIPT_DIR")"
fi

APP_ID="camouwolf"
BIN="$BUNDLE_DIR/camouwolf"
ICON_SRC_DIR="$BUNDLE_DIR/linux-desktop/icons"
DESKTOP_TEMPLATE="$BUNDLE_DIR/linux-desktop/camouwolf.desktop"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS_DIR="$DATA_HOME/applications"
ICONS_ROOT="$DATA_HOME/icons/hicolor"
DESKTOP_DEST="$APPS_DIR/${APP_ID}.desktop"

# Must match DESKTOP_ICON_SIZES in browser/scripts/generate-branding.py.
ICON_SIZES=(16 22 24 32 48 64 128 256)

uninstall() {
    echo "Removing Camouwolf launcher..."
    rm -f "$DESKTOP_DEST"
    rm -f "$ICONS_ROOT/scalable/apps/${APP_ID}.svg"
    for s in "${ICON_SIZES[@]}"; do
        rm -f "$ICONS_ROOT/${s}x${s}/apps/${APP_ID}.png"
    done
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" 2>/dev/null || true
    command -v gtk-update-icon-cache   >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$ICONS_ROOT" 2>/dev/null || true
    echo "Done. The Camouwolf launcher has been removed."
    exit 0
}

[[ "${1:-}" == "--uninstall" || "${1:-}" == "-u" ]] && uninstall

# ---- sanity checks -----------------------------------------------------
if [[ ! -x "$BIN" ]]; then
    echo "ERROR: camouwolf binary not found or not executable at: $BIN" >&2
    echo "Resolved bundle root as: $BUNDLE_DIR (script lives in $SCRIPT_DIR)" >&2
    echo "Keep this script inside the unzipped bundle so it can find the binary." >&2
    exit 1
fi
if [[ ! -f "$DESKTOP_TEMPLATE" || ! -d "$ICON_SRC_DIR" ]]; then
    echo "ERROR: desktop template or icons missing under $BUNDLE_DIR/linux-desktop/." >&2
    echo "This bundle may be incomplete; re-download it." >&2
    exit 1
fi

# ---- install icons into the hicolor theme ------------------------------
echo "Installing Camouwolf icons into $ICONS_ROOT ..."
# Scalable SVG first — modern GNOME/librsvg prefers it and renders crisp at any
# scale/HiDPI. The sized PNGs below are the fallback for DEs without SVG support.
if [[ -f "$ICON_SRC_DIR/camouwolf.svg" ]]; then
    dest_dir="$ICONS_ROOT/scalable/apps"
    mkdir -p "$dest_dir"
    cp -f "$ICON_SRC_DIR/camouwolf.svg" "$dest_dir/${APP_ID}.svg"
fi
for s in "${ICON_SIZES[@]}"; do
    src="$ICON_SRC_DIR/camouwolf_${s}.png"
    [[ -f "$src" ]] || continue
    dest_dir="$ICONS_ROOT/${s}x${s}/apps"
    mkdir -p "$dest_dir"
    cp -f "$src" "$dest_dir/${APP_ID}.png"
done

# ---- write the .desktop with resolved paths ----------------------------
echo "Installing launcher into $DESKTOP_DEST ..."
mkdir -p "$APPS_DIR"
# Reference the icon by theme name ("camouwolf") — the DE resolves it from the
# hicolor icons we just installed and picks the best size per context.
sed -e "s|@EXEC@|$BIN|g" \
    -e "s|@ICON@|$APP_ID|g" \
    "$DESKTOP_TEMPLATE" > "$DESKTOP_DEST"
chmod 644 "$DESKTOP_DEST"

# ---- refresh caches so it shows up without a re-login ------------------
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache   >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$ICONS_ROOT" 2>/dev/null || true

# ---- AppArmor: the sandbox needs a profile on Ubuntu 23.10+ -------------
# Without this Camouwolf aborts at startup with "No usable sandbox!" — the app grid
# icon appears and does nothing, because the failure happens before any window.
# We cannot install it ourselves: /etc/apparmor.d is root-owned and this script
# is deliberately unprivileged. So resolve the profile here and print the exact
# commands, rather than letting the user discover the crash by clicking.
APPARMOR_TEMPLATE="$BUNDLE_DIR/linux-desktop/apparmor-camouwolf.in"
APPARMOR_STAGED="$BUNDLE_DIR/linux-desktop/apparmor-camouwolf"
userns_restricted() {
    [[ "$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null)" == "1" ]]
}
if userns_restricted && [[ -f "$APPARMOR_TEMPLATE" ]]; then
    sed -e "s|@EXEC@|$BIN|g" "$APPARMOR_TEMPLATE" > "$APPARMOR_STAGED"
    if [[ -e /etc/apparmor.d/camouwolf ]]; then
        echo ""
        echo "NOTE: /etc/apparmor.d/camouwolf already exists. If you moved the bundle,"
        echo "      refresh it so it points at the new path:"
        echo "        sudo cp '$APPARMOR_STAGED' /etc/apparmor.d/camouwolf"
        echo "        sudo apparmor_parser -r /etc/apparmor.d/camouwolf"
    else
        echo ""
        echo "ACTION REQUIRED — this system restricts unprivileged user namespaces"
        echo "(kernel.apparmor_restrict_unprivileged_userns=1), so Camouwolf cannot start"
        echo "its sandbox and will exit immediately when launched. Install the"
        echo "AppArmor profile once, the same way Chrome's .deb does:"
        echo ""
        echo "  sudo cp '$APPARMOR_STAGED' /etc/apparmor.d/camouwolf"
        echo "  sudo apparmor_parser -r /etc/apparmor.d/camouwolf"
        echo ""
    fi
fi

echo ""
echo "Done. 'Camouwolf' should now appear in your app grid / dock with its logo."
echo "  - If it doesn't appear immediately, log out and back in (GNOME rescans on login)."
echo "  - To pin it: open it, right-click its dock icon, 'Add to Favorites'."
echo "  - Moved the bundle? Re-run this script to fix the paths."
echo "  - To remove: ./linux-desktop/install-desktop.sh --uninstall"
