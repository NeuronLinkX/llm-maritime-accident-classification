# 페르소나 문서 vs 무페르소나 문서 — 문서쌍 비교(document-pair ablation)

이 디렉터리(`task_01/02/03_no_persona_*.md`)는 `persona_01/02/03_*.md`와 독립적으로
작성된 "무페르소나 버전" 문서입니다. `step4/` 패키지가 이 문서들을 실제로 읽어서
정량 비교를 실행합니다 — **더 이상 폐기된 초안이 아닙니다.**

## STEP4에는 지금 두 가지 서로 다른 비교 모드가 있습니다

| 모드 | 무엇을 비교하는가 | 실행 config | identity_on/off의 의미 |
| --- | --- | --- | --- |
| `identity_marker` (기존) | `persona_0N_fact_evidence_analyst.md` **한 문서** 안에서 `<!-- IDENTITY_BLOCK_START/END -->` 마커로 정체성 문장 한 줄만 넣고/빼기 | `config/config.json` | on=정체성 문장 있음, off=같은 문서에서 그 문장만 제거 |
| `document_pair` (이 폴더 사용) | **독립적으로 저작된 두 문서** — `persona_0N_*.md`(역할 정의·법령 조문 인용 포함) vs 이 폴더의 `task_0N_no_persona_*.md`(명령문·간략 법령 근거) | `config/config_document_pair.json` | on=`persona_0N_*.md` 문서 사용, off=이 폴더의 문서 사용 |

두 모드는 `step4/prompt_builder.py`의 서로 다른 함수가 처리합니다:
`load_stage_prompt`(identity_marker, `step4/ablation.py`의 마커 diff 검증 포함) vs
`load_document_pair_prompt`(document_pair, 두 문서를 독립적인 것으로 보고 그대로 사용).
`persona_0N_*.md` 원본은 어느 모드에서도 수정되지 않습니다.

## 실행 방법

```bash
# identity_marker 모드가 끝난 뒤 실행하세요 (step4는 GPU 동시 사용을 막는 단일 실행 락이 있음)
./step_4_process/.venv/bin/python -m step4 --config config/config_document_pair.json --dry-run
./step_4_process/.venv/bin/python -m step4 --config config/config_document_pair.json
```

결과는 `outputs/step4_persona_vs_no_persona/`에 저장됩니다(`config/config.json` 실행 결과인
`outputs/step4_persona_ablation/`과 분리).

## 실제로 얼마나 다른 비교가 되는가 (실측 확인)

`step4/prompt_builder._extract_section`으로 양쪽 문서의 "실행용 System Prompt" 절만
직접 비교해봤습니다(정체성 문장은 제외하고 비교):

| 단계 | persona_0N.md vs task_0N_no_persona_*.md | 의미 |
| --- | --- | --- |
| persona_01 / task_01 | **완전히 동일** (정체성 문장만 다름) | 이 단계는 document_pair로 돌려도 identity_marker와 사실상 같은 결과가 나옵니다 |
| persona_02 / task_02 | **다름** (예: "페르소나 1(KMST-P01)" ↔ "NOPERSONA-01" 등 지시문 자체가 다르게 서술됨) | 실질적으로 다른 비교가 됩니다 |
| persona_03 / task_03 | **다름** (문장 구조·서술 방식이 전반적으로 다름) | 실질적으로 다른 비교가 됩니다 |

즉 지금 상태로 `document_pair` 모드를 돌리면 persona_02·persona_03 단계에서는 유의미한
새 비교가 나오지만, persona_01 단계는 `identity_marker` 모드와 크게 다르지 않은 결과가
나올 가능성이 큽니다. (`load_document_pair_prompt`가 두 절이 정체성 문장 외에 완전히
같으면 실행 시점에 경고 로그를 남깁니다.)

## 참고: STEP4 cluster 모드에서는 신경 쓰지 않아도 되는 것들

과거(비-cluster 단일사건 설계 시절) README에는 두 가지 "알려진 결함"이 적혀 있었지만,
지금의 STEP4 cluster 모드에서는 둘 다 실제로 영향이 없습니다:

- **User Prompt Template 필드 차이**: `step4/prompt_builder.build_cluster_user_prompt()`가
  군집 입력을 코드로 직접 조립하고 `[PREVIOUS_PERSONA_OUTPUT]`/`[RETRIEVED_LEGAL_CONTEXT]`
  등 모든 필드를 항상 채웁니다 — 두 문서의 "실행용 User Prompt Template" 절(13/15절)은
  애초에 런타임에 쓰이지 않습니다(persona_0N.md든 이 폴더의 문서든 동일).
- **법령 검색 컨텍스트**: `[RETRIEVED_LEGAL_CONTEXT]`는 `step4/legal_retrieval.py`가 군집
  키워드로 검색해서 on/off 양쪽에 동일한 쿼리로 한 번만 주입합니다 — 문서 본문에 적힌
  법령 조문 목록(1~11절)과 무관합니다.

즉 이 두 문서 세트의 실질적 차이는 오직 "실행용 System Prompt" 절(위 표)에서만 결정됩니다.
