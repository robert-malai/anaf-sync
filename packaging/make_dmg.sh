#!/usr/bin/env bash
# Wrap the frozen .app in a distributable disk image.
#
#     packaging/make_dmg.sh dist/anaf-sync-tray.app dist/anaf-sync-tray-macos-arm64.dmg
#
# `hdiutil` only, no `create-dmg` dependency: what makes a .dmg worth shipping
# over a .zip is the /Applications symlink beside the app — the drag gesture is
# the whole install instruction, and it needs no tooling to produce.
#
# The image is compressed (UDZO) and read-only, which is what Finder expects
# for a downloaded app and what keeps the download small.
set -euo pipefail

app="${1:?usage: make_dmg.sh <path/to/app.app> <output.dmg>}"
out="${2:?usage: make_dmg.sh <path/to/app.app> <output.dmg>}"

[ -d "${app}" ] || { echo "no such bundle: ${app}" >&2; exit 1; }

staging="$(mktemp -d)"
trap 'rm -rf "${staging}"' EXIT

# ditto, not cp: it is the only copy that reliably preserves a bundle's
# symlinks, permissions and extended attributes — and a mangled framework
# symlink inside Contents/Frameworks is a bundle that will not launch.
ditto "${app}" "${staging}/$(basename "${app}")"
ln -s /Applications "${staging}/Applications"

rm -f "${out}"
hdiutil create \
    -volname "anaf-sync" \
    -srcfolder "${staging}" \
    -fs HFS+ \
    -format UDZO \
    -ov \
    "${out}"

echo "built ${out}"
