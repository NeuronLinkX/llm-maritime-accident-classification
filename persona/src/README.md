
# KMST Persona Builder

`prompt.txt`와 해양안전심판 법령·행정규칙 문서를 사용하여 Qwen3-14B 기반 3-Type Persona 산출물을 생성하는 Python 도구입니다.

## Python을 선택한 이유

- Hugging Face Transformers와 Qwen3 지원이 가장 성숙합니다.
- PyTorch GPU 추론, 4-bit 양자화, JSON Schema 검증을 한 환경에서 처리할 수 있습니다.
- 법령 Markdown 파싱과 검색 색인을 짧고 검증 가능한 코드로 구현할 수 있습니다.
- C++·Rust보다 모델·토크나이저 호환 문제를 줄이고 연구 코드를 빠르게 수정할 수 있습니다.

C++과 Rust가 추론 런타임 지연에는 유리할 수 있지만, 이 작업의 병목은 14B 모델 생성이며 법령 처리·프롬프트 실험·검증 생태계까지 고려하면 Python이 적합합니다.

## 배치 위치

다음 두 파일을 `src`에 둡니다.

```text
/home/jiwoo/Desktop/workspace/SBERT/persona/src/
├── prompt.txt
├── generate_personas.py
└── requirements.txt
```

법령 코퍼스는 다음 구조여야 합니다.

```text
/home/jiwoo/Desktop/workspace/SBERT/persona/KMST/
├── 행정규칙_...md
├── 법령_...md
└── 국제기준_IMO 해양사고 조사협약(CI Code) 개요.md
```

## 가상환경과 설치

```bash
cd /home/jiwoo/Desktop/workspace/SBERT/persona/src
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

4-bit 로딩을 사용할 경우:

```bash
python -m pip install bitsandbytes
```

FlashAttention을 이미 설치한 환경에서는 `--attn-implementation flash_attention_2`를 사용할 수 있습니다.

## 1단계: GPU 없이 전체 구조 검증

```bash
python generate_personas.py \
  --prompt ./prompt.txt \
  --corpus ../KMST \
  --engine dry-run
```

이 단계는 법령 10개와 IMO 문서의 존재·인코딩·전체 읽기·청크 생성·JSON Schema·산출물 구조를 검증합니다. 모델은 로드하지 않습니다.

## 2단계: Qwen3-14B로 최종 생성

```bash
python generate_personas.py \
  --prompt ./prompt.txt \
  --corpus ../KMST \
  --model ~/.cache/huggingface/hub/models--Qwen--Qwen3-14B \
  --engine transformers \
  --dtype bfloat16 \
  --max-new-tokens 6144
```

Hugging Face 캐시 루트를 지정하면 코드가 `refs/main` 또는 최신 `snapshots/<revision>`에서 `config.json`을 자동으로 찾습니다.

GPU 메모리가 부족한 경우:

```bash
python generate_personas.py \
  --prompt ./prompt.txt \
  --corpus ../KMST \
  --model ~/.cache/huggingface/hub/models--Qwen--Qwen3-14B \
  --engine transformers \
  --load-in-4bit
```

## 기본 출력 위치

```text
/home/jiwoo/Desktop/workspace/SBERT/llm_based_root_cause_classification_system/persona_model
```

다른 경로를 쓰려면 `--output`을 지정합니다.

## 정확도를 위한 구현 방식

1. 필수 국내 문서 10개와 IMO 문서를 EOF까지 읽습니다.
2. SHA-256, 인코딩, 시행일, 조문 수를 매니페스트에 기록합니다.
3. 조문·문단 단위로 분할합니다.
4. 세 페르소나별 검색 질의로 BM25 법령 근거를 선정합니다.
5. 각 필수 문서에서 최소 한 개의 관련 청크를 컨텍스트에 포함합니다.
6. Qwen3의 thinking 출력은 끄고 greedy decoding으로 재현성을 높입니다.
7. 필수 섹션과 법적 경계를 자동 검증하고 한 번 자동 보정합니다.
8. LLM 결과가 검증을 통과하지 못하면 안전한 결정론적 페르소나로 대체합니다.
9. JSON Schema와 법적 안전정책은 LLM이 변경할 수 없는 코드 상수로 유지합니다.

## 주의

- 이 코드는 모델 가중치를 재학습하지 않습니다. 법령 전체 로딩·색인·검색 기반 컨텍스트 주입 방식입니다.
- 자동 레이블은 `GOLDSET_CANDIDATE`이며 전문가 검토가 필요합니다.
- `official_ratio`는 재결서에 명시된 경우에만 추출합니다.
- `analytic_contribution_score`는 연구용 지표이며 법적 과실비율이 아닙니다.
- OBD 연계와 Prediction Model 구현은 제외됩니다.
