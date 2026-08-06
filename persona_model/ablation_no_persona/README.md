# 페르소나 도입 vs 미도입 비교실험(Ablation) 설계

## 목적
`persona_01/02/03_*.md`(페르소나 버전)와 이 디렉터리의 `task_01/02/03_no_persona_*.md`(비페르소나 버전)를 같은 재결서 표본에 돌려, "당신은 ~이다"류 페르소나 정체성 부여가 출력 품질(GoldSet 라벨 정확도, 스키마 준수율, 근거 추적 가능성 등)에 실제로 영향을 주는지 정량비교하기 위한 대조군이다.

## 통제한 변수 (두 버전이 동일한 것)
- 법령 기반 10개 문서 및 검색 컨텍스트(`[SOURCE chunk_id]`)
- 수행 지침·판단기준·금지사항의 실질 내용
- JSON 출력 스키마 (`persona_0N_output_schema.json`을 그대로 재사용)
- User Prompt Template의 필드 구성

## 조작한 변수 (유일한 차이)
- 페르소나 버전: `## 2. 역할 정의`에서 "이 페르소나는 ~한다"처럼 정체성을 부여하고, System Prompt도 "당신은 KMST-P0N이다"로 시작한다.
- 비페르소나 버전: 동일 내용을 "다음 규칙에 따라 ~하라"는 명령문으로만 서술하고, System Prompt에 정체성 문장이 전혀 없다.

## 알려진 주의사항 (실험 전 반드시 확인)
1. **페르소나 버전 쪽 결함**: 2026-08-06 16시대에 재생성된 `persona_01_fact_evidence_analyst.md`, `persona_02_causation_legal_validator.md`의 "실제 실행용 System Prompt" 섹션이 페르소나 고유 프롬프트가 아니라 마스터 프롬프트의 "시스템 설계자" 문구를 그대로 복사한 상태다. 이 상태로 비교하면 페르소나 버전이 원래 의도와 다른(사실상 고장난) 프롬프트로 평가되어 비교가 무의미해진다. 비교 실험 전에 페르소나 버전의 System Prompt를 `render_fallback_persona()` 형태("당신은 KMST-P0N, {korean_name}이다...")로 반드시 교정해야 한다.
2. **입력 필드 불일치**: 현재 `persona_02_causation_legal_validator.md`의 User Prompt Template에는 `[PREVIOUS_PERSONA_OUTPUT]`, `[RETRIEVED_LEGAL_CONTEXT]` 필드가 빠져 있다. 이 문서(`task_02_no_persona_causation_legal.md`)는 그 필드를 포함한 상태로 작성했으므로, 지금 그대로 비교하면 입력 정보량 자체가 달라져 페르소나 효과가 아니라 입력량 차이를 측정하게 된다. 페르소나 버전도 같은 필드를 채운 뒤 비교할 것.
3. 위 두 문제를 해결하기 전까지는 이 디렉터리를 "설계 초안"으로만 사용하고 실측 비교실험에 투입하지 말 것.

## 권장 비교 절차
1. 동일한 재결서 표본(N건, 가능하면 GoldSet 후보가 이미 있는 사건 위주)을 고정한다.
2. 페르소나 버전 3단계(P01→P02→P03)와 비페르소나 버전 3단계(NOPERSONA-01→02→03)를 각각 같은 temperature·모델로 실행한다.
3. 비교 지표 예시:
   - 스키마 유효성(JSON parse 성공률, 필수 필드 채움률)
   - `source_excerpt`/`source_location`이 실제 재결서 원문에 존재하는 비율(환각 여부)
   - `cause_category`/`label_code` 등 분류 라벨의 사람 검수 대비 일치율(정확도, F1)
   - 반복 실행 시 라벨 안정성(동일 사건 재실행 시 라벨 변화 빈도) — 이미 저장소에 있는 `step4_stability_*.svg` / `gui_web/report_step4_matrix.php`의 안정성 비교 방식(A/B/C 변형 비교)을 그대로 원용 가능
   - `analytic_contribution_score`의 사람 평가자 점수 대비 상관관계
4. 페르소나 버전과 비페르소나 버전 간 위 지표 차이를 유의성 검정(예: paired test)으로 확인한다.

## 근거 표기 범례
각 지침 항목 끝의 표기는 다음을 의미한다.
- `(근거: 「문서명」 제N조 ...)` — 아래 10개 법령·행정규칙 원문에서 실제로 확인한 조문. `persona_model/data/`에서 grep으로 재검증 가능.
- `(출처: 연구설계 지정 — 법령 근거 아님)` — 법령이 아니라 `persona_pipeline_master_prompt.md`(연구 설계자)가 직접 지정한 경계나 스키마 요구사항.
