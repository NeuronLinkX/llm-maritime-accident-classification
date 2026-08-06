#!/usr/bin/env python3
"""STEP 4 — 분기 2: DGX Spark 로컬 LLM 서버 (다중 모델 비교 지원).

OpenAI Chat Completions API와 호환되는 서버를 FastAPI로 띄운다. 모델을
서버 시작 시 하나만 고정 로드하지 않고, MODEL_CATALOG에 등록된 여러 모델을
요청이 올 때(또는 /health?model=...&load=1로 미리) 지연 로딩(lazy load)한다
— "여러 모델을 받아서 같은 군집 레이블링 프롬프트로 비교"하는 게 목적이라,
한 프로세스 안에서 여러 모델을 오가며 쓸 수 있어야 한다.

설정 (항상 config/config.json을 먼저 참고)
    이 서버는 시작할 때마다 항상 <repo root>/config/config.json을 읽어
    포트·모델 카탈로그·생성 기본값을 결정한다(load_config() 참고).
    파일이 없거나 JSON이 깨져 있으면 예외를 던지지 않고 경고만 출력한 뒤
    아래 코드에 내장된 기본값(_DEFAULT_*)으로 계속 동작한다 — config.json은
    "있으면 우선 적용되는 오버라이드"이지 필수 파일이 아니다.
    환경변수 LOCAL_LLM_PORT / LOCAL_LLM_CONFIG가 있으면 config.json보다도
    우선한다(우선순위: 환경변수 > config.json > 내장 기본값).

엔드포인트
    GET  /v1/models                     카탈로그 + 각 모델 로드 상태
    GET  /health?model=X[&load=1]       특정 모델 상태 확인(load=1이면 아직
                                         안 받았을 때 다운로드/로드를 트리거)
    POST /v1/chat/completions           OpenAI 호환. body의 "model" 필드로
                                         어떤 모델을 쓸지 고른다(없으면 카탈로그
                                         첫 번째).

실행
    source ../step_2_process/sbert_env/bin/activate
    python3 local_llm_server.py
    (기본 포트 8500, config/config.json의 server.port로 재정의 가능)

주의: Llama-3.1-8B-Instruct는 Hugging Face에서 라이선스 동의 + 토큰이 필요한
"gated" 모델이다. huggingface.co에서 해당 저장소 라이선스에 동의하고
config/config.php에 HF_TOKEN을 등록해야 로드된다(동의 없으면 이 모델만
status="error"로 표시되고 나머지는 정상 동작).
"""
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

# 모델 4개 다 ~/.cache/huggingface/hub에 이미 완전히 캐시돼 있는데도, huggingface_hub는
# from_pretrained() 때마다 기본적으로 "캐시가 최신인지" 확인하려고 허깅페이스 서버에
# 네트워크 요청을 한 번 날린다 — 이 기기가 와이파이로 붙어 있어 이 확인 요청이 느려지거나
# 응답이 안 오면, 모델 자체는 로컬에 있는데도 로딩 전체가 그 응답을 기다리며 멈춰버린다
# (실측: 14B 로드가 GPU 0% 상태로 huggingface.co쪽 소켓만 열어둔 채 몇 분씩 멈춤).
# "로컬 LLM" 경로는 애초에 인터넷 연결 없이 도는 게 목적(GUIDE.md)이므로, transformers를
# import하기 전에 오프라인 모드를 강제해 이 네트워크 의존 자체를 없앤다.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PHP_PATH = REPO_ROOT / "config" / "config.php"
CONFIG_JSON_PATH = Path(os.environ["LOCAL_LLM_CONFIG"]) if os.environ.get("LOCAL_LLM_CONFIG") \
    else REPO_ROOT / "config" / "config.json"

# 카탈로그/포트/생성 기본값이 config.json에 없을 때 쓰는 내장 기본값.
# config.json 자체가 없어도(또는 일부 키만 있어도) 서버가 예전과 동일하게 동작해야 한다.
_DEFAULT_PORT = 8500
_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_TEMPERATURE = 0.2
_DEFAULT_MAX_TOKENS = 2000
_DEFAULT_CATALOG = [
    "Qwen/Qwen2.5-3B-Instruct",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-14B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
]


def load_config() -> dict:
    """config/config.json을 읽는다.

    파일이 없거나 파싱에 실패해도 예외를 던지지 않는다 — STEP1 전처리와 같은
    원칙("실패는 조용히 폴백")으로, 이 서버도 config.json 없이 예전 하드코딩
    기본값만으로 계속 동작할 수 있어야 한다. 대신 왜 기본값으로 넘어갔는지는
    항상 화면에 남긴다.
    """
    if not CONFIG_JSON_PATH.is_file():
        print(f"[local_llm_server] config.json을 찾을 수 없습니다({CONFIG_JSON_PATH}) — 내장 기본값 사용.")
        return {}
    try:
        with open(CONFIG_JSON_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        print(f"[local_llm_server] 설정 로드: {CONFIG_JSON_PATH}")
        return cfg if isinstance(cfg, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[local_llm_server] config.json 파싱 실패({exc}) — 내장 기본값 사용.")
        return {}


CONFIG = load_config()

PORT = int(os.environ.get("LOCAL_LLM_PORT") or CONFIG.get("server", {}).get("port", _DEFAULT_PORT))
HOST = CONFIG.get("server", {}).get("host", _DEFAULT_HOST)

_gen_cfg = CONFIG.get("generation", {})
DEFAULT_TEMPERATURE = _gen_cfg.get("default_temperature", _DEFAULT_TEMPERATURE)
DEFAULT_MAX_TOKENS = _gen_cfg.get("default_max_tokens", _DEFAULT_MAX_TOKENS)


def load_hf_token_from_config():
    """config/config.php의 define('HF_TOKEN', '...')를 읽어 환경변수로 세팅한다.

    LLaMA/Gemma 같은 gated 모델은 (1) HF 계정으로 라이선스 동의 + (2) 토큰
    둘 다 있어야 받아진다. 토큰이 없으면 그냥 넘어가고(공개 모델만 카탈로그에서
    동작), huggingface_hub가 인증 없이 요청해 gated 모델만 403으로 실패한다.
    """
    if os.environ.get("HF_TOKEN"):
        return  # 이미 셸에서 export된 값이 있으면 그걸 우선한다
    if not CONFIG_PHP_PATH.is_file():
        return
    text = CONFIG_PHP_PATH.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"define\(\s*['\"]HF_TOKEN['\"]\s*,\s*['\"]([^'\"]+)['\"]", text)
    if m and m.group(1).strip():
        token = m.group(1).strip()
        os.environ["HF_TOKEN"] = token
        os.environ["HUGGING_FACE_HUB_TOKEN"] = token  # 구버전 라이브러리 호환
        print("[local_llm_server] config.php에서 HF_TOKEN을 읽어와 적용했습니다 (gated 모델 접근 가능).")


load_hf_token_from_config()

# 비교 카탈로그 — 크기 축(Qwen 2.5의 3B/7B/14B)과 계열 축(Qwen vs Llama)을 본다.
#
# 원래는 Mistral/Phi/EXAONE도 계열 축에 넣어 크기(~7-8B)를 맞추려 했으나, 이
# 환경(transformers 5.14.1)에서 셋 다 안정적으로 돌지 않아 카탈로그에서 뺐다:
#   - microsoft/Phi-3-small-8k-instruct: 커스텀 모델링 코드가 요구하는 패키지를
#     다 설치해도(requests/tiktoken/einops/pytest) rope_scaling 파싱에서
#     "Field short_factor is required"로 로드 자체가 실패.
#   - microsoft/Phi-3.5-mini-instruct(대체 시도): 로드는 되지만 생성 시
#     "AttributeError: 'DynamicCache' object has no attribute 'seen_tokens'"로
#     크래시 — trust_remote_code=True가 네이티브 Phi3 구현 대신 저장소의
#     구버전 커스텀 코드를 타면서 이 transformers 버전과 안 맞음.
#   - LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct: 로드는 되지만 생성 시
#     "TypeError: create_causal_mask() got an unexpected keyword argument
#     'input_embeds'"로 크래시 — 저장소 커스텀 코드가 이 transformers 버전의
#     내부 함수 시그니처보다 오래됨.
#   - mistralai/Mistral-7B-Instruct-v0.3: 이 환경에서 반복적으로 호출 실패.
# 셋 다 "trust_remote_code로 저장소 커스텀 코드를 실행 → 이 transformers
# 버전과 안 맞음"이라는 같은 패턴이라, transformers를 올리거나 저장소가
# 코드를 업데이트하기 전까지는 재추가하지 않는다.
#
# 카탈로그는 config.json → 없으면 내장 기본값(_DEFAULT_CATALOG) 순으로 정한다.
MODEL_CATALOG = CONFIG.get("models", {}).get("default", {}).get("catalog") or _DEFAULT_CATALOG

models_state = {
    name: {"status": "not_loaded", "model": None, "tokenizer": None, "device": None, "error": None}
    for name in MODEL_CATALOG
}
load_locks = {name: threading.Lock() for name in MODEL_CATALOG}


def _load_model_sync(name: str):
    from transformers import AutoModelForCausalLM, AutoTokenizer

    st = models_state[name]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[local_llm_server] 로드 시작: {name} (device={device})")
    t0 = time.time()
    try:
        tokenizer = AutoTokenizer.from_pretrained(name, trust_remote_code=True, local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(
            name,
            dtype=torch.bfloat16 if device == "cuda" else torch.float32,
            device_map=device,
            trust_remote_code=True,
            local_files_only=True,  # 위 HF_HUB_OFFLINE과 이중으로 — 캐시에 없으면 조용히 네트워크로 새는 대신 바로 에러를 낸다
            # flash-attn은 이 환경(aarch64, GB10)에 사전빌드 wheel이 없어 설치가 무겁다.
            # 대신 PyTorch 내장 sdpa(scaled dot product attention)를 쓴다 — 별도 설치 없이
            # eager보다 시퀀스 길이에 따른 시간·메모리 증가폭이 훨씬 작다(eager는 O(n^2)
            # 어텐션 행렬을 그대로 들고 있어 프롬프트가 길수록 느려지고 메모리도 더 쓴다).
            attn_implementation="sdpa",
        )
        st.update(model=model, tokenizer=tokenizer, device=device, status="ok", error=None)
        print(f"[local_llm_server] 로드 완료: {name} ({time.time() - t0:.1f}초)")
    except Exception as exc:
        st.update(status="error", error=str(exc))
        print(f"[local_llm_server] 로드 실패: {name} — {exc}")


def ensure_loading(name: str) -> bool:
    """카탈로그에 있으면 필요 시 백그라운드 로딩을 시작하고 True 반환."""
    if name not in models_state:
        return False
    st = models_state[name]
    if st["status"] == "not_loaded":
        with load_locks[name]:
            if st["status"] == "not_loaded":
                st["status"] = "loading"
                threading.Thread(target=_load_model_sync, args=(name,), daemon=True).start()
    return True


app = FastAPI(title="STEP4 Local LLM Server (multi-model)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.get("/v1/models")
def list_models():
    return {
        "data": [
            {"id": name, "status": s["status"], "device": s["device"], "error": s["error"]}
            for name, s in models_state.items()
        ]
    }


@app.get("/health")
def health(model: Optional[str] = None, load: bool = False):
    if model is None:
        loaded = [n for n, s in models_state.items() if s["status"] == "ok"]
        return {"status": "ok", "catalog": list(models_state.keys()), "loaded": loaded}

    if model not in models_state:
        raise HTTPException(404, f"카탈로그에 없는 모델입니다: {model}")
    if load:
        ensure_loading(model)
    st = models_state[model]
    return {"status": st["status"], "model": model, "device": st["device"], "error": st["error"]}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: list[ChatMessage]
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    response_format: Optional[dict] = None  # 참고만 함 — 실제 JSON 준수는 프롬프트 지시에 의존


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    name = req.model or MODEL_CATALOG[0]
    if name not in models_state:
        raise HTTPException(404, f"카탈로그에 없는 모델입니다: {name}")

    ensure_loading(name)
    st = models_state[name]
    if st["status"] == "loading":
        raise HTTPException(503, f"{name} 모델 로드 중입니다. 잠시 후 다시 시도하세요.")
    if st["status"] == "error":
        raise HTTPException(500, f"{name} 로드 실패: {st['error']}")
    if st["status"] != "ok":
        raise HTTPException(500, f"{name} 상태를 알 수 없습니다.")

    tokenizer = st["tokenizer"]
    model = st["model"]
    device = st["device"]

    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    with torch.inference_mode():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=req.max_tokens,
            temperature=max(req.temperature, 0.01),
            do_sample=req.temperature > 0,
            pad_token_id=tokenizer.eos_token_id,
        )
    generated = output_ids[0][inputs["input_ids"].shape[1]:]
    text = tokenizer.decode(generated, skip_special_tokens=True)

    # GB10은 GPU/CPU 메모리가 하나의 통합 풀이라, PyTorch가 "재사용을 위해 캐시로만
    # 들고 있는" 메모리도 시스템 전체 가용 RAM을 그대로 깎아먹는다. 요청마다 중간
    # 텐서를 명시적으로 지우고 캐시를 비우지 않으면, 여러 모델을 오가며 비교하는
    # 이 서버의 특성상(모델을 안 내리고 계속 상주시킴) 메모리가 프로세스 수명 내내
    # 단조증가만 하다가 결국 스와핑을 유발해 응답이 느려지고 타임아웃이 난다.
    del output_ids, generated, inputs
    if device == "cuda":
        torch.cuda.empty_cache()

    return {
        "id": f"local-{int(time.time() * 1000)}",
        "model": name,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
    }


if __name__ == "__main__":
    import uvicorn

    config_source = str(CONFIG_JSON_PATH) if CONFIG else f"{CONFIG_JSON_PATH} (없음/파싱실패 — 내장 기본값)"
    print(f"[local_llm_server] 설정 파일: {config_source}")
    print(f"[local_llm_server] {HOST}:{PORT}에서 대기 — 카탈로그 {len(MODEL_CATALOG)}개 모델(지연 로딩), "
          f"temperature 기본값 {DEFAULT_TEMPERATURE}, max_tokens 기본값 {DEFAULT_MAX_TOKENS}")
    for m in MODEL_CATALOG:
        print(f"  - {m}")
    uvicorn.run(app, host=HOST, port=PORT)
