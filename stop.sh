#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="$ROOT_DIR/step_4_process/.step4_run.lock"
REMOVE_PYCACHE=0
REMOVE_STEP4_LOGS=0

usage() {
    cat <<'EOF'
Usage:
  ./stop.sh [--clean-cache] [--clean-logs]

Options:
  --clean-cache   remove local __pycache__ and *.pyc files under step4/
  --clean-logs    remove run.sh tee logs in output_root/logs/run_*.log
  -h, --help      show this message

Notes:
  - Only user-owned step4/vLLM processes are targeted.
  - Root-owned or other-user GPU processes are not touched.
  - Stale lock file is removed if its recorded pid is not alive.
EOF
}

log() {
    printf '[stop.sh] %s\n' "$*"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean-cache)
            REMOVE_PYCACHE=1
            shift
            ;;
        --clean-logs)
            REMOVE_STEP4_LOGS=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf '[stop.sh] ERROR: unknown argument: %s\n' "$1" >&2
            exit 1
            ;;
    esac
done

cd "$ROOT_DIR"

collect_step4_pids() {
    local patt
    patt="python -m step4|VLLM::EngineCore|EngineCore|vllm"
    ps -u "$USER" -o pid=,args= | grep -E "$patt" | grep -v -E 'grep|run.sh|stop.sh' | awk '{print $1}' | sort -u
}

show_gpu_snapshot() {
    if command -v nvidia-smi >/dev/null 2>&1; then
        log "GPU snapshot"
        nvidia-smi || true
    else
        log "nvidia-smi not found in PATH; skipping GPU snapshot"
    fi
}

terminate_user_processes() {
    mapfile -t pids < <(collect_step4_pids)
    if [[ ${#pids[@]} -eq 0 ]]; then
        log "no user-owned step4/vLLM processes found"
        return 0
    fi

    log "terminating user-owned step4/vLLM processes: ${pids[*]}"
    kill "${pids[@]}" 2>/dev/null || true
    sleep 2

    mapfile -t pids < <(collect_step4_pids)
    if [[ ${#pids[@]} -eq 0 ]]; then
        log "all user-owned step4/vLLM processes exited cleanly"
        return 0
    fi

    log "forcing remaining processes: ${pids[*]}"
    kill -9 "${pids[@]}" 2>/dev/null || true
    sleep 1

    mapfile -t pids < <(collect_step4_pids)
    if [[ ${#pids[@]} -gt 0 ]]; then
        log "some processes still remain and likely require higher privileges: ${pids[*]}"
    else
        log "all user-owned step4/vLLM processes were force-killed"
    fi
}

remove_stale_lock() {
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

remove_python_cache() {
    log "removing step4 python cache files"
    find "$ROOT_DIR/step4" -type d -name '__pycache__' -prune -exec rm -rf {} +
    find "$ROOT_DIR/step4" -type f -name '*.pyc' -delete
}

remove_run_logs() {
    log "removing run.sh tee logs under outputs/*/logs/run_*.log"
    find "$ROOT_DIR/outputs" -type f -path '*/logs/run_*.log' -delete 2>/dev/null || true
}

log "root dir: $ROOT_DIR"
show_gpu_snapshot
terminate_user_processes
remove_stale_lock

if [[ "$REMOVE_PYCACHE" -eq 1 ]]; then
    remove_python_cache
fi

if [[ "$REMOVE_STEP4_LOGS" -eq 1 ]]; then
    remove_run_logs
fi

show_gpu_snapshot
log "done"
