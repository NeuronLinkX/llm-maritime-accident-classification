#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_CONFIG="config/config.json"
LOCK_FILE="$ROOT_DIR/step_4_process/.step4_run.lock"
DEFAULT_VENV="$ROOT_DIR/.venv"
SLACK_ENV_FILE="$ROOT_DIR/config/slack.env"

CONFIG_PATH="$DEFAULT_CONFIG"
VENV_PATH="${VENV_PATH:-$DEFAULT_VENV}"
RUN_MODE="run"
LIMIT_ARG=""
SKIP_DETERMINISM=0
AUTO_CLEAN=0
FORCE_CLEAN=0
NO_TEE=0
BACKGROUND=0

usage() {
    cat <<'EOF'
Usage:
  ./run.sh [--config PATH] [--dry-run] [--limit N] [--skip-determinism]
           [--clean] [--force-clean] [--no-tee] [--background]

Options:
  --config PATH         step4 config JSON path. default: config/config.json
  --dry-run             run python -m step4 --dry-run
  --limit N             pass --limit N to step4
  --skip-determinism    reuse saved determinism result
  --clean               kill user-owned stale step4/vLLM processes before start
  --force-clean         same as --clean, plus remove stale lock file if no live pid
  --no-tee              print directly without tee log file
  --background          start in background and write stdout/stderr to the log file
  -h, --help            show this message

Examples:
  ./run.sh --config config/config_20260813.json
  ./run.sh --config config/config_20260813.json --limit 1
  ./run.sh --config config/config_20260813.json --clean
  ./run.sh --config config/config_20260813.json --background
EOF
}

log() {
    printf '[run.sh] %s\n' "$*"
}

die() {
    printf '[run.sh] ERROR: %s\n' "$*" >&2
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)
            [[ $# -ge 2 ]] || die "--config requires a path"
            CONFIG_PATH="$2"
            shift 2
            ;;
        --dry-run)
            RUN_MODE="dry-run"
            shift
            ;;
        --limit)
            [[ $# -ge 2 ]] || die "--limit requires a number"
            LIMIT_ARG="$2"
            shift 2
            ;;
        --skip-determinism)
            SKIP_DETERMINISM=1
            shift
            ;;
        --clean)
            AUTO_CLEAN=1
            shift
            ;;
        --force-clean)
            AUTO_CLEAN=1
            FORCE_CLEAN=1
            shift
            ;;
        --no-tee)
            NO_TEE=1
            shift
            ;;
        --background)
            BACKGROUND=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

require_cmd python3
require_cmd ps
require_cmd pgrep

cd "$ROOT_DIR"

[[ -f "$CONFIG_PATH" ]] || die "config file not found: $CONFIG_PATH"
[[ -f "$VENV_PATH/bin/activate" ]] || die "venv activate script not found: $VENV_PATH/bin/activate"

CONFIG_OUTPUT_ROOT="$(python3 - "$CONFIG_PATH" <<'PY'
import json, os, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = Path(cfg["paths"]["output_root"])
if not root.is_absolute():
    root = Path.cwd() / root
print(root)
PY
)"

CONFIG_GPU_UTIL="$(python3 - "$CONFIG_PATH" <<'PY'
import json, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(cfg["model"].get("gpu_memory_utilization", ""))
PY
)"

LOG_PATH="$CONFIG_OUTPUT_ROOT/logs/run_$(date +%Y%m%d_%H%M%S).log"
LATEST_LOG_PATH="$CONFIG_OUTPUT_ROOT/logs/latest.log"
PID_PATH="$CONFIG_OUTPUT_ROOT/logs/latest.pid"
mkdir -p "$(dirname "$LOG_PATH")"

collect_step4_pids() {
    local patt
    patt="python -m step4|VLLM::EngineCore|EngineCore|vllm"
    ps -u "$USER" -o pid=,args= | grep -E "$patt" | grep -v -E 'grep|run.sh' | awk '{print $1}' | sort -u
}

show_gpu_snapshot() {
    if command -v nvidia-smi >/dev/null 2>&1; then
        log "GPU snapshot"
        nvidia-smi || true
    else
        log "nvidia-smi not found in PATH; skipping GPU snapshot"
    fi
}

show_stale_lock_info() {
    [[ -f "$LOCK_FILE" ]] || return 0
    log "lock file exists: $LOCK_FILE"
    cat "$LOCK_FILE" || true
}

cleanup_user_processes() {
    mapfile -t pids < <(collect_step4_pids)
    if [[ ${#pids[@]} -eq 0 ]]; then
        log "no user-owned step4/vLLM processes found"
        return 0
    fi

    log "cleaning user-owned step4/vLLM processes: ${pids[*]}"
    kill "${pids[@]}" 2>/dev/null || true
    sleep 2

    mapfile -t pids < <(collect_step4_pids)
    if [[ ${#pids[@]} -gt 0 ]]; then
        log "forcing remaining processes: ${pids[*]}"
        kill -9 "${pids[@]}" 2>/dev/null || true
        sleep 1
    fi
}

remove_stale_lock_if_safe() {
    [[ -f "$LOCK_FILE" ]] || return 0
    local lock_pid=""
    lock_pid="$(sed -n 's/^pid=\([0-9]\+\).*/\1/p' "$LOCK_FILE" | head -n 1)"
    if [[ -n "$lock_pid" ]] && ps -p "$lock_pid" >/dev/null 2>&1; then
        log "lock file pid is still alive: $lock_pid; keeping lock file"
        return 0
    fi
    log "removing stale lock file: $LOCK_FILE"
    rm -f "$LOCK_FILE"
}

log "root dir: $ROOT_DIR"
log "config: $CONFIG_PATH"
log "output_root: $CONFIG_OUTPUT_ROOT"
log "gpu_memory_utilization: $CONFIG_GPU_UTIL"
log "venv: $VENV_PATH"
show_stale_lock_info
show_gpu_snapshot

if [[ "$AUTO_CLEAN" -eq 1 ]]; then
    cleanup_user_processes
fi

if [[ "$FORCE_CLEAN" -eq 1 ]]; then
    remove_stale_lock_if_safe
fi

if [[ "$RUN_MODE" == "run" ]]; then
    if [[ "$CONFIG_GPU_UTIL" == "0.85" || "$CONFIG_GPU_UTIL" == "0.9" ]]; then
        log "warning: current config requests high gpu_memory_utilization=$CONFIG_GPU_UTIL"
        log "if shared GPU memory is tight, engine startup may fail before generation begins"
    fi
fi

STEP4_ARGS=( -m step4 --config "$CONFIG_PATH" )
if [[ "$RUN_MODE" == "dry-run" ]]; then
    STEP4_ARGS+=( --dry-run )
fi
if [[ -n "$LIMIT_ARG" ]]; then
    STEP4_ARGS+=( --limit "$LIMIT_ARG" )
fi
if [[ "$SKIP_DETERMINISM" -eq 1 ]]; then
    STEP4_ARGS+=( --skip-determinism )
fi

log "activating venv"
# shellcheck disable=SC1090
source "$VENV_PATH/bin/activate"

if [[ -f "$SLACK_ENV_FILE" ]]; then
    log "loading slack env: $SLACK_ENV_FILE"
    # shellcheck disable=SC1090
    source "$SLACK_ENV_FILE"
fi

log "python: $(command -v python)"
log "command: python ${STEP4_ARGS[*]}"
log "log file: $LOG_PATH"
ln -sfn "$(basename "$LOG_PATH")" "$LATEST_LOG_PATH"

if [[ "$BACKGROUND" -eq 1 ]]; then
    if [[ "$RUN_MODE" == "dry-run" ]]; then
        die "--background cannot be used with --dry-run"
    fi
    if [[ "$NO_TEE" -eq 0 ]]; then
        log "--background implies file logging only (no tee)"
    fi
    nohup python "${STEP4_ARGS[@]}" >"$LOG_PATH" 2>&1 &
    bg_pid=$!
    printf '%s\n' "$bg_pid" > "$PID_PATH"
    log "started in background: pid=$bg_pid"
    log "latest pid file: $PID_PATH"
    log "latest log symlink: $LATEST_LOG_PATH"
    log "watch with: tail -f $LATEST_LOG_PATH"
    exit 0
elif [[ "$NO_TEE" -eq 1 ]]; then
    exec python "${STEP4_ARGS[@]}"
else
    exec python "${STEP4_ARGS[@]}" 2>&1 | tee "$LOG_PATH"
fi
