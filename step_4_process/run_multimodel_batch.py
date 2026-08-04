#!/usr/bin/env python3
"""STEP 4 — gui_web "전체 모델 비교 실행" 버튼을 터미널에서 반복 실행한다.

브라우저를 열어두고 버튼을 계속 누르는 대신, 이 스크립트가 백그라운드에서
저장 기록이 목표 건수(기본 100건)에 도달할 때까지 같은 절차를 자동으로
반복한다. 버튼이 하는 일과 완전히 동일하다 — 같은 PHP 엔드포인트를 같은
순서로 호출한다:

    1. GET  api_local_llm_models.php     카탈로그 조회
    2. GET  api_local_llm_health.php     모델별 로드 대기(?load=1로 폴링)
    3. POST api_local_llm_label.php      군집 라벨링 요청
    4. POST api_save_multimodel_run.php  결과 저장
             (step_4_process/output/runs/{설정버전}/multimodel_runs.jsonl —
              SAMPLES_PER_CLUSTER 등 STEP4 그라운딩 설정이 바뀌면 자동으로
              다른 폴더에 쌓인다. 진행 건수도 로컬 파일을 직접 세지 않고
              api_list_multimodel_runs.php를 통해 "현재 설정 버전" 기준으로 센다.)

실행(포그라운드):
    source ../step_2_process/sbert_env/bin/activate
    python3 run_multimodel_batch.py                  # 총 100건까지
    python3 run_multimodel_batch.py --target 50

특정 모델 제외(예: Qwen2.5-7B-Instruct만 유독 응답이 길어져 타임아웃이 잦을 때):
    python3 run_multimodel_batch.py --target 100 --exclude-models "Qwen2.5-7B-Instruct"

백그라운드로 띄우고 로그는 파일로:
    nohup python3 run_multimodel_batch.py --target 100 --exclude-models "Qwen2.5-7B-Instruct" \
        > batch.log 2>&1 &
    disown
    tail -f batch.log

주의: DGX Spark의 GPU 하나를 계속 쓰는 작업이라 100건까지 채우는 데 꽤 오래
걸릴 수 있다(회차당 수 분 × 필요 회차 수). 중간에 멈춰도(Ctrl+C, 프로세스
종료) 이미 저장된 기록은 그대로 남아 있고, 다시 실행하면 남은 건수만큼만
이어서 돈다(중복으로 100건을 넘겨 채우지 않음).

부분 성공 처리: 회차에 포함된 모델 중 하나라도 실패하면 그 회차는 아예
저장하지 않고(디스크에 쓰지 않음) 곧바로 같은 회차를 다시 시도한다 — 목표
건수는 "포함된 모델 전부가 성공한 회차"의 개수를 센다.
"""
import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://localhost:9000"
DEFAULT_ENDPOINT = "http://localhost:8500/v1/chat/completions"
MODEL_LOAD_TIMEOUT_SEC = 280
MODEL_LOAD_POLL_SEC = 4
LABEL_TIMEOUT_SEC = 420
LABEL_RETRIES = 3
RETRY_SLEEP_SEC = 4


def http_get(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(url: str, payload: dict, timeout: int = LABEL_TIMEOUT_SEC) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def describe_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace").strip()
    except Exception:
        body = ""
    if body:
        return f"HTTP {exc.code}: {body}"
    return f"HTTP {exc.code}: {exc.reason}"


def label_with_retries(base_url: str, endpoint: str, model: str) -> dict:
    payload = {"endpoint": endpoint, "model": model}
    last_exc = None
    for attempt in range(1, LABEL_RETRIES + 1):
        try:
            return http_post(f"{base_url}/api_local_llm_label.php", payload, timeout=LABEL_TIMEOUT_SEC)
        except urllib.error.HTTPError as exc:
            last_exc = RuntimeError(describe_http_error(exc))
        except Exception as exc:
            last_exc = exc
        if attempt < LABEL_RETRIES:
            print(f"  - {model} 재시도 {attempt}/{LABEL_RETRIES - 1} 실패: {last_exc} — {RETRY_SLEEP_SEC}초 뒤 재시도", flush=True)
            time.sleep(RETRY_SLEEP_SEC)
    raise last_exc if last_exc is not None else RuntimeError(f"{model} 라벨링 요청 실패")


def count_saved_runs(base_url: str) -> int:
    """현재 STEP4 설정 버전(config_version_key) 폴더에 저장된 기록 건수.

    로컬 파일을 직접 세지 않고 api_list_multimodel_runs.php를 호출한다 —
    버전 파라미터를 안 주면 서버가 "현재 코드 설정" 폴더를 기본값으로 골라주므로,
    이 스크립트가 lib_llm_common.php의 해시 계산 로직을 따로 복제할 필요가 없다.
    """
    data = http_get(f"{base_url}/api_list_multimodel_runs.php")
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "기록 조회 실패"))
    return len(data.get("runs", [])), data.get("config_version", "?")


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


def run_one_pass(base_url: str, endpoint: str, exclude_models: list[str]) -> str | None:
    """한 회차를 돌린다. 제외 목록에 없는 모델 전부가 성공해야 저장하고, 그
    id를 반환한다. 하나라도 실패하면 저장하지 않고 None을 반환한다 — 이렇게
    "완전히 성공한 회차만" 남기면, main()의 while 루프가 카운트 증가 없이
    자동으로 같은 회차를 다시 시도하므로 별도의 "부분 성공 기록을 지우는"
    로직이 필요 없다(애초에 디스크에 쓰지 않으므로 지울 것도 없다).
    """
    catalog = http_get(f"{base_url}/api_local_llm_models.php?endpoint={urllib.parse.quote(endpoint)}")
    if not catalog.get("ok"):
        raise RuntimeError(catalog.get("error", "모델 목록 조회 실패"))
    all_ids = [m["id"] for m in catalog["data"]]
    model_ids = [mid for mid in all_ids if not any(ex.lower() in mid.lower() for ex in exclude_models)]
    if exclude_models:
        skipped = [mid for mid in all_ids if mid not in model_ids]
        if skipped:
            print(f"  (제외: {', '.join(skipped)})", flush=True)

    results = []
    for mid in model_ids:
        try:
            print(f"  - {mid} 로드 대기…", flush=True)
            wait_for_model(base_url, endpoint, mid)
            print(f"  - {mid} 라벨링 요청 중…", flush=True)
            data = label_with_retries(base_url, endpoint, mid)
            if data.get("ok"):
                results.append({"model": mid, "ok": True, "clusters": data["clusters"]})
                print(f"  - {mid} 완료 (군집 {len(data['clusters'])}개)", flush=True)
            else:
                results.append({"model": mid, "ok": False, "error": data.get("error", "알 수 없는 오류")})
                print(f"  - {mid} 실패: {data.get('error')}", flush=True)
        except Exception as exc:
            results.append({"model": mid, "ok": False, "error": str(exc)})
            print(f"  - {mid} 예외: {exc}", flush=True)

    ok_count = sum(1 for r in results if r["ok"])
    if ok_count < len(model_ids):
        print(f"  -> 폐기: {ok_count}/{len(model_ids)}개만 성공 — 저장하지 않고 다음 회차에서 다시 시도", flush=True)
        return None

    saved = http_post(f"{base_url}/api_save_multimodel_run.php", {"results": results})
    if not saved.get("ok"):
        raise RuntimeError(saved.get("error", "저장 실패"))
    print(f"  -> 저장됨: {saved['id']} ({ok_count}/{len(model_ids)}개 전부 성공)", flush=True)
    return saved["id"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=int, default=100, help="저장 기록이 총 몇 건이 될 때까지 돌릴지(기본 100)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="gui_web PHP 서버 주소(기본 http://localhost:9000)")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="로컬 LLM chat completions 엔드포인트")
    ap.add_argument("--sleep-between", type=float, default=5.0, help="회차 사이 대기 시간(초, 기본 5)")
    ap.add_argument("--exclude-models", default="", help="쉼표로 구분된 제외할 모델명(부분일치, 대소문자 무시). 예: 'Qwen2.5-7B-Instruct'")
    args = ap.parse_args()
    exclude_models = [s.strip() for s in args.exclude_models.split(",") if s.strip()]

    print(f"[run_multimodel_batch] 목표 {args.target}건, 서버 {args.base_url}, 로컬 LLM {args.endpoint}"
          + (f", 제외 모델: {exclude_models}" if exclude_models else ""), flush=True)

    while True:
        current, config_version = count_saved_runs(args.base_url)
        if current >= args.target:
            print(f"[run_multimodel_batch] [{config_version}] 목표 {args.target}건 도달(현재 {current}건). 종료합니다.", flush=True)
            break
        print(f"[run_multimodel_batch] [{config_version}] 회차 시작 — 현재 {current}건 / 목표 {args.target}건", flush=True)
        try:
            run_one_pass(args.base_url, args.endpoint, exclude_models)
        except Exception as exc:
            print(f"[run_multimodel_batch] 이번 회차 실패: {exc} — {args.sleep_between}초 뒤 계속", flush=True)
        time.sleep(args.sleep_between)


if __name__ == "__main__":
    main()
