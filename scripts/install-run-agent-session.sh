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
  "install_dir": "/opt/journl-agent-worker-$environment",
  "worker_interval_seconds": 1.0,
  "job_timeout_seconds": 1800,
  "pi_path_entries": ["~/.npm-global/bin", "~/.local/bin", "/usr/local/bin", "/usr/bin", "/bin"],
  "external_read_roots": [],
  "external_write_roots": [],
  "worker_api": {"enabled": false, "base_url": "http://127.0.0.1:4100", "token": "CHANGE_ME"},
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
timeout = value.get('job_timeout_seconds', 1800)
paths = value.get('pi_path_entries')
start = value.get('start_after_install')
write_roots = value.get('external_write_roots', [])
read_roots = value.get('external_read_roots', [])
if not isinstance(user, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]*[$]?', user) or user == 'CHANGE_ME':
    raise SystemExit('host worker config requires a real run_user')
if not isinstance(install, str) or not install.startswith('/') or '\n' in install or '\r' in install:
    raise SystemExit('host worker config requires an absolute install_dir')
if not isinstance(interval, (int, float)) or not 0.2 <= interval <= 60:
    raise SystemExit('worker_interval_seconds must be between 0.2 and 60')
if not isinstance(timeout, (int, float)) or not 60 <= timeout <= 3600:
    raise SystemExit('job_timeout_seconds must be between 60 and 3600')
if not isinstance(paths, list) or not paths or any(not isinstance(item, str) or not item or ':' in item or '\n' in item or '\r' in item for item in paths):
    raise SystemExit('pi_path_entries must be a non-empty list of PATH entries')
if not isinstance(start, bool):
    raise SystemExit('start_after_install must be true or false')
worker_api = value.get('worker_api')
if worker_api is not None:
    from urllib.parse import urlparse
    if not isinstance(worker_api, dict) or set(worker_api) != {'enabled', 'base_url', 'token'}:
        raise SystemExit('worker_api is invalid')
    enabled, base_url, token = worker_api.get('enabled'), worker_api.get('base_url'), worker_api.get('token')
    parsed = urlparse(base_url) if isinstance(base_url, str) else None
    if (not isinstance(enabled, bool) or parsed is None or parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or not parsed.port or parsed.path not in ('', '/') or parsed.params or parsed.query or parsed.fragment
            or parsed.username or parsed.password or not isinstance(token, str) or '\n' in token or '\r' in token
            or (enabled and not 32 <= len(token) <= 512)):
        raise SystemExit('worker_api is invalid')
if not isinstance(write_roots, list) or not isinstance(read_roots, list):
    raise SystemExit('external read/write roots must be lists')
for roots, label_name in ((write_roots, 'write'), (read_roots, 'read')):
  seen = set()
  for root in roots:
    if not isinstance(root, dict): raise SystemExit(f'external {label_name} root must be an object')
    root_id, path, label = root.get('id'), root.get('path'), root.get('label')
    if (not isinstance(root_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', root_id)
            or root_id in seen or not isinstance(path, str) or not path.startswith('/')
            or '\\n' in path or '\\r' in path or not isinstance(label, str) or not 0 < len(label) <= 120):
        raise SystemExit(f'external {label_name} root is invalid')
    seen.add(root_id)
print(user)
print(install)
print(interval)
print(':'.join(paths))
print('true' if start else 'false')
# Agent-Projekte in write/execute mode need real write access to their folders.
# ProtectSystem=strict otherwise keeps the whole host read-only for the worker.
# The ticket worktree directory lives outside every repository and would
# otherwise be read-only, which fails each run with EROFS.
tickets = value.get('tickets') or {}
extra = set()
if isinstance(tickets, dict) and tickets.get('worktrees'):
    extra.add(str(tickets['worktrees']))
print('\x1f'.join(sorted({root['path'] for root in write_roots} | extra)))
PY
)
[[ ${#config_values[@]} -eq 6 ]] || { echo "could not read host worker config: $config_path" >&2; exit 2; }
run_user=${config_values[0]}
install_dir=${config_values[1]}
worker_interval=${config_values[2]}
pi_path=${config_values[3]}
start_service=${config_values[4]}
IFS=$'\x1f' read -r -a project_write_roots <<< "${config_values[5]}"
[[ -z "$start_service_override" ]] || start_service=$start_service_override
id "$run_user" >/dev/null 2>&1 || { echo 'run_user in host_worker.json must be an existing host user.' >&2; exit 2; }
run_home=$(getent passwd "$run_user" | cut -d: -f6)
[[ -n "$run_home" && -d "$run_home" ]] || { echo "Cannot determine home directory for $run_user." >&2; exit 2; }
# The worker itself must read this file after dropping root privileges.  It
# contains the internal worker API token, so grant it only to that service
# account instead of making it broadly readable through the data directory.
chown "$run_user":"$run_user" "$config_path"
chmod 0600 "$config_path"
pi_path=${pi_path//\~/$run_home}

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# Ein in ReadWritePaths genannter Pfad muss existieren, sonst startet die Unit
# nicht. Der Worktree-Ordner gehoert dem Dienstbenutzer.
for root in "${project_write_roots[@]}"; do
  [[ -n "$root" && ! -e "$root" ]] || continue
  install -d -m 0755 -o "$run_user" "$root" 2>/dev/null || mkdir -p "$root"
done

service_name="journl-agent-worker-$environment"

# Zwei Umgebungen duerfen sich das Installationsverzeichnis nicht teilen. Sonst
# tauscht ein Install der einen Umgebung unbemerkt den Code der anderen aus, und
# die uebernimmt ihn irgendwann beim naechsten Neustart — ohne dass jemand das
# ausgeloest oder gesehen haette.
for unit in /etc/systemd/system/journl-agent-worker-*.service; do
  [[ -e "$unit" ]] || continue
  [[ "$unit" == "/etc/systemd/system/$service_name.service" ]] && continue
  grep -qxF "WorkingDirectory=$install_dir" "$unit" || continue
  echo "install_dir $install_dir wird bereits von $(basename "$unit") benutzt." >&2
  echo "Jede Umgebung braucht ihr eigenes install_dir. Anpassen in: $config_path" >&2
  echo 'Beim Trennen zuerst die Umgebung installieren, deren Pfad sich aendert.' >&2
  exit 2
done

install -d -m 0755 "$install_dir"
install -m 0755 "$source_dir/run-agent-session.sh" "$install_dir/run-agent-session.sh"
install -m 0755 "$source_dir/journal-agent-worker.py" "$install_dir/journal-agent-worker.py"
# Der Ticket-Abholer wird vom Worker importiert und muss daneben liegen.
install -m 0644 "$source_dir/ticket_runner.py" "$install_dir/ticket_runner.py"
install -m 0644 "$source_dir/host_targets.py" "$install_dir/host_targets.py"
install -m 0644 "$source_dir/journl-reference-tools.ts" "$install_dir/journl-reference-tools.ts"
chown -R "$run_user":"$run_user" "$install_dir"

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
$(for root in "${project_write_roots[@]}"; do [[ -n "$root" ]] && printf 'ReadWritePaths=%s\n' "$root"; done)

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
