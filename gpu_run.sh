#!/usr/bin/env bash

# STEP 1 terminal dashboard
# Usage:
#   ./run_gpu.sh
#   NO_COLOR=1 ./run_gpu.sh
#   VENV_PATH=/path/to/venv LOG_FILE=/path/to/log ./run_gpu.sh

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(uname -s)" == "Darwin" ]]; then
    DEFAULT_VENV_PATH="$SCRIPT_DIR/step_1_process/paddle_env"
else
    DEFAULT_VENV_PATH="$HOME/paddle_dev_test"
fi
VENV_PATH="${VENV_PATH:-$DEFAULT_VENV_PATH}"
# 실제 작업 스크립트는 step_1_process/ 안에 있다 — 이 파일은 step_1/(부모)
# 에 있으므로 한 단계 더 들어가야 한다.
WORKER="${WORKER:-$SCRIPT_DIR/step_1_process/step_1_run_decoder_data.sh}"
LOG_FILE="${LOG_FILE:-$SCRIPT_DIR/step_1.log}"

IS_TTY=0
[[ -t 1 ]] && IS_TTY=1

if [[ $IS_TTY -eq 1 && -z "${NO_COLOR:-}" ]]; then
    RESET=$'\033[0m'; BOLD=$'\033[1m'; DIM=$'\033[2m'
    RED=$'\033[38;5;203m'; GREEN=$'\033[38;5;84m'
    YELLOW=$'\033[38;5;221m'; CYAN=$'\033[38;5;45m'
    BLUE=$'\033[38;5;75m'; PURPLE=$'\033[38;5;141m'
    WHITE=$'\033[38;5;255m'; GRAY=$'\033[38;5;245m'
else
    RESET=""; BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""
    CYAN=""; BLUE=""; PURPLE=""; WHITE=""; GRAY=""
fi

terminal_width() {
    local width
    width="$(tput cols 2>/dev/null || printf '80')"
    [[ "$width" =~ ^[0-9]+$ ]] || width=80
    (( width < 60 )) && width=60
    (( width > 100 )) && width=100
    printf '%s' "$width"
}

repeat_char() {
    local char="$1" count="$2" line
    printf -v line '%*s' "$count" ''
    printf '%s' "${line// /$char}"
}

format_duration() {
    local seconds="$1"
    printf '%02d:%02d:%02d' \
        "$((seconds / 3600))" \
        "$(((seconds % 3600) / 60))" \
        "$((seconds % 60))"
}

print_banner() {
    local width inner
    width="$(terminal_width)"
    inner=$((width - 2))
    printf '\n%s%s╭%s╮%s\n' "$BOLD" "$CYAN" "$(repeat_char '─' "$inner")" "$RESET"
    printf '%s%s│%s  %s⚓  AI FISHING VESSEL · GPU PIPELINE%s\n' \
        "$BOLD" "$CYAN" "$RESET" "$WHITE" "$RESET"
    printf '%s%s│%s  %sDocument Decoder / STEP 1%s\n' \
        "$BOLD" "$CYAN" "$RESET" "$DIM" "$RESET"
    printf '%s%s╰%s╯%s\n\n' "$BOLD" "$CYAN" "$(repeat_char '─' "$inner")" "$RESET"
}

status_line() {
    local icon="$1" color="$2" label="$3" value="$4"
    printf '  %s%s%s  %-18s%s %s\n' "$color" "$icon" "$RESET" "$label" "$RESET" "$value"
}

section() {
    local title="$1" width
    width="$(terminal_width)"
    printf '\n%s%s┌─ %s %s%s\n' \
        "$BOLD" "$PURPLE" "$title" "$(repeat_char '─' "$((width - ${#title} - 5))")" "$RESET"
}

die() {
    status_line "✖" "$RED" "실행 중단" "$1" >&2
    exit 1
}

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM
    if [[ $exit_code -eq 130 || $exit_code -eq 143 ]]; then
        printf '\n'
        status_line "■" "$YELLOW" "사용자 중단" "파이프라인을 안전하게 종료했습니다."
    fi
    exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

print_banner

# 여기까지만 있고 그 뒤로 실제로 venv를 켜거나 워커 스크립트를 실행하는
# 코드가 없어서, 배너만 찍고 스크립트가 그냥 끝나버리는 상태였다. 아래가
# 빠져 있던 실행부다.

[[ -f "$VENV_PATH/bin/activate" ]] || die "GPU venv를 찾을 수 없습니다: $VENV_PATH"
[[ -x "$WORKER" ]] || die "작업 스크립트를 찾을 수 없습니다: $WORKER (chmod +x 했는지 확인)"

section "환경 준비"
status_line "●" "$CYAN" "GPU venv" "$VENV_PATH"
# shellcheck disable=SC1091
source "$VENV_PATH/bin/activate"
status_line "✔" "$GREEN" "venv 활성화" "완료"

section "STEP 1 파이프라인 실행"
status_line "●" "$CYAN" "작업 스크립트" "$WORKER"
status_line "●" "$CYAN" "로그 파일" "$LOG_FILE"
printf '\n%s실시간 로그: %stail -f "%s"%s\n\n' "$DIM" "$CYAN" "$LOG_FILE" "$RESET"

# 워커 스크립트(step_1_run_decoder_data.sh)는 ./data, ./build 같은 상대경로를
# 쓰므로, 실행 전 반드시 그 스크립트가 있는 디렉터리로 이동해야 한다.
cd -- "$(dirname -- "$WORKER")" || die "작업 디렉터리로 이동 실패: $(dirname -- "$WORKER")"

worker_start="$(date +%s)"
"$WORKER" >> "$LOG_FILE" 2>&1
worker_exit=$?
worker_elapsed=$(( $(date +%s) - worker_start ))

section "완료"
if [[ $worker_exit -eq 0 ]]; then
    status_line "✔" "$GREEN" "성공" "소요시간 $(format_duration "$worker_elapsed") · 로그: $LOG_FILE"
else
    status_line "✖" "$RED" "실패" "종료코드 $worker_exit · 로그 확인: $LOG_FILE"
fi
echo

exit "$worker_exit"
