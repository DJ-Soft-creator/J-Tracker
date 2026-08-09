#!/usr/bin/env bash
# Install the one-shot agent runner and its systemd worker on the Docker host.
# The worker is intentionally host-side: it uses the host user's existing Pi
# login and only receives jobs from the shared journal data directory.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  sudo ./install-run-agent-session.sh --environment dev|prod --user HOST_USER

Options:
  --environment ENV  Separate DEV or PROD service (required)
  --data-root PATH   Override the environment's host path mounted as /app/data
  --user NAME        Existing host user that owns the Pi login (required)
  --install-dir DIR  Override environment-specific installation directory
  --no-start         Install, but keep the service disabled and stopped

The service is not a cron job.  It waits continuously for explicit job files
under <data-root>/<user-id>/ai_jobs/.  It never sends editor keystrokes to Pi.
EOF
}

environment= data_root= run_user= install_dir= start_service=true
while (($#)); do
  case "$1" in
    --environment) environment=${2:?missing value}; shift 2 ;;
    --data-root) data_root=${2:?missing value}; shift 2 ;;
    --user) run_user=${2:?missing value}; shift 2 ;;
    --install-dir) install_dir=${2:?missing value}; shift 2 ;;
    --no-start) start_service=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 2; }
[[ "$environment" == dev || "$environment" == prod ]] || { echo '--environment must be dev or prod.' >&2; exit 2; }
if [[ -z "$data_root" ]]; then
  if [[ "$environment" == dev ]]; then
    data_root=/docker-storage/my_Journal_data_DEV/data/journals
  else
    data_root=/docker-storage/my_Journal_data/data/journals
  fi
fi
[[ -d "$data_root" ]] || { echo '--data-root must be an existing directory.' >&2; exit 2; }
if [[ -z "$install_dir" ]]; then install_dir="/opt/journl-agent-worker-$environment"; fi
id "$run_user" >/dev/null 2>&1 || { echo '--user must be an existing host user.' >&2; exit 2; }
run_home=$(getent passwd "$run_user" | cut -d: -f6)
[[ -n "$run_home" && -d "$run_home" ]] || { echo "Cannot determine home directory for $run_user." >&2; exit 2; }

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install -d -m 0755 "$install_dir"
install -m 0755 "$source_dir/run-agent-session.sh" "$install_dir/run-agent-session.sh"
install -m 0755 "$source_dir/journal-agent-worker.py" "$install_dir/journal-agent-worker.py"
chown -R "$run_user":"$run_user" "$install_dir"

service_name="journl-agent-worker-$environment"
cat > "/etc/systemd/system/$service_name.service" <<EOF
[Unit]
Description=Journl $environment host-side Pi agent worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$run_user
Group=$run_user
WorkingDirectory=$install_dir
Environment=HOME=$run_home
Environment=PATH=$run_home/.npm-global/bin:$run_home/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 $install_dir/journal-agent-worker.py --data-root $data_root --runner $install_dir/run-agent-session.sh
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$data_root $install_dir

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
if "$start_service"; then
  systemctl enable "$service_name.service"
  systemctl restart "$service_name.service"
else
  systemctl disable --now "$service_name.service" >/dev/null 2>&1 || true
fi

echo "Installed: $install_dir/run-agent-session.sh"
echo "Environment: $environment"
echo "Data root:    $data_root"
echo "Service:      $service_name.service"
echo "Logs:         journalctl -u $service_name -f"
echo "Status:       systemctl status $service_name"
