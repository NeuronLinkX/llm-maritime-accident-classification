#!/usr/bin/env python3
"""STEP 4 — gui_web "전체 모델 비교 실행" 버튼을 터미널에서 반복 실행한다.

브라우저를 열어두고 버튼을 계속 누르는 대신, 이 스크립트가 백그라운드에서
저장 기록이 목표 건수(기본 100건)에 도달할 때까지 같은 절차를 자동으로
반복한다. 버튼이 하는 일과 완전히 동일하다 — 같은 PHP 엔드포인트를 같은
순서로 호출한다:

    1. GET  api_local_llm_models.php     카탈로그 조회
    2. GET  api_local_llm_health.php     모델별 로드 대기(?load=1로 폴링)
    3. POST api_local_llm_label.php      군집 라벨링 요청
    4. POST api_save_multimodel_run.php  결과 저장 (step_4_process/output/multimodel_runs.jsonl)

실행(포그라운드):
    source ../step_2_process/sbert_env/bin/activate
    python3 run_multimodel_batch.py                  # 총 100건까지
    python3 run_multimodel_batch.py --target 50

백그라운드로 띄우고 로그는 파일로:
    nohup python3 run_multimodel_batch.py > batch.log 2>&1 &
    tail -f batch.log

주의: DGX Spark의 GPU 하나를 계속 쓰는 작업이라 100건까지 채우는 데 꽤 오래
걸릴 수 있다(회차당 수 분 × 필요 회차 수). 중간에 멈춰도(Ctrl+C, 프로세스
종료) 이미 저장된 기록은 그대로 남아 있고, 다시 실행하면 남은 건수만큼만
이어서 돈다(중복으로 100건을 넘겨 채우지 않음).
"""
import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RUNS_FILE = Path(__file__).resolve().parent / "output" / "multimodel_runs.jsonl"
DEFAULT_BASE_URL = "http://localhost:9000"
DEFAULT_ENDPOINT = "http://localhost:8500/v1/chat/completions"
MODEL_LOAD_TIMEOUT_SEC = 280
MODEL_LOAD_POLL_SEC = 4


def http_get(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(url: str, payload: dict, timeout: int = 220) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def count_saved_runs() -> int:
    if not RUNS_FILE.is_file():
        return 0
    with open(RUNS_FILE, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def wait_for_model(base_url: str, endpoint: str, model: str) -> None:
    start = time.time()
    while True:
        url = (f"{base_url}/api_local_llm_health.php?endpoint={urllib.parse.quote(endpoint)}"
               f"&model={urllib.parse.quote(model)}&load=1")
        data = http_get(url)
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "상태 확인 실패"))
        status = data.get("status")
        if status == "ok":
            return
        if status == "error":
            raise RuntimeError(data.get("error") or f"{model} 로드 실패")
        if time.time() - start > MODEL_LOAD_TIMEOUT_SEC:
            raise TimeoutError(f"{model} 로드 대기 시간 초과({MODEL_LOAD_TIMEOUT_SEC}초)")
        time.sleep(MODEL_LOAD_POLL_SEC)


def run_one_pass(base_url: str, endpoint: str) -> str:
    catalog = http_get(f"{base_url}/api_local_llm_models.php?endpoint={urllib.parse.quote(endpoint)}")
    if not catalog.get("ok"):
        raise RuntimeError(catalog.get("error", "모델 목록 조회 실패"))
    model_ids = [m["id"] for m in catalog["data"]]

    results = []
    for mid in model_ids:
        try:
            print(f"  - {mid} 로드 대기…", flush=True)
            wait_for_model(base_url, endpoint, mid)
            print(f"  - {mid} 라벨링 요청 중…", flush=True)
            data = http_post(f"{base_url}/api_local_llm_label.php", {"endpoint": endpoint, "model": mid})
            if data.get("ok"):
                results.append({"model": mid, "ok": True, "clusters": data["clusters"]})
                print(f"  - {mid} 완료 (군집 {len(data['clusters'])}개)", flush=True)
            else:
                results.append({"model": mid, "ok": False, "error": data.get("error", "알 수 없는 오류")})
                print(f"  - {mid} 실패: {data.get('error')}", flush=True)
        except Exception as exc:
            results.append({"model": mid, "ok": False, "error": str(exc)})
            print(f"  - {mid} 예외: {exc}", flush=True)

    saved = http_post(f"{base_url}/api_save_multimodel_run.php", {"results": results})
    if not saved.get("ok"):
        raise RuntimeError(saved.get("error", "저장 실패"))
    print(f"  -> 저장됨: {saved['id']}", flush=True)
    return saved["id"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=int, default=100, help="저장 기록이 총 몇 건이 될 때까지 돌릴지(기본 100)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="gui_web PHP 서버 주소(기본 http://localhost:9000)")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="로컬 LLM chat completions 엔드포인트")
    ap.add_argument("--sleep-between", type=float, default=5.0, help="회차 사이 대기 시간(초, 기본 5)")
    args = ap.parse_args()

    print(f"[run_multimodel_batch] 목표 {args.target}건, 서버 {args.base_url}, 로컬 LLM {args.endpoint}", flush=True)

    while True:
        current = count_saved_runs()
        if current >= args.target:
            print(f"[run_multimodel_batch] 목표 {args.target}건 도달(현재 {current}건). 종료합니다.", flush=True)
            break
        print(f"[run_multimodel_batch] 회차 시작 — 현재 {current}건 / 목표 {args.target}건", flush=True)
        try:
            run_one_pass(args.base_url, args.endpoint)
        except Exception as exc:
            print(f"[run_multimodel_batch] 이번 회차 실패: {exc} — {args.sleep_between}초 뒤 계속", flush=True)
        time.sleep(args.sleep_between)


if __name__ == "__main__":
    main()
