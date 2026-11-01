#!/usr/bin/env bash
# Kompakter Testlauf für KI-Agenten und Menschen.
#
# Warum dieses Skript existiert: auf dem Host fehlen Flask/Pillow/psycopg,
# dort werden ~232 der 389 Tests still übersprungen. Aussagekräftig ist nur der
# Lauf im DEV-Image. Dessen Rohausgabe ist aber voll mit INFO-Logs und
# ResourceWarnings — dieses Skript filtert sie und gibt nur das Ergebnis aus.
#
# Ausnahme: tests.test_code_structure braucht `git`, das im DEV-Image fehlt.
# Dieser eine Test läuft deshalb zusätzlich auf dem Host (er braucht kein Flask).
#
# Nutzung:
#   scripts/run-tests.sh                 # alles
#   scripts/run-tests.sh tests.test_brain_api   # gezielt
#   scripts/run-tests.sh --db            # zusätzlich die PostgreSQL-Integrationstests
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${JOURNL_TEST_IMAGE:-journaling-tracker-dev:latest}"
DOCKER="sudo docker"
RUN_DB=0
TARGETS=()

for arg in "$@"; do
  case "$arg" in
    --db) RUN_DB=1 ;;
    *) TARGETS+=("$arg") ;;
  esac
done

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

if [ ${#TARGETS[@]} -gt 0 ]; then
  cmd=(python3 -m unittest "${TARGETS[@]}")
else
  cmd=(python3 -m unittest discover -s tests)
fi

$DOCKER run --rm -v "$REPO:/repo" -w /repo -e PYTHONPATH=/repo/app "$IMAGE" \
  "${cmd[@]}" > "$log" 2>&1
status=$?

# Der git-Fehler ist reiner Image-Mangel: denselben Test auf dem Host prüfen.
git_only_failure=0
if grep -q "No such file or directory: 'git'" "$log"; then
  if (cd "$REPO" && PYTHONPATH="$REPO/app" python3 -m unittest \
        tests.test_code_structure >/dev/null 2>&1); then
    git_only_failure=1
  fi
fi

summary="$(grep -E '^(Ran [0-9]+ test|OK|FAILED)' "$log" | tr '\n' ' ')"
echo "== App-Tests (DEV-Image) =="
echo "${summary:-kein Ergebnis - Image vorhanden? ($IMAGE)}"

failure_count="$(grep -cE '^(ERROR|FAIL): ' "$log")"
known_only=0
if [ $git_only_failure -eq 1 ] && [ "$failure_count" = "1" ]; then
  known_only=1
  echo "Einziger Fehler ist test_no_bytecode_or_cache_is_tracked: im DEV-Image"
  echo "fehlt git. Auf dem Host läuft derselbe Test grün -> kein echter Fehler."
fi

# Echte Fehler zeigen: nur Kopfzeile + letzte Assertion je Fall, nicht der ganze Traceback.
if [ $status -ne 0 ] && [ $known_only -eq 0 ]; then
  echo
  # Bekannten git-Fehler samt seinem Block ausblenden, wenn er nur am Image liegt.
  filtered="$log"
  if [ $git_only_failure -eq 1 ]; then
    filtered="$(mktemp)"
    awk '/^(ERROR|FAIL): test_no_bytecode_or_cache_is_tracked/{skip=1}
         /^={70}$/{skip=0}
         !skip' "$log" > "$filtered"
  fi
  grep -E '^(ERROR|FAIL): ' "$filtered"
  echo "--- Details ---"
  awk '/^(=|-){70}$/{p=1} p' "$filtered" \
    | grep -vE '^(INFO|WARNING|DEBUG):|ResourceWarning' | head -120
  [ "$filtered" != "$log" ] && rm -f "$filtered"
fi

# JS-Syntax auf dem Host pruefen (im DEV-Image fehlt node). Die Python-Tests
# lesen die Skripte nur als Text: eine doppelt deklarierte Variable legte
# 5.3.23 die ganze write-voice.js lahm, waehrend alle Tests gruen waren.
js_status=0
if [ ${#TARGETS[@]} -eq 0 ] && command -v node >/dev/null; then
  js_failed=()
  for js in "$REPO"/app/static/*.js; do
    case "$js" in *.min.js) continue ;; esac
    node --check "$js" >/dev/null 2>&1 || js_failed+=("$js")
  done
  echo
  echo "== JS-Syntax (Host, node --check) =="
  if [ ${#js_failed[@]} -eq 0 ]; then
    echo "OK"
  else
    js_status=1
    for js in "${js_failed[@]}"; do
      echo "FEHLER: ${js#"$REPO"/}"
      node --check "$js" 2>&1 | head -6
    done
  fi
fi

if [ $RUN_DB -eq 1 ]; then
  echo
  echo "== DB-Integrationstests (Wegwerf-Stack) =="
  dblog="$(mktemp)"
  $DOCKER compose -f "$REPO/docker-compose.db-tests.yml" run --rm \
    db-tests-runner > "$dblog" 2>&1
  grep -E '^(Ran [0-9]+ test|OK|FAILED)' "$dblog" | tr '\n' ' '; echo
  grep -qE '^(OK)' "$dblog" || awk '/^(=|-){70}$/{p=1} p' "$dblog" | head -80
  $DOCKER compose -f "$REPO/docker-compose.db-tests.yml" down -v >/dev/null 2>&1
  rm -f "$dblog"
fi

[ $js_status -ne 0 ] && exit 1
# Der bekannte git-Fehler soll den Exit-Code nicht rot färben.
[ $known_only -eq 1 ] && exit 0
exit $status
