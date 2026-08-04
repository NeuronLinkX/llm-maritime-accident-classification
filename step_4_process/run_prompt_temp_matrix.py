#!/usr/bin/env python3
"""STEP 4 — 프롬프트 3종 x temperature 2종(=6칸) 실험 매트릭스를 자동으로 돈다.

run_multimodel_batch.py("전체 모델 비교 실행"을 반복)와 완전히 같은 절차를 6개
(prompt_variant, temperature) 조합에 대해 순서대로 반복한다. 아키텍처는 그대로 두고
(gui_web/lib_llm_common.php의 build_prompt()/config_version_key()에 요청 바디로
prompt_variant/temperature를 실어 보내는 오버라이드만 추가했다 — 값을 안 주면 기존
동작과 100% 동일하다), STEP4 "Prompt Engineering" 단계의 System Prompt/Task
Instruction만 config/prompt_variants.json의 A/B/C로 바꿔 끼우고 "Gateway" 단계의
temperature만 0 / 0.2로 바꿔가며 같은 파이프라인을 6번 통과시킨다.

    1. GET  api_experiment_version.php   (prompt_variant, temperature) -> config_version
                                          (모델 호출 없이 결정론적으로 계산, 진행 건수
                                           재조회에 씀 — 중간에 멈춰도 재실행 시 이어서 돔)
    2. GET  api_local_llm_models.php     카탈로그 조회
    3. GET  api_local_llm_health.php     모델별 로드 대기(?load=1로 폴링)
    4. POST api_local_llm_label.php      {prompt_variant, temperature}를 실어 군집 라벨링 요청
    5. POST api_save_multimodel_run.php  {prompt_variant, temperature}를 실어 결과 저장
             (step_4_process/output/runs/{config_version}/multimodel_runs.jsonl)

실행(포그라운드):
    source ../step_2_process/sbert_env/bin/activate
    python3 run_prompt_temp_matrix.py --target-per-cell 30 \
        --base-url http://127.0.0.1:9101 --exclude-models "Qwen2.5-7B-Instruct"

백그라운드로 띄우고 로그는 파일로(장시간 소요 — 칸당 30건 x 6칸, 회차당 모델 3개 순차 호출):
    nohup python3 run_prompt_temp_matrix.py --target-per-cell 30 \
        --base-url http://127.0.0.1:9101 --exclude-models "Qwen2.5-7B-Instruct" \
        > matrix_batch.log 2>&1 &
    disown
    tail -f matrix_batch.log

부분 성공 처리: run_multimodel_batch.py와 동일 — 회차에 포함된 모델 중 하나라도
실패하면 그 회차는 저장하지 않고 곧바로 같은 회차를 다시 시도한다.
"""
import argparse
import itertools
import json
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:9101"
DEFAULT_ENDPOINT = "http://localhost:8500/v1/chat/completions"
MODEL_LOAD_TIMEOUT_SEC = 280
MODEL_LOAD_POLL_SEC = 4
VARIANTS = ["A", "B", "C"]
TEMPERATURES = [0.0, 0.2]


def http_get(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(url: str, payload: dict, timeout: int = 220) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cell_config_version(base_url: str, variant: str, temperature: float) -> str:
    qs = urllib.parse.urlencode({"prompt_variant": variant, "temperature": temperature})
    data = http_get(f"{base_url}/api_experiment_version.php?{qs}")
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "config_version 조회 실패"))
    return data["config_version"]


def count_saved_runs(base_url: str, config_version: str) -> int:
    """이 칸에 저장된 기록 수. 폴더가 아직 한 번도 생성되지 않았으면(=이 config_version으로
    저장된 적이 없으면) api_list_multimodel_runs.php가 "알 수 없는 version"으로 400을 반환한다
    — 첫 회차 전에는 항상 그런 상태이므로 이 경우를 0건으로 취급한다."""
    qs = urllib.parse.urlencode({"version": config_version})
    try:
        data = http_get(f"{base_url}/api_list_multimodel_runs.php?{qs}")
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            return 0
        raise
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "기록 조회 실패"))
    return len(data.get("runs", []))


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


def run_one_pass(base_url: str, endpoint: str, exclude_models: list[str], variant: str, temperature: float) -> str | None:
    """한 회차를 돈다. 제외 목록에 없는 모델 전부가 성공해야 저장하고 그 id를 반환한다.
    (run_multimodel_batch.py의 run_one_pass()와 동일한 "전부 성공만 저장" 규칙.)"""
    catalog = http_get(f"{base_url}/api_local_llm_models.php?endpoint={urllib.parse.quote(endpoint)}")
    if not catalog.get("ok"):
        raise RuntimeError(catalog.get("error", "모델 목록 조회 실패"))
    all_ids = [m["id"] for m in catalog["data"]]
    model_ids = [mid for mid in all_ids if not any(ex.lower() in mid.lower() for ex in exclude_models)]

    results = []
    for mid in model_ids:
        try:
            print(f"    - {mid} 로드 대기…", flush=True)
            wait_for_model(base_url, endpoint, mid)
            print(f"    - {mid} 라벨링 요청 중… (variant={variant}, T={temperature})", flush=True)
            data = http_post(f"{base_url}/api_local_llm_label.php", {
                "endpoint": endpoint, "model": mid,
                "prompt_variant": variant, "temperature": temperature,
            })
            if data.get("ok"):
                results.append({"model": mid, "ok": True, "clusters": data["clusters"]})
                print(f"    - {mid} 완료 (군집 {len(data['clusters'])}개)", flush=True)
            else:
                results.append({"model": mid, "ok": False, "error": data.get("error", "알 수 없는 오류")})
                print(f"    - {mid} 실패: {data.get('error')}", flush=True)
        except Exception as exc:
            results.append({"model": mid, "ok": False, "error": str(exc)})
            print(f"    - {mid} 예외: {exc}", flush=True)

    ok_count = sum(1 for r in results if r["ok"])
    if ok_count < len(model_ids):
        print(f"    -> 폐기: {ok_count}/{len(model_ids)}개만 성공 — 저장하지 않고 다음 회차에서 다시 시도", flush=True)
        return None

    saved = http_post(f"{base_url}/api_save_multimodel_run.php", {
        "results": results, "prompt_variant": variant, "temperature": temperature,
    })
    if not saved.get("ok"):
        raise RuntimeError(saved.get("error", "저장 실패"))
    print(f"    -> 저장됨: {saved['id']} ({ok_count}/{len(model_ids)}개 전부 성공)", flush=True)
    return saved["id"]


def run_cell(base_url: str, endpoint: str, exclude_models: list[str], variant: str, temperature: float,
             target: int, sleep_between: float) -> None:
    config_version = cell_config_version(base_url, variant, temperature)
    print(f"[{variant}/T{temperature}] config_version={config_version}", flush=True)
    while True:
        current = count_saved_runs(base_url, config_version)
        if current >= target:
            print(f"[{variant}/T{temperature}] 목표 {target}건 도달(현재 {current}건). 이 칸 종료.", flush=True)
            return
        print(f"[{variant}/T{temperature}] 회차 시작 — 현재 {current}건 / 목표 {target}건", flush=True)
        try:
            run_one_pass(base_url, endpoint, exclude_models, variant, temperature)
        except Exception as exc:
            print(f"[{variant}/T{temperature}] 이번 회차 실패: {exc} — {sleep_between}초 뒤 계속", flush=True)
        time.sleep(sleep_between)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target-per-cell", type=int, default=30, help="칸(prompt_variant x temperature)마다 저장 기록이 몇 건이 될 때까지 돌릴지(기본 30)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="이 실험용 gui_web PHP 서버 주소")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="로컬 LLM chat completions 엔드포인트(기존 서버 그대로 공유)")
    ap.add_argument("--sleep-between", type=float, default=5.0, help="회차 사이 대기 시간(초, 기본 5)")
    ap.add_argument("--exclude-models", default="Qwen2.5-7B-Instruct", help="쉼표로 구분된 제외 모델명(부분일치, 대소문자 무시)")
    ap.add_argument("--variants", default=",".join(VARIANTS), help="돌릴 prompt_variant 목록(쉼표 구분, 기본 A,B,C)")
    ap.add_argument("--temperatures", default=",".join(str(t) for t in TEMPERATURES), help="돌릴 temperature 목록(쉼표 구분, 기본 0.0,0.2)")
    args = ap.parse_args()

    exclude_models = [s.strip() for s in args.exclude_models.split(",") if s.strip()]
    variants = [s.strip() for s in args.variants.split(",") if s.strip()]
    temperatures = [float(s.strip()) for s in args.temperatures.split(",") if s.strip()]
    cells = list(itertools.product(variants, temperatures))

    print(f"[run_prompt_temp_matrix] {len(cells)}칸 x 목표 {args.target_per_cell}건, 서버 {args.base_url}, "
          f"로컬 LLM {args.endpoint}, 제외 모델: {exclude_models}", flush=True)

    for variant, temperature in cells:
        run_cell(args.base_url, args.endpoint, exclude_models, variant, temperature,
                 args.target_per_cell, args.sleep_between)

    print("[run_prompt_temp_matrix] 6칸 전체 완료.", flush=True)


if __name__ == "__main__":
    main()
