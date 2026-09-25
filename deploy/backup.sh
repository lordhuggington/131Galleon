#!/usr/bin/env bash
# Nightly SQLite backup. Run on the server from the repo folder, e.g. in crontab:
#   15 3 * * * cd /opt/house-run-sheet && ./deploy/backup.sh >> backups/backup.log 2>&1
# Uses SQLite's online backup API, so it is safe while the app is running.
# Optional off-site copy: set BACKUP_TARGET, e.g. u123456@u123456.your-storagebox.de:house-run-sheet/
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
stamp=$(date +%Y-%m-%d_%H%M)
out="backups/house-$stamp.db"
docker compose exec -T app python - "$stamp" <<'PY'
import sqlite3, sys
src = sqlite3.connect("/data/house.db")
dst = sqlite3.connect(f"/data/backup-{sys.argv[1]}.db")
src.backup(dst)
dst.close(); src.close()
PY
mv "data/backup-$stamp.db" "$out"
gzip -9 "$out"
echo "$(date -Is) wrote $out.gz"
find backups -name 'house-*.db.gz' -mtime +30 -delete
# Photos live on disk next to the database; copy them so the off-site rsync below picks them up.
if [[ -d data/photos ]]; then
  mkdir -p backups/photos
  rsync -a --delete data/photos/ backups/photos/
  echo "$(date -Is) synced photos into backups/photos"
fi
if [[ -n "${BACKUP_TARGET:-}" ]]; then
  rsync -a -e "ssh -p 23" backups/ "$BACKUP_TARGET"
  echo "$(date -Is) synced to $BACKUP_TARGET"
fi
