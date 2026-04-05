#!/bin/bash
set -e

CRON_SCHEDULE="${BACKUP_CRON:-0 3 * * *}"

# Write environment variables for cron to source
env | grep -E '^(MONGO_|BACKUP_)' > /etc/backup.env

# Create cron job
cat > /etc/cron.d/backup << EOF
${CRON_SCHEDULE} root . /etc/backup.env && /usr/local/bin/backup.sh >> /var/log/backup.log 2>&1
EOF
chmod 0644 /etc/cron.d/backup

# Create log file
touch /var/log/backup.log

echo "Backup cron configured: ${CRON_SCHEDULE}"
echo "Retention: ${BACKUP_RETENTION_DAYS:-7} days"

# Start cron in foreground
exec cron -f
