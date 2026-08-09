#!/usr/bin/env bash
# Executes one deterministic AI turn and appends the result to the source Markdown file.
# The web application must authenticate/authorise the request before calling this script.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  run-agent-session.sh --agent pi --model PROVIDER/MODEL --context none|section|journal \
    --source FILE --prompt-file FILE [options]

Required:
  --agent NAME             codex, pi, opencode, hermes, or custom
  --model MODEL            Agent/model identifier (Pi accepts provider/model)
  --context MODE           none, section, or journal
  --source FILE            Markdown file receiving the reply
  --prompt-file FILE       UTF-8 file containing the configured workflow prompt

Options:
  --section-file FILE      Current saved block; required for context=section
  --journal-file FILE      Complete journal; required for context=journal
  --context-file FILE      Additional Markdown context (repeatable)
  --data-root DIR          Allowed root for every Markdown input/output (required in production)
  --session-id ID          Stable session ID (default: generated UUID)
  --actor ID               Authenticated user ID, written as metadata only
  --expected-revision SHA  SHA-256 revision supplied by the authorised monitor
  --max-answer-bytes N     Maximum UTF-8 answer size (default: 262144)
  --dry-run                Build and validate the request, but do not call an agent or write

Agent adapters:
  codex    codex exec --ephemeral --sandbox read-only --model MODEL (subscription login)
  pi       pi --no-tools --no-session --model MODEL -p, prompt via stdin
  opencode command from OPENCODE_AGENT_CMD (must read stdin and write only the answer to stdout)
  hermes   command from HERMES_AGENT_CMD   (same contract)
  custom   command from CUSTOM_AGENT_CMD   (same contract)

The command variables are intentionally command *paths*, not shell snippets. Configure
model selection inside an adapter executable or use MODEL as its first argument there.
EOF
}

die() { printf 'run-agent-session: %s\n' "$*" >&2; exit 2; }

agent= model= context= source= prompt_file= section_file= journal_file= data_root=
session_id= actor= expected_revision= max_answer_bytes=262144 dry_run=false
context_files=()
while (($#)); do
  case "$1" in
    --agent) (($# >= 2)) || die "missing value for $1"; agent=$2; shift 2 ;;
    --model) (($# >= 2)) || die "missing value for $1"; model=$2; shift 2 ;;
    --context) (($# >= 2)) || die "missing value for $1"; context=$2; shift 2 ;;
    --source) (($# >= 2)) || die "missing value for $1"; source=$2; shift 2 ;;
    --prompt-file) (($# >= 2)) || die "missing value for $1"; prompt_file=$2; shift 2 ;;
    --section-file) (($# >= 2)) || die "missing value for $1"; section_file=$2; shift 2 ;;
    --journal-file) (($# >= 2)) || die "missing value for $1"; journal_file=$2; shift 2 ;;
    --data-root) (($# >= 2)) || die "missing value for $1"; data_root=$2; shift 2 ;;
    --session-id) (($# >= 2)) || die "missing value for $1"; session_id=$2; shift 2 ;;
    --actor) (($# >= 2)) || die "missing value for $1"; actor=$2; shift 2 ;;
    --expected-revision) (($# >= 2)) || die "missing value for $1"; expected_revision=$2; shift 2 ;;
    --max-answer-bytes) (($# >= 2)) || die "missing value for $1"; max_answer_bytes=$2; shift 2 ;;
    --context-file) (($# >= 2)) || die "missing value for --context-file"; context_files+=("$2"); shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ "$agent" =~ ^(codex|pi|opencode|hermes|custom)$ ]] || die "unsupported agent: $agent"
[[ -n "$model" && "$model" != *$'\n'* && "$model" != *$'\r'* ]] || die "invalid model"
[[ "$max_answer_bytes" =~ ^[1-9][0-9]{0,8}$ ]] || die "invalid --max-answer-bytes"
[[ "$context" =~ ^(none|section|journal)$ ]] || die "context must be none, section, or journal"
[[ -f "$source" && "${source##*.}" == md ]] || die "--source must be an existing Markdown file"
[[ -f "$prompt_file" ]] || die "--prompt-file must exist"
[[ "$context" != section || -f "$section_file" ]] || die "--section-file is required for context=section"
[[ "$context" != journal || -f "$journal_file" ]] || die "--journal-file is required for context=journal"

# Resolve paths once and reject traversal/symlink escapes. The application should always
# pass DATA_DIR here; without it this script intentionally has no production-safe boundary.
realpath_file() { realpath -e -- "$1"; }
source=$(realpath_file "$source")
prompt_file=$(realpath_file "$prompt_file")
if [[ -n "$data_root" ]]; then
  data_root=$(realpath -e -- "$data_root")
  [[ "$source" == "$data_root/"* ]] || die "source lies outside --data-root"
  for file in "$prompt_file" "${section_file:-}" "${journal_file:-}" "${context_files[@]}"; do
    [[ -z "$file" ]] && continue
    resolved=$(realpath_file "$file")
    [[ "$resolved" == "$data_root/"* ]] || die "context file lies outside --data-root: $file"
  done
else
  die "--data-root is required"
fi

if [[ -z "$session_id" ]]; then
  session_id=$(python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
)
fi
[[ "$session_id" =~ ^[0-9a-fA-F-]{36}$ ]] || die "session-id must be a UUID"
[[ -z "$expected_revision" || "$expected_revision" =~ ^[0-9a-f]{64}$ ]] || die "expected-revision must be a SHA-256 hash"

# Keep the stable per-source lock for the entire turn, not merely the final
# rename. Other application writers use this exact <source>.lock convention.
exec 9>"$source.lock"
flock -n 9 || die "another agent session is already running for this source"

work_dir=$(mktemp -d)
request="$work_dir/request.md"
answer="$work_dir/answer.md"
cleanup() { rm -rf "$work_dir"; }
trap cleanup EXIT

# The config marker is a host-side concurrency guard, not document content.
# Never place it in the model prompt: otherwise a document-oriented agent can
# faithfully reproduce it in its answer and expose it in the writing tab.
without_session_config() {
  awk '
    /^<!--[[:space:]]*jt:agent-session-config[[:space:]]*$/ { hidden = 1; next }
    hidden && /^-->[[:space:]]*$/ { hidden = 0; next }
    !hidden { print }
  ' "$1"
}

{
  printf '%s\n\n' 'Du bearbeitest eine Markdown-Agent-Session.'
  printf '%s\n' 'Antworte ausschließlich mit dem Markdown-Inhalt deiner Antwort. Keine Dateizugriffe, keine Tool-Aufrufe, keine Präambel.'
  printf '%s\n\n' 'Die Anwendung schreibt deine Antwort selbst revisionssicher in die Quelldatei.'
  printf '%s\n\n' '## Auftrag'
  cat "$prompt_file"
  case "$context" in
    none) printf '\n\n## Dokument-Kontext\nKein Dokument-Kontext wurde freigegeben.\n' ;;
    section) printf '\n\n## Gespeicherter Abschnitt\n'; without_session_config "$section_file" ;;
    journal) printf '\n\n## Vollständiges Journal\n'; cat "$journal_file" ;;
  esac
  for file in "${context_files[@]}"; do
    printf '\n\n## Zusätzliche Datei: %s\n' "$(basename -- "$file")"
    cat "$file"
  done
} > "$request"

if "$dry_run"; then
  printf 'validated session=%s source=%s agent=%s model=%s\n' "$session_id" "$source" "$agent" "$model"
  exit 0
fi

case "$agent" in
  codex)
    # Isolated temporary workspace plus read-only sandbox: only this wrapper writes Markdown.
    codex exec --ephemeral --skip-git-repo-check --sandbox read-only --model "$model" \
      --output-last-message "$answer" -C "$work_dir" - < "$request" > /dev/null
    ;;
  pi)
    # --no-tools prevents the model from editing arbitrary files; only this wrapper writes.
    pi --no-tools --no-session --model "$model" -p < "$request" > "$answer"
    ;;
  opencode|hermes|custom)
    case "$agent" in
      opencode) adapter=${OPENCODE_AGENT_CMD:-} ;;
      hermes) adapter=${HERMES_AGENT_CMD:-} ;;
      custom) adapter=${CUSTOM_AGENT_CMD:-} ;;
    esac
    [[ -n "$adapter" ]] || die "set ${agent^^}_AGENT_CMD to a trusted adapter executable"
    command -v "$adapter" >/dev/null || die "adapter not found: $adapter"
    "$adapter" "$model" < "$request" > "$answer"
    ;;
esac

[[ -s "$answer" ]] || die "agent returned no answer"
[[ $(wc -c < "$answer") -le "$max_answer_bytes" ]] || die "agent answer exceeds --max-answer-bytes"
# Atomic update; the generated block is inserted before this app's hashtag footer.
# The hidden metadata lets the web app identify agent writes and avoids a feedback loop.
ANSWER_FILE="$answer" SOURCE_FILE="$source" SESSION_ID="$session_id" ACTOR_ID="$actor" EXPECTED_REVISION="$expected_revision" python3 - <<'PY'
import hashlib, json, os, re, tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ['SOURCE_FILE'])
reply = Path(os.environ['ANSWER_FILE']).read_text(encoding='utf-8').strip()
if not reply:
    raise SystemExit('empty answer after trimming')
meta = json.dumps({
    'schema': 1, 'session_id': os.environ['SESSION_ID'],
    'actor_id': os.environ.get('ACTOR_ID', ''), 'origin': 'agent'
}, ensure_ascii=False, sort_keys=True)
now = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
block = f'\n\n___\n\n## Agentenantwort | Datum & Uhrzeit: {now}\n<!-- jt:agent-session {meta} -->\n\n{reply}\n\n___\n'
content = path.read_text(encoding='utf-8')
expected = os.environ.get('EXPECTED_REVISION', '')
revision_source = re.sub(r'"source_revision"\s*:\s*"[^"]*"', '"source_revision":""', content)
if expected and hashlib.sha256(revision_source.encode('utf-8')).hexdigest() != expected:
    raise SystemExit('conflict: source revision changed while agent was running')
# A host monitor must only finish a live session matching this request.
if f'"session_id": "{os.environ["SESSION_ID"]}"' not in content:
    raise SystemExit('conflict: session configuration no longer matches source')
footer = '<!-- jt:hashtag-index:start schema="1" -->'
pos = content.find(footer)
updated = (content[:pos].rstrip() + block + '\n' + content[pos:]) if pos >= 0 else content.rstrip() + block
# Keep a recoverable pre-write copy beside the source; monitor retention is
# intentionally left to the operator's existing backup strategy.
backup = path.with_name(path.name + '.agent-backup')
fd, backup_tmp = tempfile.mkstemp(prefix='.agent-backup-', dir=path.parent, text=True)
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(content); f.flush(); os.fsync(f.fileno())
    os.replace(backup_tmp, backup)
finally:
    if os.path.exists(backup_tmp): os.unlink(backup_tmp)
fd, tmp = tempfile.mkstemp(prefix='.agent-write-', dir=path.parent, text=True)
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(updated); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
PY

printf 'written session=%s source=%s\n' "$session_id" "$source"
