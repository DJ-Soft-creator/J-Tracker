#!/bin/bash
# Explicit PostgreSQL migration runner for the DEV and later PROD checkouts.
# It never deploys application code, resets a database, or enables JOURNL_DB_ENABLED.
set -euo pipefail

DEV_DIR="${DEV_DIR:-/docker-storage/Journal-Tracker-DEV}"
PROD_DIR="${PROD_DIR:-/docker-storage/Journal-Tracker}"

usage() {
    cat <<'EOF'
Usage: scripts/DB-Migrations.sh <dev|prod> <status|upgrade> [--start-db] [--confirm <environment>]

status              Read only: show the current Journl Alembic revision.
upgrade             Apply pending forward migrations. Requires an exact confirmation.
--start-db          Start only the PostgreSQL service if it is not running.
--confirm <target>  Required for upgrade: --confirm dev or --confirm prod.

The script never deploys/rebuilds the app, enables JOURNL_DB_ENABLED, downgrades,
resets, drops, or restores a database. Run it from a checkout that already contains
the migration code intended for that environment.
EOF
}

log() { printf '[db-migrations] %s\n' "$*"; }
die() { printf '[db-migrations] FEHLER: %s\n' "$*" >&2; exit 1; }

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi
[[ $# -ge 2 ]] || { usage >&2; exit 2; }
TARGET="$1"
ACTION="$2"
shift 2
START_DB=false
CONFIRM=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start-db) START_DB=true; shift ;;
        --confirm)
            [[ $# -ge 2 ]] || die "--confirm erwartet dev oder prod."
            CONFIRM="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unbekannte Option: $1" ;;
    esac
done

case "$TARGET" in
    dev)
        BASE_DIR="$DEV_DIR"
        EXPECTED_BRANCH="dev-environment"
        COMPOSE_FILE="docker-compose.dev.yml"
        ENV_FILE="/docker-storage/my_Journal_data_DEV/data/journals/.env"
        APP_SERVICE="journaling-tracker-dev"
        DB_SERVICE="postgres-dev"
        ;;
    prod)
        BASE_DIR="$PROD_DIR"
        EXPECTED_BRANCH="prod"
        COMPOSE_FILE="docker-compose.yml"
        ENV_FILE="/docker-storage/my_Journal_data/data/journals/.env"
        APP_SERVICE="journaling-tracker"
        DB_SERVICE="postgres"
        ;;
    *) die "Ziel muss dev oder prod sein." ;;
esac

case "$ACTION" in status|upgrade) ;; *) die "Aktion muss status oder upgrade sein." ;; esac
[[ "$ACTION" != "upgrade" || "$CONFIRM" == "$TARGET" ]] || {
    die "upgrade fuer $TARGET verlangt --confirm $TARGET."
}

[[ -d "$BASE_DIR/.git" ]] || die "Kein Git-Checkout: $BASE_DIR"
[[ "$(git -C "$BASE_DIR" branch --show-current)" == "$EXPECTED_BRANCH" ]] || \
    die "$BASE_DIR muss auf Branch $EXPECTED_BRANCH stehen."
[[ -f "$BASE_DIR/$COMPOSE_FILE" ]] || die "Compose-Datei fehlt: $BASE_DIR/$COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || die "Env-Datei fehlt: $ENV_FILE"
[[ -f "$BASE_DIR/app/migrations/versions/0001_db_base.py" ]] || \
    die "Keine Journl-DB-Migration im Zielcheckout gefunden."

compose=(sudo docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
services="$(cd "$BASE_DIR" && "${compose[@]}" config --services)"
grep -Fxq "$APP_SERVICE" <<<"$services" || die "App-Service $APP_SERVICE fehlt im Compose-Vertrag."
grep -Fxq "$DB_SERVICE" <<<"$services" || die "PostgreSQL-Service $DB_SERVICE fehlt im Compose-Vertrag."

if ! (cd "$BASE_DIR" && "${compose[@]}" ps --status running --services | grep -Fxq "$DB_SERVICE"); then
    if [[ "$START_DB" == true ]]; then
        log "Starte ausschliesslich $DB_SERVICE ..."
        (cd "$BASE_DIR" && "${compose[@]}" up -d "$DB_SERVICE")
    else
        die "$DB_SERVICE laeuft nicht. Mit --start-db darf nur dieser Dienst gestartet werden."
    fi
fi

if ! (cd "$BASE_DIR" && "${compose[@]}" ps --status running --services | grep -Fxq "$APP_SERVICE"); then
    die "$APP_SERVICE laeuft nicht. Das Skript startet oder deployt die App bewusst nicht."
fi

case "$ACTION" in
    status)
        log "Lese den aktuellen Migrationsstand fuer $TARGET ..."
        (cd "$BASE_DIR" && "${compose[@]}" exec -T "$APP_SERVICE" python /app/scripts/db-migrate.py current)
        ;;
    upgrade)
        log "Wende ausschliesslich Vorwaertsmigrationen in $TARGET an ..."
        (cd "$BASE_DIR" && "${compose[@]}" exec -T "$APP_SERVICE" python /app/scripts/db-migrate.py upgrade)
        log "Pruefe den resultierenden Stand ..."
        (cd "$BASE_DIR" && "${compose[@]}" exec -T "$APP_SERVICE" python /app/scripts/db-migrate.py current)
        ;;
esac
