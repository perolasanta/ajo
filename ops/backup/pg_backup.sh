#!/usr/bin/env bash
# Compatibility entrypoint. The canonical backup implementation uses age and
# the environment-configured private Supabase S3 bucket.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/backup.sh" "$@"
