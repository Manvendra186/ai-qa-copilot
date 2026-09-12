#!/usr/bin/env bash
# AI QA Copilot — restore (build bible §19 S8.5).
#
#   scripts/restore.sh                                  # newest backup
#   scripts/restore.sh backups/qa-copilot-backup-X.tar.gz
#   RESTORE_DIR=/opt/qa scripts/restore.sh <backup>     # custom target
#
# Reads (env or the repo's .env): DATABASE_URL / PGPASSWORD (as in backup.sh).
#
# Behaviour:
#   1. extract the backup bundle
#   2. restore the database with pg_restore in a SINGLE transaction
#      (--single-transaction + --clean + --if-exists: either the whole
#      schema comes back or nothing does — no half-restored state)
#   3. restore the artifacts tree (when the bundle carries one) into
#      $ARTIFACTS_DIR (default ./data/artifacts, or $RESTORE_DIR/artifacts)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$REPO_ROOT/data/artifacts}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# --- locate the backup bundle ------------------------------------------------
if [ "$#" -ge 1 ]; then
  BACKUP="$1"
else
  BACKUP="$(ls -1t "$BACKUP_DIR"/qa-copilot-backup-*.tar.gz 2>/dev/null | head -n1 || true)"
fi
[ -n "${BACKUP:-}" ] && [ -f "$BACKUP" ] || {
  echo "error: no backup found (pass a path or put one in $BACKUP_DIR)" >&2
  exit 1
}
echo "restoring from: $BACKUP"

# --- configuration ------------------------------------------------------------
# Load the repo's .env (CRLF-safe, same loader as backup.sh; the caller's
# environment always wins over .env values).
if [ -f "$REPO_ROOT/.env" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    line="${line#export }"
    case "$line" in '' | '#'*) continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    [ -n "$key" ] || continue
    if [ -z "${!key:-}" ]; then
      export "$key=$value"
    fi
  done < "$REPO_ROOT/.env"
fi

command -v pg_restore >/dev/null 2>&1 || {
  echo "error: pg_restore not found on PATH (install the Postgres client tools)" >&2
  exit 1
}

PG_URL="${DATABASE_URL%%+*}"
if [ -z "${PG_URL:-}" ]; then
  PG_URL="postgresql://${PGUSER:-qa}@${PGHOST:-localhost}:${PGPORT:-5432}/${PGDATABASE:-qa_copilot}"
fi
export PGPASSWORD="${PGPASSWORD:-}"

# --- extract ------------------------------------------------------------------
tar -xzf "$BACKUP" -C "$STAGE"
if [ -f "$STAGE/MANIFEST" ]; then
  echo "manifest:"
  sed 's/^/  /' "$STAGE/MANIFEST"
fi
[ -f "$STAGE/db.dump" ] || {
  echo "error: bundle has no db.dump" >&2
  exit 1
}

# --- database (single transaction) ---------------------------------------------
echo "restoring database (single transaction) → $PG_URL"
pg_restore --clean --if-exists --single-transaction --no-owner --no-privileges \
  --dbname="$PG_URL" "$STAGE/db.dump"

# --- artifacts -----------------------------------------------------------------
if [ -f "$STAGE/artifacts.tgz" ]; then
  mkdir -p "$ARTIFACTS_DIR"
  # The archive stores the artifacts directory by its basename; extract it
  # so that directory lands at the parent of $ARTIFACTS_DIR.
  tar -xzf "$STAGE/artifacts.tgz" -C "$(dirname "$ARTIFACTS_DIR")"
  echo "artifacts restored to: $ARTIFACTS_DIR"
else
  echo "bundle has no artifacts to restore"
fi

echo "restore complete"
