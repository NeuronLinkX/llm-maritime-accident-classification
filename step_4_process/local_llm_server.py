#!/usr/bin/env python3
"""STEP 4 — 분기 2: DGX Spark 로컬 LLM 서버 (다중 모델 비교 지원).

OpenAI Chat Completions API와 호환되는 서버를 FastAPI로 띄운다. 모델을
서버 시작 시 하나만 고정 로드하지 않고, MODEL_CATALOG에 등록된 여러 모델을
요청이 올 때(또는 /health?model=...&load=1로 미리) 지연 로딩(lazy load)한다
— "여러 모델을 받아서 같은 군집 레이블링 프롬프트로 비교"하는 게 목적이라,
한 프로세스 안에서 여러 모델을 오가며 쓸 수 있어야 한다.

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
    (기본 포트 8500)

주의: Llama-3.1-8B-Instruct는 Hugging Face에서 라이선스 동의 + 토큰이 필요한
"gated" 모델이다. huggingface.co에서 해당 저장소 라이선스에 동의하고
config/config.php에 HF_TOKEN을 등록해야 로드된다(동의 없으면 이 모델만
status="error"로 표시되고 나머지는 정상 동작).
"""
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

PORT = int(os.environ.get("LOCAL_LLM_PORT", "8500"))

CONFIG_PHP_PATH = Path(__file__).resolve().parent.parent / "config" / "config.php"


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

# 비교 카탈로그 — 크기 축(Qwen 2.5의 3B/7B/14B)과 계열 축(Mistral/Phi/EXAONE/Llama)을
# 함께 넣어서 "모델이 커지면 나아지는지"와 "다른 계열은 어떻게 다른지"를 둘 다 볼 수
# 있게 구성했다. 계열 축은 서로 다른 크기를 비교하면 크기 차이 때문에 성능 차이가
# 나는 건지 계열 차이 때문인지 구분이 안 되므로, 원래는 전부 7B 안팎으로 맞추려
# 했다(EXAONE 2.4B→7.8B, Llama 3B→8B). Phi만 예외 — "microsoft/Phi-3-small-8k-instruct"
# (7B)를 시도했으나 이 환경의 transformers(5.14.1)에서 그 저장소의 커스텀 모델링
# 코드가 요구하는 패키지를 다 설치해도(requests/tiktoken/einops/pytest) 마지막엔
# rope_scaling 설정 파싱 자체가 깨져("Field short_factor is required") 로드가
# 안 됐다 — 커스텀 코드와 이 transformers 버전 간의 호환성 문제로 보인다.
# 그래서 Phi는 크기를 맞추는 대신 트랜스포머스 네이티브 지원(커스텀 코드 불필요)이라
# 안정적으로 도는 "microsoft/Phi-3.5-mini-instruct"(3.8B)로 되돌렸다.
# Llama-3.1-8B-Instruct, EXAONE-3.5-7.8B-Instruct는 모델 저장소에 포함된 커스텀
# 코드를 실행해야 로드된다(trust_remote_code=True) — 둘 다 원 제작사(Meta/LG AI
# Research) 공식 저장소라 실행을 허용했다.
# Llama-3.1-8B-Instruct는 gated라 HF_TOKEN + 해당 저장소 라이선스 동의가 없으면
# 이 모델만 로드 실패(status="error")로 표시되고 나머지는 정상 동작한다.
MODEL_CATALOG = [
    "Qwen/Qwen2.5-3B-Instruct",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-14B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "microsoft/Phi-3.5-mini-instruct",
    "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
]

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
        tokenizer = AutoTokenizer.from_pretrained(name, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            name,
            dtype=torch.bfloat16 if device == "cuda" else torch.float32,
            device_map=device,
            trust_remote_code=True,
            attn_implementation="eager",  # flash-attn 미설치 환경 대응
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
    temperature: float = 0.2
    max_tokens: int = 2000
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

    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=req.max_tokens,
            temperature=max(req.temperature, 0.01),
            do_sample=req.temperature > 0,
            pad_token_id=tokenizer.eos_token_id,
        )
    generated = output_ids[0][inputs["input_ids"].shape[1]:]
    text = tokenizer.decode(generated, skip_special_tokens=True)

    return {
        "id": f"local-{int(time.time() * 1000)}",
        "model": name,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
    }


if __name__ == "__main__":
    import uvicorn

    print(f"[local_llm_server] 포트 {PORT}에서 대기 — 카탈로그 {len(MODEL_CATALOG)}개 모델(지연 로딩)")
    for m in MODEL_CATALOG:
        print(f"  - {m}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
