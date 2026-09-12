#!/usr/bin/env bash
# AI QA Copilot — backup (build bible §19 S8.5: pg_dump + artifacts, gzip).
#
#   scripts/backup.sh                              # defaults
#   BACKUP_DIR=/mnt/backups scripts/backup.sh      # custom destination
#   ARTIFACTS_DIR=/data/artifacts scripts/backup.sh
#
# Reads (env or the repo's .env):
#   DATABASE_URL   — SQLAlchemy URL (postgresql+psycopg://…); the driver
#                    suffix is stripped for pg_dump. Falls back to
#                    PGHOST/PGUSER/PGDATABASE/PGPASSWORD when unset.
#   ARTIFACTS_DIR  — execution artifacts to include (default ./data/artifacts)
#
# Writes: $BACKUP_DIR/qa-copilot-backup-<UTC>.tar.gz containing
#   db.dump         — pg_dump custom format (restored by scripts/restore.sh)
#   artifacts.tgz   — the artifacts tree (present when the dir is non-empty)
#   MANIFEST        — timestamp, host, schema version, sizes

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$REPO_ROOT/data/artifacts}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# --- configuration ----------------------------------------------------------
# .env values (if present) override the caller's environment for the three
# keys below — the dev .env is the source of truth for local credentials.
# Load the repo's .env (CRLF-safe: the dev .env has Windows line endings —
# a naive `source` would leave a trailing \r in every value and break
# pg_dump). The caller's environment always wins (explicit vars override
# .env); comments, blank lines and export-style prefixes are tolerated.
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

command -v pg_dump >/dev/null 2>&1 || {
  echo "error: pg_dump not found on PATH (install the Postgres client tools)" >&2
  exit 1
}

# libpq URL: strip the SQLAlchemy driver suffix (+psycopg / +psycopg2).
PG_URL="${DATABASE_URL%%+*}"
if [ -z "${PG_URL:-}" ]; then
  PG_URL="postgresql://${PGUSER:-qa}@${PGHOST:-localhost}:${PGPORT:-5432}/${PGDATABASE:-qa_copilot}"
fi
export PGPASSWORD="${PGPASSWORD:-}"

# --- dump -------------------------------------------------------------------
DB_OUT="$STAGE/db.dump"
echo "backing up database → $DB_OUT"
pg_dump --format=custom --file="$DB_OUT" "$PG_URL"
# Informational only (never fails the backup):
echo "  server version: $(psql "$PG_URL" -tAc 'SELECT current_setting('\''server_version'\'')' 2>/dev/null || echo unknown)"

# --- artifacts (optional) ----------------------------------------------------
if [ -d "$ARTIFACTS_DIR" ] && [ -n "$(ls -A "$ARTIFACTS_DIR" 2>/dev/null)" ]; then
  echo "archiving artifacts from $ARTIFACTS_DIR"
  tar -czf "$STAGE/artifacts.tgz" -C "$(dirname "$ARTIFACTS_DIR")" "$(basename "$ARTIFACTS_DIR")"
  ARTIFACTS_PRESENT=1
else
  echo "no artifacts to archive (dir missing or empty: $ARTIFACTS_DIR)"
  ARTIFACTS_PRESENT=0
fi

# --- manifest + bundle --------------------------------------------------------
{
  echo "created_at=$TIMESTAMP"
  echo "host=$(hostname)"
  echo "artifacts_present=$ARTIFACTS_PRESENT"
  echo "db_dump_bytes=$(wc -c < "$DB_OUT")"
} > "$STAGE/MANIFEST"

FINAL="$BACKUP_DIR/qa-copilot-backup-$TIMESTAMP.tar.gz"
mkdir -p "$BACKUP_DIR"
tar -czf "$FINAL" -C "$STAGE" .

echo "backup complete: $FINAL ($(du -h "$FINAL" | cut -f1))"
