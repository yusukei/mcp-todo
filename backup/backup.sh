#!/bin/bash
set -euo pipefail

BACKUP_DIR="/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
WORK_DIR=$(mktemp -d)
BACKUP_FILE="${BACKUP_DIR}/backup_${TIMESTAMP}.zip"

echo "[$(date)] Starting backup..."

# 1. mongodump
mongodump \
    --uri="${MONGO_URI}" \
    --db="${MONGO_DBNAME}" \
    --gzip \
    --archive="${WORK_DIR}/db.agz" \
    --quiet

# 2. Create zip with db dump + assets
cd "${WORK_DIR}"
zip -q "${BACKUP_FILE}" db.agz

if [ -d "/data/docsite_assets" ] && [ "$(ls -A /data/docsite_assets 2>/dev/null)" ]; then
    cd /data
    zip -qr "${BACKUP_FILE}" docsite_assets/
fi

if [ -d "/data/bookmark_assets" ] && [ "$(ls -A /data/bookmark_assets 2>/dev/null)" ]; then
    cd /data
    zip -qr "${BACKUP_FILE}" bookmark_assets/
fi

# 3. Cleanup work directory
rm -rf "${WORK_DIR}"

# 4. Rotate old backups
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
find "${BACKUP_DIR}" -name "backup_*.zip" -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true

# 5. Report
SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
COUNT=$(find "${BACKUP_DIR}" -name "backup_*.zip" | wc -l)
echo "[$(date)] Backup completed: ${BACKUP_FILE} (${SIZE}, ${COUNT} total)"
