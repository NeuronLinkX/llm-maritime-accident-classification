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
#
# VPN으로 이 서버(DGX Spark)에 접속해 다른 컴퓨터 브라우저에서 열 것이므로
# REPORT_HOST도 로컬 LLM 서버와 마찬가지로 기본 0.0.0.0(모든 인터페이스)으로
# 바인딩한다. 이 기기 자신에서만 쓰고 싶으면 REPORT_HOST=localhost로 좁힐 수 있다.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_HOST="${REPORT_HOST:-0.0.0.0}"
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
  # $!로 기억한 PID가 자식(python3/php)까지 못 내렸을 수 있으니 포트 기준으로 한 번 더 정리한다.
  REPORT_PORT="$REPORT_PORT" LOCAL_LLM_PORT="$LOCAL_LLM_PORT" "$SCRIPT_DIR/stop_servers.sh"
  echo "[start_servers] 모두 종료했습니다."
}
trap cleanup EXIT INT TERM

echo "[start_servers] 기존에 떠 있는 프로세스 정리 중… (이전 실행이 남긴 게 있으면 여기서 내려감)"
REPORT_PORT="$REPORT_PORT" LOCAL_LLM_PORT="$LOCAL_LLM_PORT" "$SCRIPT_DIR/stop_servers.sh"

echo "[start_servers] STEP1~4 시뮬레이션 시작 → http://${REPORT_HOST}:${REPORT_PORT}/ (simulation.html, VPN 등 원격 접속 가능)"
(cd "$SCRIPT_DIR/gui_web" && php -S "${REPORT_HOST}:${REPORT_PORT}") &
PIDS+=("$!")

echo "[start_servers] 로컬 LLM 서버(FastAPI) 시작 → http://localhost:${LOCAL_LLM_PORT}"
(
  cd "$SCRIPT_DIR/step_4_process"
  # shellcheck disable=SC1091
  source "$SBERT_ENV/bin/activate"
  # bash는 PATH를 바꿔도 이미 "python3"를 찾아본 적 있는 셸이면 예전 위치를
  # 해시 캐시에 들고 있다 — activate로 venv를 PATH 맨 앞에 넣어도 그 캐시 때문에
  # 시스템 python3(torch 없음)가 그대로 실행되는 경우가 있었다(ModuleNotFoundError:
  # torch). 아예 venv 바이너리를 절대경로로 직접 불러 이 문제를 원천 차단한다.
  LOCAL_LLM_PORT="$LOCAL_LLM_PORT" "$SBERT_ENV/bin/python3" local_llm_server.py
) &
PIDS+=("$!")

echo "[start_servers] 준비 완료. Ctrl+C로 둘 다 종료합니다."
wait
