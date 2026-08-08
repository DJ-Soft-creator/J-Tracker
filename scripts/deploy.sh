#!/bin/bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

TARGET=""; UPDATE_TARGET=""; COMMIT_MESSAGE=""; VERSION_BUMP=""; NO_BUILD=false; FORCE_RECREATE=false; DEPLOY_AFTER_GIT_UPDATE=false
DEV_DIR="${DEV_DIR:-/opt/journal-tracker-dev}"
PROD_DIR="${PROD_DIR:-/opt/journal-tracker}"
INTERACTIVE=false
[[ $# -eq 0 ]] && INTERACTIVE=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        dev|prod|promote) TARGET="$1"; shift ;;
        git-update)
            TARGET="$1"; shift
            [[ $# -gt 0 && ( "$1" == "dev" || "$1" == "prod" || "$1" == "both" ) ]] || {
                log_error "git-update erwartet dev, prod oder both."; exit 1;
            }
            UPDATE_TARGET="$1"; shift ;;
        --deploy) DEPLOY_AFTER_GIT_UPDATE=true; shift ;;
        --no-build) NO_BUILD=true; shift ;;
        --force-recreate) FORCE_RECREATE=true; shift ;;
        --message)
            [[ $# -ge 2 ]] || { log_error "--message erwartet eine Commit-Nachricht."; exit 1; }
            COMMIT_MESSAGE="$2"; shift 2 ;;
        --version-bump)
            [[ $# -ge 2 && ( "$2" == "patch" || "$2" == "minor" || "$2" == "major" ) ]] || {
                log_error "--version-bump erwartet patch, minor oder major."; exit 1;
            }
            VERSION_BUMP="$2"; shift 2 ;;
        -h|--help)
            echo "Ohne Parameter startet ein interaktives Menue."
            echo "Usage: $0 [dev|prod|promote] [--no-build] [--force-recreate]"
            echo "       $0 promote --version-bump patch|minor|major"
            echo "       $0 git-update [dev|prod|both] [--message \"Commit-Nachricht\"] [--deploy]"
            echo "         --deploy ist nur mit 'git-update dev' erlaubt und deployt anschliessend DEV."
            exit 0 ;;
        *) log_error "Unbekannter Parameter: $1"; exit 1 ;;
    esac
done

if [[ -z "$TARGET" && "$INTERACTIVE" == false ]]; then
    log_error "Kein Ziel angegeben!"
    echo "Verwende: $0 [dev|prod|promote] [--no-build] [--force-recreate]"
    exit 1
fi

[[ "$DEPLOY_AFTER_GIT_UPDATE" == false || "$TARGET" == "git-update" ]] || {
    log_error "--deploy ist nur mit 'git-update dev' erlaubt."; exit 1;
}

[[ -z "$VERSION_BUMP" || "$TARGET" == "promote" ]] || {
    log_error "--version-bump ist nur mit 'promote' erlaubt."; exit 1;
}

configure_target() {
    case "$1" in
        dev)
            BASE_DIR="$DEV_DIR"; GIT_BRANCH="dev-environment"; COMPOSE_FILE="docker-compose.dev.yml"
            SERVICE="journaling-tracker-dev"; ENV_FILE="/path/to/journal-data-dev/.env" ;;
        prod)
            BASE_DIR="$PROD_DIR"; GIT_BRANCH="prod"; COMPOSE_FILE="docker-compose.yml"
            SERVICE="journaling-tracker"; ENV_FILE="/path/to/journal-data/.env" ;;
    esac
}

ensure_clean_branch() {
    local directory="$1" branch="$2"
    [[ -d "$directory/.git" ]] || { log_error "Kein Git-Repository: $directory"; exit 1; }
    [[ "$(git -C "$directory" branch --show-current)" == "$branch" ]] || {
        log_error "$directory muss auf Branch '$branch' ausgecheckt sein."; exit 1;
    }
    git -C "$directory" diff --quiet && git -C "$directory" diff --cached --quiet || {
        log_error "Versionierte lokale Änderungen in $directory. Bitte committen oder verwerfen."; exit 1;
    }
}

update_branch() {
    local directory="$1" branch="$2"
    ensure_clean_branch "$directory" "$branch"
    git -C "$directory" fetch origin "+refs/heads/$branch:refs/remotes/origin/$branch"
    if [[ "$(git -C "$directory" rev-parse HEAD)" != "$(git -C "$directory" rev-parse "origin/$branch")" ]]; then
        git -C "$directory" merge-base --is-ancestor HEAD "origin/$branch" || {
            log_error "Lokaler Branch '$branch' weicht von origin/$branch ab."; exit 1;
        }
        git -C "$directory" merge --ff-only "origin/$branch"
    fi
}

deploy() {
    configure_target "$1"
    log_info "Deploy $1 (Branch: $GIT_BRANCH, Verzeichnis: $BASE_DIR)..."
    update_branch "$BASE_DIR" "$GIT_BRANCH"
    [[ -f "$BASE_DIR/$COMPOSE_FILE" ]] || { log_error "Compose-Datei fehlt: $BASE_DIR/$COMPOSE_FILE"; exit 1; }
    [[ -f "$ENV_FILE" ]] || { log_error "Umgebungsdatei fehlt: $ENV_FILE"; exit 1; }

    local compose=(sudo docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
    local recreate=()
    [[ "$FORCE_RECREATE" == true ]] && recreate+=(--force-recreate)
    if [[ "$NO_BUILD" == true ]]; then
        (cd "$BASE_DIR" && "${compose[@]}" up -d "${recreate[@]}")
    else
        (cd "$BASE_DIR" && "${compose[@]}" up -d --build "${recreate[@]}")
    fi

    sleep 5
    if (cd "$BASE_DIR" && "${compose[@]}" ps --status running --services | grep -Fxq "$SERVICE"); then
        log_success "$1 Deployment erfolgreich!"
    else
        log_error "$1 Deployment fehlgeschlagen!"
        (cd "$BASE_DIR" && "${compose[@]}" logs --tail=20)
        exit 1
    fi
}

promote() {
    [[ "$NO_BUILD" == false && "$FORCE_RECREATE" == false ]] || {
        log_error "Optionen sind fuer 'promote' nicht erlaubt."; exit 1;
    }
    [[ -n "$VERSION_BUMP" ]] || {
        log_error "promote erfordert --version-bump patch, minor oder major."; exit 1;
    }
    log_info "Promote dev-environment nach prod..."
    update_branch "$DEV_DIR" "dev-environment"
    update_branch "$PROD_DIR" "prod"
    git -C "$DEV_DIR" fetch origin "+refs/heads/prod:refs/remotes/origin/prod"
    git -C "$DEV_DIR" merge-base --is-ancestor "origin/prod" HEAD || {
        log_error "prod ist nicht Vorfahr von dev-environment; Promotion waere kein Fast-Forward."; exit 1;
    }

    local version_file="$DEV_DIR/app/VERSION" current major minor patch next
    [[ -f "$version_file" ]] || { log_error "Versionsdatei fehlt: $version_file"; exit 1; }
    current="$(<"$version_file")"
    [[ "$current" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || {
        log_error "Ungueltige Version '$current' in $version_file (erwartet: X.Y.Z)."; exit 1;
    }
    major=$((10#${BASH_REMATCH[1]}))
    minor=$((10#${BASH_REMATCH[2]}))
    patch=$((10#${BASH_REMATCH[3]}))
    case "$VERSION_BUMP" in
        patch) ((patch += 1)) ;;
        minor) ((minor += 1)); patch=0 ;;
        major) ((major += 1)); minor=0; patch=0 ;;
    esac
    next="$major.$minor.$patch"
    printf '%s\n' "$next" > "$version_file"
    git -C "$DEV_DIR" add app/VERSION
    git -C "$DEV_DIR" commit -m "chore: bump version to $next"
    git -C "$DEV_DIR" push origin HEAD:refs/heads/dev-environment
    git -C "$DEV_DIR" push origin HEAD:refs/heads/prod
    log_success "Version von $current auf $next erhoeht und nach PROD promotet."
    deploy prod
}

git_update() {
    local target="$1" directory branch message
    case "$target" in
        dev) directory="$DEV_DIR"; branch="dev-environment" ;;
        prod) directory="$PROD_DIR"; branch="prod" ;;
    esac
    [[ -d "$directory/.git" ]] || { log_error "Kein Git-Repository: $directory"; exit 1; }
    [[ "$(git -C "$directory" branch --show-current)" == "$branch" ]] || {
        log_error "$directory muss auf Branch '$branch' ausgecheckt sein."; exit 1;
    }
    git -C "$directory" fetch origin "+refs/heads/$branch:refs/remotes/origin/$branch"
    git -C "$directory" merge-base --is-ancestor "origin/$branch" HEAD || {
        log_error "$branch ist nicht auf dem aktuellen Remote-Stand. Erst Remote-Aenderungen integrieren."; exit 1;
    }
    git -C "$directory" add -A
    if git -C "$directory" diff --cached --quiet; then
        log_info "Keine Aenderungen zum Committen in $target."
        return
    fi
    message="$COMMIT_MESSAGE"
    if [[ -z "$message" ]]; then
        read -r -p "Commit-Nachricht fuer $target ($branch): " message
        [[ -n "$message" ]] || { log_error "Commit-Nachricht darf nicht leer sein."; exit 1; }
    fi
    git -C "$directory" commit -m "$message"
    git -C "$directory" push origin "$branch"
    log_success "$target nach origin/$branch gepusht."
}

choose_action() {
    [[ -t 0 ]] || { log_error "Ohne Parameter ist ein interaktives Terminal erforderlich."; exit 1; }
    echo ""
    echo "Was moechtest du ausfuehren?"
    select action in "DEV deployen" "PROD deployen" "DEV committen, pushen und deployen" "DEV committen und pushen" "PROD committen und pushen" "Beide committen und pushen" "DEV nach PROD promoten" "Abbrechen"; do
        case "$REPLY" in
            1|2)
                [[ "$REPLY" == 1 ]] && TARGET=dev || TARGET=prod
                read -r -p "Image neu bauen? [J/n]: " build
                [[ "$build" =~ ^[Nn]$ ]] && NO_BUILD=true
                read -r -p "Container erzwingen neu erstellen? [j/N]: " recreate
                [[ "$recreate" =~ ^[JjYy]$ ]] && FORCE_RECREATE=true
                break ;;
            3) TARGET=git-update; UPDATE_TARGET=dev; DEPLOY_AFTER_GIT_UPDATE=true; break ;;
            4) TARGET=git-update; UPDATE_TARGET=dev; break ;;
            5) TARGET=git-update; UPDATE_TARGET=prod; break ;;
            6) TARGET=git-update; UPDATE_TARGET=both; break ;;
            7)
                TARGET=promote
                while [[ -z "$VERSION_BUMP" ]]; do
                    read -r -p "Versionssprung [patch/minor/major]: " VERSION_BUMP
                    [[ "$VERSION_BUMP" == "patch" || "$VERSION_BUMP" == "minor" || "$VERSION_BUMP" == "major" ]] || {
                        log_warn "Bitte patch, minor oder major eingeben."; VERSION_BUMP="";
                    }
                done
                break ;;
            8) log_info "Abgebrochen."; exit 0 ;;
            *) log_warn "Bitte eine gueltige Nummer waehlen." ;;
        esac
    done
}

[[ "$INTERACTIVE" == true ]] && choose_action

if [[ "$TARGET" == "git-update" ]]; then
    [[ "$DEPLOY_AFTER_GIT_UPDATE" == false || "$UPDATE_TARGET" == "dev" ]] || {
        log_error "--deploy ist nur mit 'git-update dev' erlaubt."; exit 1;
    }
    [[ "$DEPLOY_AFTER_GIT_UPDATE" == true || ( "$NO_BUILD" == false && "$FORCE_RECREATE" == false ) ]] || {
        log_error "Build-Optionen sind nur mit 'git-update dev --deploy' erlaubt."; exit 1;
    }
    if [[ "$UPDATE_TARGET" == "both" ]]; then
        git_update dev
        git_update prod
    else
        git_update "$UPDATE_TARGET"
    fi
    [[ "$DEPLOY_AFTER_GIT_UPDATE" == true ]] && deploy dev
elif [[ "$TARGET" == "promote" ]]; then
    promote
else
    deploy "$TARGET"
fi
