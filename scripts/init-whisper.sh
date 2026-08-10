#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Einmalige Initialisierung des privaten Journl-Whisper-Dienstes.

Usage:
  sudo ./scripts/init-whisper.sh dev [--no-model-download]
  sudo ./scripts/init-whisper.sh prod [--no-model-download]

Das Skript ergänzt ausschließlich fehlende Whisper-Werte in der persistenten
.env, erzeugt bei Bedarf einen API-Key, startet API und Scheduler und prüft sie.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
    echo "Fehler: Bitte mit sudo ausführen." >&2
    usage >&2
    exit 1
}

target="${1:-}"
skip_model=false
[[ $# -gt 0 ]] && shift
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-model-download) skip_model=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Fehler: Unbekannte Option: $1" >&2; usage >&2; exit 1 ;;
    esac
    shift
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
case "$target" in
    dev)
        expected_root="/docker-storage/Journal-Tracker-DEV"
        compose_file="docker-compose.dev.yml"
        env_file="/docker-storage/my_Journal_data_DEV/data/journals/.env"
        whisper_service="whisper-dev"
        scheduler_service="whisper-scheduler-dev"
        default_port="8091"
        ;;
    prod)
        expected_root="/docker-storage/Journal-Tracker"
        compose_file="docker-compose.yml"
        env_file="/docker-storage/my_Journal_data/data/journals/.env"
        whisper_service="whisper"
        scheduler_service="whisper-scheduler"
        default_port="8090"
        ;;
    *) echo "Fehler: Ziel muss dev oder prod sein." >&2; usage >&2; exit 1 ;;
esac

[[ "$repo_root" == "$expected_root" ]] || {
    echo "Fehler: '$target' muss aus $expected_root initialisiert werden (aktuell: $repo_root)." >&2
    exit 1
}
[[ -f "$repo_root/$compose_file" ]] || { echo "Fehler: $compose_file fehlt." >&2; exit 1; }

mkdir -p "$(dirname -- "$env_file")"
[[ -e "$env_file" ]] || install -m 600 /dev/null "$env_file"
[[ -f "$env_file" && ! -L "$env_file" ]] || {
    echo "Fehler: $env_file muss eine reguläre Datei und darf kein Symlink sein." >&2
    exit 1
}

env_uid="$(stat -c '%u' "$env_file")"
env_gid="$(stat -c '%g' "$env_file")"
env_mode="$(stat -c '%a' "$env_file")"
work_file="$(mktemp)"
next_file="$(mktemp)"
trap 'rm -f -- "$work_file" "$next_file"' EXIT
cp -- "$env_file" "$work_file"

set_env_value() {
    local key="$1" value="$2" mode="${3:-missing}"
    awk -v wanted="$key" -v replacement="$value" -v replace_mode="$mode" '
        BEGIN { found = 0 }
        $0 ~ "^[[:space:]]*" wanted "=" {
            if (found) next
            found = 1
            current = $0
            sub("^[[:space:]]*" wanted "=", "", current)
            if (replace_mode == "placeholder" && (current == "" || current ~ /^<.*>$/))
                print wanted "=" replacement
            else
                print $0
            next
        }
        { print }
        END { if (!found) print wanted "=" replacement }
    ' "$work_file" > "$next_file"
    mv -- "$next_file" "$work_file"
    next_file="$(mktemp)"
}

generated_key="$(openssl rand -hex 32)"
set_env_value WHISPER_API_KEY "$generated_key" placeholder
set_env_value WHISPER_MODEL base
set_env_value WHISPER_COMPUTE_TYPE int8
set_env_value WHISPER_CPU_THREADS 4
set_env_value WHISPER_PORT "$default_port"
set_env_value WHISPER_SCHEDULE_HOUR 11
set_env_value WHISPER_LANGUAGE de
install -o "$env_uid" -g "$env_gid" -m "$env_mode" "$work_file" "$env_file"

api_key="$(awk -F= '$1 == "WHISPER_API_KEY" { sub(/^[^=]*=/, ""); print; exit }' "$env_file")"
port="$(awk -F= '$1 == "WHISPER_PORT" { sub(/^[^=]*=/, ""); print; exit }' "$env_file")"
[[ -n "$api_key" ]] || { echo "Fehler: WHISPER_API_KEY ist leer." >&2; exit 1; }
[[ "$port" =~ ^[0-9]+$ ]] || { echo "Fehler: WHISPER_PORT ist keine Zahl." >&2; exit 1; }

compose=(docker compose --env-file "$env_file" -f "$repo_root/$compose_file" --profile whisper)
echo "Starte Whisper-API und 11-Uhr-Scheduler für $target ..."
"${compose[@]}" up -d --build "$whisper_service" "$scheduler_service"

health_url="http://127.0.0.1:$port/health"
for attempt in $(seq 1 60); do
    if curl --fail --silent --show-error "$health_url" >/dev/null; then
        break
    fi
    if [[ "$attempt" -eq 60 ]]; then
        echo "Fehler: Whisper ist auf $health_url nicht gesund." >&2
        "${compose[@]}" logs --tail=50 "$whisper_service" >&2
        exit 1
    fi
    sleep 2
done

if [[ "$skip_model" == false ]]; then
    echo "Lade das konfigurierte Modell einmalig herunter und in den CPU-Speicher ..."
    curl --fail --silent --show-error --max-time 21600 \
        -X POST -H "Authorization: Bearer $api_key" \
        "http://127.0.0.1:$port/v1/models/load" >/dev/null
fi

echo "Whisper für $target ist bereit: $health_url"
echo "Der API-Key wurde in $env_file hinterlegt und nicht ausgegeben."
echo "Status: cd $repo_root && docker compose --env-file $env_file -f $compose_file --profile whisper ps"
