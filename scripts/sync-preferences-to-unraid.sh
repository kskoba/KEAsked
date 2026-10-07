#!/usr/bin/env bash
# Mirrors one month's physician preference submissions (the per-physician
# xlsx files a directory import reads) to the Unraid backend's config
# folder, so a remote solve imports exactly what you have locally. Companion
# to sync-config-to-unraid.sh, which handles the yaml config files.
#
#   ./scripts/sync-preferences-to-unraid.sh "/path/to/January" [RemoteSubdir]
#
# RemoteSubdir defaults to the local folder's own name, so the example above
# lands in $UNRAID_CONFIG_DIR/January/. Only *.xlsx files are copied (the
# template .xltx and any screenshots/notes in the folder are skipped), and
# the remote folder is made to MIRROR the local one -- a file you renamed
# or removed locally is removed remotely too, since a stale copy would be
# imported as a duplicate submission. A dry run is shown first; pass -y to
# skip the confirmation (needed when run non-interactively).
#
# Env overrides, same as sync-config-to-unraid.sh:
#   UNRAID_HOST=user@host UNRAID_CONFIG_DIR=/path/to/config

set -euo pipefail

UNRAID_HOST="${UNRAID_HOST:-root@192.168.0.5}"
UNRAID_CONFIG_DIR="${UNRAID_CONFIG_DIR:-/mnt/user/appdata/kea-scheduler/config}"

yes=0
args=()
for a in "$@"; do
  case "$a" in
    -y|--yes) yes=1 ;;
    *) args+=("$a") ;;
  esac
done

if [[ ${#args[@]} -lt 1 ]]; then
  echo "usage: $0 [-y] <local preferences folder> [remote subdir]" >&2
  exit 1
fi

LOCAL_DIR="${args[0]%/}"
REMOTE_SUBDIR="${args[1]:-$(basename "$LOCAL_DIR")}"
REMOTE_DIR="$UNRAID_CONFIG_DIR/$REMOTE_SUBDIR"

if [[ ! -d "$LOCAL_DIR" ]]; then
  echo "Not a directory: $LOCAL_DIR" >&2
  exit 1
fi

count=$(find "$LOCAL_DIR" -maxdepth 1 -name '*.xlsx' | wc -l)
if [[ "$count" -eq 0 ]]; then
  echo "No .xlsx files in $LOCAL_DIR -- refusing to mirror an empty folder (that would delete everything remote)." >&2
  exit 1
fi

RSYNC_OPTS=(-az --delete --include='*.xlsx' --exclude='*')

echo "Mirroring $count xlsx file(s): $LOCAL_DIR/  ->  $UNRAID_HOST:$REMOTE_DIR/"
echo "--- dry run ---"
rsync "${RSYNC_OPTS[@]}" --dry-run --itemize-changes "$LOCAL_DIR/" "$UNRAID_HOST:$REMOTE_DIR/" \
  | grep -E '^(<|>|\*deleting)' || echo "(remote already identical)"
echo "---"

if [[ $yes -ne 1 ]]; then
  ans=""
  read -r -p "Apply? [y/N] " ans || true   # no tty (e.g. run from a tool) -> treated as No
  [[ "$ans" == [yY]* ]] || { echo "Not applied (pass -y to apply without a prompt)."; exit 0; }
fi

ssh "$UNRAID_HOST" "mkdir -p '$REMOTE_DIR'"
rsync "${RSYNC_OPTS[@]}" --progress "$LOCAL_DIR/" "$UNRAID_HOST:$REMOTE_DIR/"
echo "Done. Re-run the directory import in the app (pointed at $REMOTE_DIR) for the solver to pick these up."
