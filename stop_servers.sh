#!/usr/bin/env bash
# start_servers.sh로 띄운 통합 리포트(PHP, :9000)와 로컬 LLM 서버(FastAPI, :8500)를
# 내린다. $!로 기억해둔 PID를 죽이는 방식은 이전 실행이 비정상 종료돼 자식
# 프로세스가 고아로 남거나(부모만 죽고 python3/php는 살아있는 경우), 터미널을
# 닫아서 PID를 잃어버린 경우 무용지물이 된다 — 그래서 "그 포트를 지금 실제로
# 물고 있는 프로세스"를 매번 다시 조회해서 내린다. start_servers.sh도 시작 전에
# 이 스크립트를 호출해 이전에 뭐가 떠 있었든 항상 깨끗한 상태에서 시작한다.
#
# 사용:
#   ./stop_servers.sh
#   REPORT_PORT=9000 LOCAL_LLM_PORT=8500 ./stop_servers.sh

REPORT_PORT="${REPORT_PORT:-9000}"
LOCAL_LLM_PORT="${LOCAL_LLM_PORT:-8500}"

pids_on_port() {
  ss -ltnp 2>/dev/null | awk -v p=":$1" '$4 ~ (p"$") {print $0}' | grep -oP 'pid=\K[0-9]+' | sort -u
}

stop_port() {
  local port="$1" label="$2"
  local pids
  pids="$(pids_on_port "$port")"
  if [ -z "$pids" ]; then
    echo "[stop_servers] ${label}(${port}): 실행 중인 프로세스 없음"
    return
  fi
  echo "[stop_servers] ${label}(${port}): PID ${pids//$'\n'/,} 종료 중…"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  for _ in $(seq 1 10); do
    sleep 0.3
    pids="$(pids_on_port "$port")"
    [ -z "$pids" ] && break
  done
  if [ -n "$pids" ]; then
    echo "[stop_servers] ${label}(${port}): SIGTERM으로 안 내려가 강제 종료(kill -9)"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

stop_port "$REPORT_PORT" "리포트(PHP)"
stop_port "$LOCAL_LLM_PORT" "로컬 LLM 서버"
echo "[stop_servers] 완료."
