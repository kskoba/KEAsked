#!/usr/bin/env bash
# kea -- manage the KEA Physician Scheduler dev stack from the terminal.
#
#   kea up        (also: kea -up, kea restart)  stop whatever is running, start backend + frontend, verify
#   kea down                                     stop backend + frontend
#   kea status                                   what's running, backend health, in-flight solve?
#   kea logs [backend|frontend]                  tail the latest log(s)
#   kea backend                                  restart only the Python backend (after editing scheduler/*.py)
#   kea test                                     run the backend pytest suite
#
# Suggested alias (in ~/.bashrc or ~/.zshrc):
#   alias kea='/home/cid/Dropbox/KEAclaude/KEAsked/scripts/kea.sh'
#
# Mirrors .claude/skills/restart-kea-stack/SKILL.md: the backend has no
# hot reload, so any scheduler/*.py edit needs a restart; the Vite dev
# server hot-reloads .jsx on its own. A restart clears the backend's
# in-memory state (imported submissions, loaded schedule) -- re-import
# before generating again. Refuses to stop a backend with a solve in
# flight unless --force is given.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_BIN="$REPO/.venv/bin"
BACKEND_URL="http://127.0.0.1:5000"
LOG_DIR="${KEA_LOG_DIR:-$HOME/.cache/kea-stack/logs}"
mkdir -p "$LOG_DIR"

# Process detection is by executable NAME first (python3 / node / electron)
# and only then by command line, so a shell whose own command line merely
# mentions these strings (a grep, an editor, this script's caller) is never
# matched -- `pgrep -f` alone killed the calling shell once (2026-10-07).

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[[ -x "$VENV_BIN/python3" ]] || die "venv not found at $VENV_BIN -- see .claude/skills/run-kea-scheduler/SKILL.md"

_pids_by_name_and_cmd() {  # _pids_by_name_and_cmd <comm regex> <cmdline substring>
  local pid
  for pid in $(pgrep -x "$1" 2>/dev/null || true); do
    [[ "$pid" == "$$" || "$pid" == "$PPID" ]] && continue
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q -- "$2" && echo "$pid"
  done
  return 0
}
backend_pids()  { _pids_by_name_and_cmd 'python3?' 'scheduler.api.server'; }
frontend_pids() {
  _pids_by_name_and_cmd 'node' 'electron-vite'
  _pids_by_name_and_cmd 'npm' 'run dev'
  _pids_by_name_and_cmd 'electron' "$REPO/frontend"
}

solve_running() {
  curl -s --max-time 2 "$BACKEND_URL/api/generate-progress" 2>/dev/null | grep -q '"running": *true'
}

health() { curl -s --max-time 3 "$BACKEND_URL/api/health" 2>/dev/null || true; }

latest_log() {  # latest_log backend|frontend -> path or empty (never fails, even with no logs yet)
  find "$LOG_DIR" -maxdepth 1 -name "$1-*.log" -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -n1 | cut -d' ' -f2- || true
}

wait_for() {  # wait_for <seconds> <command...> ; returns 0 when the command succeeds
  local tries=$1; shift
  for ((i = 0; i < tries * 2; i++)); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 0.5
  done
  return 1
}

cmd_status() {
  bold "KEA stack status"
  local b f
  b=$(backend_pids); f=$(frontend_pids)
  if [[ -n "$b" ]]; then ok "backend running (pid ${b//$'\n'/, })"; else warn "backend not running"; fi
  if [[ -n "$f" ]]; then ok "frontend running (pids ${f//$'\n'/, })"; else warn "frontend not running"; fi
  local h; h=$(health)
  if [[ "$h" == *'"ok"'* ]]; then ok "backend health: $h"; else warn "backend not answering at $BACKEND_URL"; fi
  if solve_running; then warn "A SOLVE IS IN FLIGHT -- don't restart without --force"; fi
  local settings="$HOME/.config/kea-scheduler-frontend/kea-settings.json"
  if [[ -f "$settings" ]]; then
    local cfg; cfg=$(tr -d '\n ' < "$settings")
    if [[ "$cfg" == *'"remote"'* ]]; then
      warn "app is pointed at a REMOTE backend ($cfg) -- the local backend above is not the one it talks to"
    else
      ok "app backend setting: $cfg"
    fi
  fi
  local lb lf
  lb=$(latest_log backend); lf=$(latest_log frontend)
  [[ -n "$lb" ]] && echo "  latest backend log:  $lb"
  [[ -n "$lf" ]] && echo "  latest frontend log: $lf"
  return 0
}

cmd_down() {
  local force=${1:-}
  bold "Stopping KEA stack"
  if [[ "$force" != "--force" ]] && solve_running; then
    die "a solve is running on the backend; re-run with --force to stop it anyway"
  fi
  local pids
  pids=$( { frontend_pids; backend_pids; } | sort -u | tr '\n' ' ')
  if [[ -z "${pids// /}" ]]; then ok "nothing was running"; return 0; fi
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 2
  pids=$( { frontend_pids; backend_pids; } | sort -u | tr '\n' ' ')
  if [[ -n "${pids// /}" ]]; then
    warn "still alive after SIGTERM, sending SIGKILL: $pids"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi
  ok "stopped"
}

start_backend() {
  local log="$LOG_DIR/backend-$(date +%Y%m%d-%H%M%S).log"
  # The background job must be a single, fully-redirected simple command
  # (not `cd && cmd &`, which backgrounds a wrapper shell that keeps the
  # caller's stdout open and hangs anything piping this script).
  (
    cd "$REPO" || exit 1
    PATH="$VENV_BIN:$PATH" nohup setsid python3 -m scheduler.api.server > "$log" 2>&1 < /dev/null &
  )
  if wait_for 20 bash -c "curl -s --max-time 2 $BACKEND_URL/api/health | grep -q ok"; then
    ok "backend up: $(health)   (log: $log)"
  else
    warn "backend did not answer within 20s -- last log lines:"
    tail -n 15 "$log" | sed 's/^/      /'
    return 1
  fi
}

start_frontend() {
  local log="$LOG_DIR/frontend-$(date +%Y%m%d-%H%M%S).log"
  (
    cd "$REPO/frontend" || exit 1
    PATH="$VENV_BIN:$PATH" nohup setsid npm run dev > "$log" 2>&1 < /dev/null &
  )
  if wait_for 40 grep -q "Python API server is ready" "$log"; then
    ok "frontend up (log: $log)"
    # Electron's own dev-mode backend spawn collides with the one we started -- expected, harmless.
    if grep -q "address already in use" "$log" 2>/dev/null; then
      ok "electron reused the running backend (expected 'address already in use' in log)"
    fi
    return 0
  else
    warn "frontend did not report ready within 40s -- last log lines:"
    tail -n 15 "$log" | sed 's/^/      /'
    return 1
  fi
}

cmd_up() {
  cmd_down "${1:-}"
  bold "Starting KEA stack"
  start_backend
  start_frontend
  echo
  ok "clean state: re-import submissions before generating"
}

cmd_backend() {
  bold "Restarting backend only"
  if [[ "${1:-}" != "--force" ]] && solve_running; then
    die "a solve is running; re-run with --force"
  fi
  local b; b=$(backend_pids)
  # shellcheck disable=SC2086
  [[ -n "$b" ]] && kill $b 2>/dev/null || true
  sleep 2
  start_backend
  ok "frontend left running; it will reconnect to the new backend"
}

cmd_logs() {
  local which=${1:-both} f
  local files=()
  if [[ "$which" == "backend"  || "$which" == "both" ]]; then f=$(latest_log backend);  [[ -n "$f" ]] && files+=("$f"); fi
  if [[ "$which" == "frontend" || "$which" == "both" ]]; then f=$(latest_log frontend); [[ -n "$f" ]] && files+=("$f"); fi
  [[ ${#files[@]} -gt 0 ]] || die "no logs yet in $LOG_DIR"
  tail -n 40 -F "${files[@]}"
}

cmd_test() {
  bold "Backend tests"
  ( cd "$REPO/scheduler" && "$VENV_BIN/python3" -m pytest -q "$@" 2>&1 | grep -v "RuntimeWarning: Unexpected value in sys" )
}

case "${1:-}" in
  up|-up|--up|restart)  shift; cmd_up "$@" ;;
  down|-down|stop)      shift; cmd_down "$@" ;;
  backend|-b)           shift; cmd_backend "$@" ;;
  status|-s|"")         cmd_status ;;
  logs|-l)              shift; cmd_logs "$@" ;;
  test|-t)              shift; cmd_test "$@" ;;
  -h|--help|help)       sed -n '2,20p' "$0" ;;
  *) die "unknown command '$1' -- try: kea up | down | backend | status | logs | test" ;;
esac
