#!/usr/bin/env bash
# Manage the two independent host Pi worker services from this repository.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./host-worker.sh install|reinstall <dev|prod|all> [--no-start]
  ./host-worker.sh start|stop|restart|status|logs <dev|prod|all> [--lines N]

Examples:
  ./host-worker.sh reinstall all
  ./host-worker.sh restart prod
  ./host-worker.sh logs dev --lines 150

install/reinstall reads <data-root>/host_worker.json, copies the versioned worker
sources from this scripts directory to the shared /opt/journl-agent-worker base,
and updates the selected systemd unit. A missing config is created as a template.
DEV and PROD remain separate services with separate data roots. --no-start is only
valid for install/reinstall and installs selected services disabled and stopped.
EOF
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  usage
  exit 0
fi

interactive=false
[[ $# -eq 0 ]] && interactive=true
action=${1:-}
environment=${2:-}
shift $(( $# >= 2 ? 2 : $# ))
no_start=false
lines=100

while (($#)); do
  case "$1" in
    --no-start) no_start=true; shift ;;
    --lines) lines=${2:?missing value for --lines}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unbekannte Option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if "$interactive"; then
  echo "Journl Host-Worker Verwaltung"
  echo "  1) Installieren / aktualisieren"
  echo "  2) Starten und aktivieren"
  echo "  3) Stoppen und deaktivieren"
  echo "  4) Neu starten"
  echo "  5) Status anzeigen"
  echo "  6) Letzte Logs anzeigen"
  read -r -p "Aktion [1-6]: " choice
  case "$choice" in
    1) action=reinstall ;;
    2) action=start ;;
    3) action=stop ;;
    4) action=restart ;;
    5) action=status ;;
    6) action=logs ;;
    *) echo "Ungültige Aktion." >&2; exit 2 ;;
  esac
  echo "  1) DEV"
  echo "  2) PROD"
  echo "  3) DEV und PROD"
  read -r -p "Umgebung [1-3]: " choice
  case "$choice" in
    1) environment=dev ;;
    2) environment=prod ;;
    3) environment=all ;;
    *) echo "Ungültige Umgebung." >&2; exit 2 ;;
  esac
  if [[ "$action" =~ ^(install|reinstall)$ ]]; then
    echo "Startverhalten wird aus host_worker.json übernommen (oder mit --no-start überschrieben)."
  elif [[ "$action" == logs ]]; then
    read -r -p "Anzahl Logzeilen [100]: " answer
    lines=${answer:-100}
  fi
fi

[[ "$action" =~ ^(install|reinstall|start|stop|restart|status|logs)$ ]] || { usage >&2; exit 2; }
[[ "$environment" =~ ^(dev|prod|all)$ ]] || { usage >&2; exit 2; }
[[ "$lines" =~ ^[1-9][0-9]*$ ]] || { echo '--lines muss positiv sein.' >&2; exit 2; }
if [[ "$action" =~ ^(install|reinstall)$ ]]; then
  :
elif [[ "$no_start" == true ]]; then
  echo '--no-start gilt nur für install/reinstall.' >&2; exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
targets=("$environment")
[[ "$environment" == all ]] && targets=(dev prod)

for env in "${targets[@]}"; do
  service="journl-agent-worker-$env.service"
  case "$action" in
    install|reinstall)
      args=(sudo "$script_dir/install-run-agent-session.sh" --environment "$env")
      [[ "$no_start" == true ]] && args+=(--no-start)
      "${args[@]}"
      ;;
    start) sudo systemctl enable --now "$service" ;;
    stop) sudo systemctl disable --now "$service" ;;
    restart) sudo systemctl restart "$service" ;;
    status) sudo systemctl --no-pager status "$service" ;;
    logs) sudo journalctl -u "$service" -n "$lines" --no-pager ;;
  esac
done
