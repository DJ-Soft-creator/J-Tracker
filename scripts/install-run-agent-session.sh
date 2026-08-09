#!/usr/bin/env bash
# Install the one-shot agent runner and its systemd worker on the Docker host.
# The worker is intentionally host-side: it uses the host user's existing Pi
# login and only receives jobs from the shared journal data directory.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  sudo ./install-run-agent-session.sh --environment dev|prod [--no-start]

Options:
  --environment ENV  Separate DEV or PROD service (required)
  --no-start         Install, but keep the service disabled and stopped

The service is not a cron job.  It waits continuously for explicit job files
under <data-root>/<user-id>/ai_jobs/.  It never sends editor keystrokes to Pi.

All persistent host settings live in <data-root>/host_worker.json beside
users.json. If it does not exist, this command creates an example file and
exits without installing a service.
EOF
}

environment= data_root= start_service_override=
while (($#)); do
  case "$1" in
    --environment) environment=${2:?missing value}; shift 2 ;;
    --no-start) start_service_override=false; shift ;;
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
config_path="$data_root/host_worker.json"
if [[ ! -e "$config_path" ]]; then
  cat > "$config_path" <<EOF
{
  "schema": 1,
  "environment": "$environment",
  "run_user": "CHANGE_ME",
  "install_dir": "/opt/journl-agent-worker",
  "worker_interval_seconds": 1.0,
  "pi_path_entries": ["~/.npm-global/bin", "~/.local/bin", "/usr/local/bin", "/usr/bin", "/bin"],
  "external_write_roots": [],
  "start_after_install": true
}
EOF
  chmod 0640 "$config_path"
  echo "Beispiel-Konfiguration erstellt: $config_path" >&2
  echo "Bitte run_user (und bei Bedarf die weiteren Werte) ausfüllen und den Befehl erneut ausführen." >&2
  exit 3
fi
[[ -f "$config_path" && ! -L "$config_path" ]] || { echo "host worker config must be a normal file: $config_path" >&2; exit 2; }
mapfile -t config_values < <(python3 - "$config_path" "$environment" <<'PY'
import json, re, sys
from pathlib import Path

path = Path(sys.argv[1])
environment = sys.argv[2]
try:
    value = json.loads(path.read_text(encoding='utf-8'))
except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f'invalid host worker config: {exc}')
if not isinstance(value, dict) or value.get('schema') != 1 or value.get('environment') != environment:
    raise SystemExit('host worker config has an invalid schema or environment')
user = value.get('run_user')
install = value.get('install_dir')
interval = value.get('worker_interval_seconds')
paths = value.get('pi_path_entries')
start = value.get('start_after_install')
roots = value.get('external_write_roots', [])
if not isinstance(user, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]*[$]?', user) or user == 'CHANGE_ME':
    raise SystemExit('host worker config requires a real run_user')
if not isinstance(install, str) or not install.startswith('/') or '\n' in install or '\r' in install:
    raise SystemExit('host worker config requires an absolute install_dir')
if not isinstance(interval, (int, float)) or not 0.2 <= interval <= 60:
    raise SystemExit('worker_interval_seconds must be between 0.2 and 60')
if not isinstance(paths, list) or not paths or any(not isinstance(item, str) or not item or ':' in item or '\n' in item or '\r' in item for item in paths):
    raise SystemExit('pi_path_entries must be a non-empty list of PATH entries')
if not isinstance(start, bool):
    raise SystemExit('start_after_install must be true or false')
if not isinstance(roots, list):
    raise SystemExit('external_write_roots must be a list')
seen = set()
for root in roots:
    if not isinstance(root, dict): raise SystemExit('external write root must be an object')
    root_id, path, label = root.get('id'), root.get('path'), root.get('label')
    if (not isinstance(root_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', root_id)
            or root_id in seen or not isinstance(path, str) or not path.startswith('/')
            or '\\n' in path or '\\r' in path or not isinstance(label, str) or not 0 < len(label) <= 120):
        raise SystemExit('external write root is invalid')
    seen.add(root_id)
print(user)
print(install)
print(interval)
print(':'.join(paths))
print('true' if start else 'false')
PY
)
[[ ${#config_values[@]} -eq 5 ]] || { echo "could not read host worker config: $config_path" >&2; exit 2; }
run_user=${config_values[0]}
install_dir=${config_values[1]}
worker_interval=${config_values[2]}
pi_path=${config_values[3]}
start_service=${config_values[4]}
[[ -z "$start_service_override" ]] || start_service=$start_service_override
id "$run_user" >/dev/null 2>&1 || { echo 'run_user in host_worker.json must be an existing host user.' >&2; exit 2; }
run_home=$(getent passwd "$run_user" | cut -d: -f6)
[[ -n "$run_home" && -d "$run_home" ]] || { echo "Cannot determine home directory for $run_user." >&2; exit 2; }
pi_path=${pi_path//\~/$run_home}

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
Environment=PATH=$pi_path
ExecStart=/usr/bin/python3 $install_dir/journal-agent-worker.py --data-root $data_root --runner $install_dir/run-agent-session.sh --host-config $config_path --interval $worker_interval
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$data_root

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
echo "Config:       $config_path"
echo "Environment: $environment"
echo "Data root:    $data_root"
echo "Service:      $service_name.service"
echo "Logs:         journalctl -u $service_name -f"
echo "Status:       systemctl status $service_name"
