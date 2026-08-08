#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUN_TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
START_EPOCH="$(date +%s)"

GENERATOR="${SCRIPT_DIR}/generate_personas.py"
PROMPT_PATH="${SCRIPT_DIR}/prompt.txt"
CORPUS_DIR="${SCRIPT_DIR}/../KMST"
MODEL_DIR="/home/jiwoo/.cache/huggingface/hub/models--Qwen--Qwen3-14B"
OUTPUT_DIR="/home/jiwoo/Desktop/workspace/SBERT/llm_based_root_cause_classification_system/persona_model"
LOG_DIR="${SCRIPT_DIR}/logs"

RUN_LOG="${LOG_DIR}/generate_personas_${RUN_TIMESTAMP}.log"
JSON_LOG="${LOG_DIR}/generate_personas_${RUN_TIMESTAMP}.jsonl"
GPU_LOG="${LOG_DIR}/generate_personas_${RUN_TIMESTAMP}_gpu.csv"
GPU_MONITOR_PID=""

mkdir -p "${LOG_DIR}"
ln -sfn "${RUN_LOG}" "${LOG_DIR}/latest.log"

if command -v jq >/dev/null 2>&1; then
  JQ_AVAILABLE=true
  ln -sfn "${JSON_LOG}" "${LOG_DIR}/latest.jsonl"
else
  JQ_AVAILABLE=false
fi

log() {
  local level="$1"
  local event="$2"
  local message="$3"
  local timestamp
  timestamp="$(date '+%Y-%m-%dT%H:%M:%S%z')"
  printf '[%s] [%s] [%s] %s\n' "${timestamp}" "${level}" "${event}" "${message}" \
    | tee -a "${RUN_LOG}"
  if [[ "${JQ_AVAILABLE}" == true ]]; then
    jq -cn \
      --arg timestamp "${timestamp}" \
      --arg level "${level}" \
      --arg event "${event}" \
      --arg message "${message}" \
      '{timestamp:$timestamp,level:$level,event:$event,message:$message}' >> "${JSON_LOG}"
  fi
}

stop_gpu_monitor() {
  if [[ -n "${GPU_MONITOR_PID}" ]] && kill -0 "${GPU_MONITOR_PID}" 2>/dev/null; then
    kill "${GPU_MONITOR_PID}" 2>/dev/null || true
    wait "${GPU_MONITOR_PID}" 2>/dev/null || true
  fi
}

finish() {
  local exit_code="$1"
  local end_epoch elapsed_seconds json_log_result
  stop_gpu_monitor
  end_epoch="$(date +%s)"
  elapsed_seconds=$((end_epoch - START_EPOCH))
  json_log_result="disabled"
  [[ "${JQ_AVAILABLE}" == true ]] && json_log_result="${JSON_LOG}"
  log INFO END "exit_code=${exit_code}, elapsed_seconds=${elapsed_seconds}, run_log=${RUN_LOG}, json_log=${json_log_result}, gpu_log=${GPU_LOG}"
  exit "${exit_code}"
}

trap stop_gpu_monitor INT TERM

log INFO START "Qwen3-14B GPU 페르소나 생성 시작"
log INFO MONITOR "진행률 보기: tail -n 50 -F ${LOG_DIR}/latest.log | grep --line-buffered -E 'PROGRESS|전체 예상 진행|ERROR|생성 완료'"

VENV_PYTHON="${SCRIPT_DIR}/.venv/bin/python"
if [[ -x "${VENV_PYTHON}" ]]; then
  PYTHON_BIN="${VENV_PYTHON}"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  log ERROR DEPENDENCY "Python 실행 파일을 찾을 수 없음"
  finish 127
fi

for required_path in "${GENERATOR}" "${PROMPT_PATH}" "${CORPUS_DIR}" "${MODEL_DIR}"; do
  if [[ ! -e "${required_path}" ]]; then
    log ERROR PREFLIGHT "필수 경로 없음: ${required_path}"
    finish 2
  fi
done

DEPENDENCY_REPORT="$(
  "${PYTHON_BIN}" -c '
import torch, transformers, accelerate
print("torch=" + torch.__version__ + ", transformers=" + transformers.__version__ + ", accelerate=" + accelerate.__version__ + ", cuda=" + str(torch.cuda.is_available()))
if not torch.cuda.is_available():
    raise SystemExit(3)
' 2>&1
)"
DEPENDENCY_EXIT_CODE=$?
if [[ "${DEPENDENCY_EXIT_CODE}" -ne 0 ]]; then
  log ERROR DEPENDENCY "GPU Python 환경 점검 실패: ${DEPENDENCY_REPORT}"
  finish 126
fi
log INFO DEPENDENCY "Python=${PYTHON_BIN}; ${DEPENDENCY_REPORT}"

if command -v nvidia-smi >/dev/null 2>&1; then
  printf 'timestamp,name,utilization_gpu\n' > "${GPU_LOG}"
  (
    while true; do
      timestamp="$(date '+%Y-%m-%dT%H:%M:%S%z')"
      nvidia-smi --query-gpu=name,utilization.gpu --format=csv,noheader,nounits \
        | while IFS= read -r metrics; do
            printf '%s,%s\n' "${timestamp}" "${metrics}"
          done
      sleep 5
    done
  ) >> "${GPU_LOG}" 2>&1 &
  GPU_MONITOR_PID=$!
  log INFO GPU_MONITOR "5초 간격 GPU 로그 시작: ${GPU_LOG}"
fi

"${PYTHON_BIN}" -u "${GENERATOR}" \
  --prompt "${PROMPT_PATH}" \
  --corpus "${CORPUS_DIR}" \
  --model "${MODEL_DIR}" \
  --output "${OUTPUT_DIR}" \
  --engine transformers \
  --force-gpu \
  --dtype bfloat16 \
  --max-new-tokens 6144 \
  --progress-every-tokens 64 \
  --progress-every-seconds 10 \
  2>&1 | while IFS= read -r line; do
    printf '[%s] [PYTHON] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${line}"
  done | tee -a "${RUN_LOG}"

EXIT_CODE=${PIPESTATUS[0]}
if [[ "${EXIT_CODE}" -ne 0 ]]; then
  log ERROR PYTHON_EXIT "생성 실패: exit_code=${EXIT_CODE}; 기존 dry-run 산출물 검증 생략"
  finish "${EXIT_CODE}"
fi
log INFO PYTHON_EXIT "Python 생성 정상 종료"

if [[ "${JQ_AVAILABLE}" == true ]]; then
  TOKEN_REPORT="${OUTPUT_DIR}/token_count_report.json"
  if [[ -f "${TOKEN_REPORT}" ]] && jq -e '.status == "EXACT"' "${TOKEN_REPORT}" >/dev/null; then
    TOKEN_SUMMARY="$(jq -r '"prompt_tokens=\(.prompt_tokens), corpus_tokens=\(.corpus_tokens), total_source_tokens=\(.total_source_tokens)"' "${TOKEN_REPORT}")"
    log INFO TOKEN_SUMMARY "${TOKEN_SUMMARY}"
  else
    log ERROR TOKEN_SUMMARY "정확한 token_count_report.json이 없음"
    EXIT_CODE=4
  fi

  JSON_VALIDATION_FAILED=0
  while IFS= read -r json_file; do
    if jq empty "${json_file}" >/dev/null 2>&1; then
      log INFO JSON_VALIDATION "PASS: ${json_file}"
    else
      log ERROR JSON_VALIDATION "FAIL: ${json_file}"
      JSON_VALIDATION_FAILED=1
    fi
  done < <(find "${OUTPUT_DIR}" -maxdepth 1 -type f -name '*.json' -print | sort)
  [[ "${JSON_VALIDATION_FAILED}" -ne 0 ]] && EXIT_CODE=4
fi

finish "${EXIT_CODE}"

