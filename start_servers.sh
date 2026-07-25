#!/usr/bin/env bash
# 통합 리포트(PHP, :9000)와 DGX Spark 로컬 LLM 서버(FastAPI, :8500)를
# 한 번에 띄운다. Ctrl+C 한 번으로 둘 다 같이 내려간다.
#
# 이 스크립트는 터미널을 띄워둔 동안만 돈다 — 로그아웃 후에도 계속 켜두려면
# systemd 사용자 서비스(step1-report.service / step1-localllm.service)로
# 등록하는 걸 권장한다. GUIDE.md 18.2절 참고.
#
# 사용:
#   ./start_servers.sh
#
# 개별 포트를 바꾸고 싶으면 환경변수로:
#   REPORT_PORT=9000 LOCAL_LLM_PORT=8500 ./start_servers.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_PORT="${REPORT_PORT:-9000}"
LOCAL_LLM_PORT="${LOCAL_LLM_PORT:-8500}"
SBERT_ENV="$SCRIPT_DIR/step_2_process/sbert_env"

if [ ! -d "$SBERT_ENV" ]; then
  echo "[start_servers] sbert_env가 없습니다: $SBERT_ENV" >&2
  echo "[start_servers] GUIDE.md 15.2절대로 먼저 만들어 주세요." >&2
  exit 1
fi

PIDS=()
cleanup() {
  echo ""
  echo "[start_servers] 종료 중… (PID: ${PIDS[*]:-없음})"
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "[start_servers] 모두 종료했습니다."
}
trap cleanup EXIT INT TERM

echo "[start_servers] 통합 리포트(PHP) 시작 → http://localhost:${REPORT_PORT}/report.php"
(cd "$SCRIPT_DIR/gui_web" && php -S "localhost:${REPORT_PORT}") &
PIDS+=("$!")

echo "[start_servers] 로컬 LLM 서버(FastAPI) 시작 → http://localhost:${LOCAL_LLM_PORT}"
(
  cd "$SCRIPT_DIR/step_4_process"
  # shellcheck disable=SC1091
  source "$SBERT_ENV/bin/activate"
  LOCAL_LLM_PORT="$LOCAL_LLM_PORT" python3 local_llm_server.py
) &
PIDS+=("$!")

echo "[start_servers] 준비 완료. Ctrl+C로 둘 다 종료합니다."
wait
