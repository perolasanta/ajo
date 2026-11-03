#!/usr/bin/env bash
# Shared environment-only S3/rclone configuration for backup operators.

configure_offsite() {
  : "${BACKUP_S3_ENDPOINT:?BACKUP_S3_ENDPOINT is required}"
  : "${BACKUP_S3_REGION:?BACKUP_S3_REGION is required}"
  : "${BACKUP_S3_ACCESS_KEY_ID:?BACKUP_S3_ACCESS_KEY_ID is required}"
  : "${BACKUP_S3_SECRET_ACCESS_KEY:?BACKUP_S3_SECRET_ACCESS_KEY is required}"
  : "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET is required}"

  if [[ "$BACKUP_S3_ENDPOINT" != https://* || "$BACKUP_S3_BUCKET" == */* ]]; then
    printf '%s\n' 'Invalid offsite endpoint or bucket configuration.' >&2
    return 2
  fi

  # Prevent accidental use of an operator's on-disk rclone configuration.
  export RCLONE_CONFIG=/dev/null
  export RCLONE_CONFIG_OFFSITE_TYPE=s3
  export RCLONE_CONFIG_OFFSITE_PROVIDER=Other
  export RCLONE_CONFIG_OFFSITE_ENV_AUTH=false
  export RCLONE_CONFIG_OFFSITE_ENDPOINT="$BACKUP_S3_ENDPOINT"
  export RCLONE_CONFIG_OFFSITE_REGION="$BACKUP_S3_REGION"
  export RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID"
  export RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY"
  export RCLONE_CONFIG_OFFSITE_FORCE_PATH_STYLE=true
  OFFSITE_BUCKET="offsite:${BACKUP_S3_BUCKET}"
  OFFSITE_REMOTE="offsite:${BACKUP_S3_BUCKET}/ajo"
}

require_alert_configuration() {
  : "${BACKUP_SLACK_WEBHOOK_URL:?BACKUP_SLACK_WEBHOOK_URL is required}"
  : "${BACKUP_TELEGRAM_BOT_TOKEN:?BACKUP_TELEGRAM_BOT_TOKEN is required}"
  : "${BACKUP_TELEGRAM_CHAT_ID:?BACKUP_TELEGRAM_CHAT_ID is required}"
}

send_backup_alert() {
  local event="$1"
  python3 "$ROOT/ops/backup/notify.py" "$event" >/dev/null 2>&1 || true
}
