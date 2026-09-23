#!/usr/bin/env bash
# Copies the real (gitignored) config files -- physicians.yaml,
# scheduler_config.yaml, bytebloc.yaml, email.yaml, sked.yaml -- to the
# Unraid backend's bind-mounted config folder. `git pull` never touches
# these (they're gitignored on purpose, real org data + secrets); this is
# the other half of getting Unraid in sync. Run it any time after editing
# one of these locally, before or after rebuilding the container (see the
# update-unraid-backend skill).
#
# Override the destination via env vars if these don't match your setup:
#   UNRAID_HOST=user@host UNRAID_CONFIG_DIR=/path/to/config ./sync-config-to-unraid.sh

set -euo pipefail

UNRAID_HOST="${UNRAID_HOST:-root@192.168.0.5}"
UNRAID_CONFIG_DIR="${UNRAID_CONFIG_DIR:-/mnt/user/appdata/kea-scheduler/config}"
LOCAL_CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scheduler/config" && pwd)"

FILES=(physicians.yaml scheduler_config.yaml bytebloc.yaml email.yaml sked.yaml)

to_sync=()
for f in "${FILES[@]}"; do
  if [[ -f "$LOCAL_CONFIG_DIR/$f" ]]; then
    to_sync+=("$LOCAL_CONFIG_DIR/$f")
  else
    echo "skip: $f not found locally"
  fi
done

if [[ ${#to_sync[@]} -eq 0 ]]; then
  echo "Nothing to sync -- no real config files found in $LOCAL_CONFIG_DIR" >&2
  exit 1
fi

echo "Syncing to $UNRAID_HOST:$UNRAID_CONFIG_DIR/ ..."
rsync -avz --progress "${to_sync[@]}" "$UNRAID_HOST:$UNRAID_CONFIG_DIR/"
echo "Done."
