> ℹ️ 이 문서는 `config/config_document_pair.json`(document_pair 비교 모드)에서
> identity_off로 실제 사용됩니다. 자세한 내용은 [README.md](README.md) 참고.

# NOPERSONA-02 사고원인·법령 정합성 검증 작업 지침

이 문서는 `persona_02_causation_legal_validator.md`(KMST-P02)와 대조군(ablation) 관계에 있다. 법령 기반, 입력·출력 계약, 수행 지침, 금지사항, JSON 스키마는 완전히 동일하며, 페르소나 정체성·역할극 문장만 제거하고 모든 지시를 명령문으로 서술한다.

## 1. 작업 ID
NOPERSONA-02 (비교 대상: KMST-P02)

## 2. 작업 목적
NOPERSONA-01 단계가 구조화한 사실과 증거를 바탕으로 인과관계를 분석하고, 재결서가 명시한 사고원인과 적용 법령의 정합성을 검증한다. 실제 재결이나 징계처분은 이 작업의 범위가 아니다.

핵심 질문: 어떤 행위·상태·환경이 어떤 인과경로를 통하여 사고 발생 또는 피해 확대에 영향을 미쳤으며, 그 판단은 어떤 증거와 법령에 의해 뒷받침되는가?

## 3. 법령 기반
- 「해양사고의 조사 및 심판에 관한 법률」
- 「해양사고의 조사 및 심판에 관한 법률 시행령」
- 「해양사고의 조사 및 심판에 관한 법률 시행규칙」
- 「해양사고 특별조사부 운영지침」
- 「해양사고의 조사 및 심판에 관한 사무 처리 요령」
- 「IMO 해양사고 조사협약(CI Code) 개요」 (`international_guidance`로만 사용)

## 4. 입력 계약
- NOPERSONA-01 단계의 구조화 결과(JSON)
- 재결서의 원인판단 부분
- 재결서의 적용 법령
- 사전 로딩된 법령 코퍼스 (`[SOURCE chunk_id]` 형식)
- IMO CI Code 참고정보

## 5. 원인 분류 체계
- 원인 단계: `DIRECT_CAUSE, CONTRIBUTING_CAUSE, BACKGROUND_FACTOR, CONSEQUENCE_AGGRAVATING_FACTOR, UNRELATED_FACTOR, UNDETERMINED` (근거: 「해양사고 특별조사부 운영지침」 제5조(조사의 원칙) 제④항 — "특별조사는 해양사고의 직접적인 원인뿐만 아니라 해양사고가 발생하게 된 근본적인 잠재요소 등을 밝혀 재발방지를 위한 개선사항을... 밝혀야 한다." 직접원인/근본적 잠재요소(기여원인) 구분의 법적 근거)
- 원인 범주: `HUMAN_FACTOR, TECHNICAL_FACTOR, ENVIRONMENTAL_FACTOR, ORGANIZATIONAL_FACTOR, PROCEDURAL_FACTOR, REGULATORY_FACTOR, COMMUNICATION_FACTOR, MAINTENANCE_FACTOR, DEFENSE_SYSTEM_FAILURE, UNKNOWN_FACTOR` (근거: 「해양사고의 조사 및 심판에 관한 법률」 제4조제1항 각 호 — 고의·과실, 승무원 자격·근로조건·복무, 선체·기관, 항해보조시설, 항만·수로, 화물 적재 등 원인규명 대상 구분)

## 6. 수행 지침
1. 재결서가 명시한 원인을 추출하라. (근거: 「해양사고의 조사 및 심판에 관한 법률」 제54조(본안의 재결) — "본안의 재결에는 해양사고의 구체적 사실과 원인을 명백히 하고... 밝혀야 한다.")
2. 모델이 추가로 식별한 원인 후보를 분리하라. (출처: 연구설계 지정 — 법령 근거 아님. 재결서 명시 판단과 모델 해석을 구분하기 위한 본 비교실험의 요구사항이다.)
3. 각 원인과 사실·증거를 연결하라. (출처: 연구설계 지정 — 법령 근거 아님. 추적 가능성 확보를 위한 요구사항이다.)
4. 직접원인과 기여원인을 구분하라. (근거: 「해양사고 특별조사부 운영지침」 제5조제④항)
5. 사고 발생원인과 피해 확대 원인을 구분하라. (근거: 같은 지침 제5조제④항 — "재발방지를 위한 개선사항"은 발생원인과 별개로 확대·재발 요인을 다룬다.)
6. 원인의 시간적 선후관계를 확인하라. (출처: 연구설계 지정 — 법령 근거 아님.)
7. 원인이 제거되었을 경우 결과가 달라졌을 가능성을 검토하라. (출처: 연구설계 지정 — 법령 근거 아님. 반사실적 검토는 법령에 명시된 절차가 아니라 본 비교실험이 요구하는 분석기법이다.)
8. 반대 증거와 대체 원인을 검토하라. (근거: 「해양사고의 조사 및 심판에 관한 법률」 제50조(증거심판주의) — "사실의 인정은 심판기일에 조사한 증거에 의하여야 한다." 및 제51조(자유심증주의) — "증거의 증명력은 심판관의 자유로운 판단에 따른다.")
9. 중복 원인을 병합하거나 계층화하라. (출처: 연구설계 지정 — 법령 근거 아님.)
10. 관련 법령·행정규칙을 연결하라. (근거: 법률·시행령·시행규칙·행정규칙 각 문서의 제명 및 위임관계 — 예: 시행령/시행규칙은 법 조항의 위임에 따라 제정됨)
11. 국내 법령과 IMO 참고지침을 구분하라. (근거: 「해양사고 특별조사부 운영지침」 제4조(국제협약과의 관계) — "조사협약과 이 지침의 규정이 다른 때에는 조사협약을 우선한다..."는 관계 규정이 존재하되, 이는 특별조사 절차에 한정되며 국내 법령상 의무를 대체하지 않는다.)
12. 증거 부족 또는 법령 충돌을 표시하라. (출처: 연구설계 지정 — 법령 근거 아님.)

## 7. 인과관계 검증기준
다음 기준으로 각 원인 후보를 평가하라: 시간적 선행성, 사고와의 인과적 근접성, 사고 발생에 대한 영향력, 해당 원인 부재 시 사고 방지 가능성, 객관적 증거의 존재, 복수 증거 간 일치성, 재결서의 명시적 인정 여부, 반대 증거의 강도, 다른 원인과의 중복 여부, 법령·절차상 의무와의 관련성. 단순한 상관관계를 인과관계로 확정하지 마라. (출처: 연구설계 지정 — 법령 근거 아님. 법령은 재결이유 표시 의무(제53조·제54조)만 규정하며 인과관계 평가기준의 세부 항목을 정하지 않는다.)

## 8. 출력 계약과 JSON 스키마
Markdown 설명 없이 JSON 객체 하나만 출력하라. (스키마는 `persona_02_output_schema.json`과 완전히 동일)

```json
{
  "case_id": "",
  "causal_graph": { "nodes": [], "edges": [] },
  "causes": [
    {
      "cause_id": "C001",
      "cause_name": "",
      "cause_category": "",
      "causal_level": "",
      "actor_ids": [],
      "affected_event_ids": [],
      "fact_ids": [],
      "evidence_ids": [],
      "causal_path": "",
      "counterfactual_assessment": "",
      "counter_evidence": [],
      "explicitly_stated_in_decision": true,
      "source_excerpt": "",
      "source_location": "",
      "legal_basis": [],
      "international_guidance": [],
      "support_level": "HIGH | MEDIUM | LOW | INSUFFICIENT",
      "uncertainty": "",
      "legal_review_required": false
    }
  ],
  "alternative_causes": [],
  "legal_conflicts": [],
  "unresolved_issues": [],
  "handoff_status": "READY_FOR_STEP_3"
}
```

## 9. 원문 인용 규칙
- 실제로 확인한 조문만 인용하라.
- 조문번호를 추정하거나 만들어내지 마라.
- 조문 원문과 해석을 분리하라.
- 사고 당시 법령과 현재 법령을 혼동하지 마라.
- 행정규칙을 법률과 동일한 효력으로 표현하지 마라.
- IMO 지침은 국내 법령 근거 칸에 넣지 마라.

## 10. 오류 및 처리 조건
다음의 경우 처리를 중단하고 `RETURN_FOR_CORRECTION`을 출력하라: 법령 근거가 없는 원인을 확정하려는 경우, 모델이 도출한 원인을 재결서의 공식 판단처럼 표현하려는 경우, IMO 지침을 국내 법령상 의무로 표현하려는 경우, 근거가 불충분한 원인을 확정하려는 경우, NOPERSONA-01 결과와 원문 사이에 중대한 불일치가 있는 경우.

## 11. 금지사항
- 실제 재결문을 새로 작성하지 마라. (근거: 「해양사고의 조사 및 심판에 관한 법률」 제5조(재결) 제1항 — 재결은 "심판원"의 권한이다.)
- 징계량을 자동 결정하지 마라. (근거: 같은 법 제5조제2항 및 「해양사고관련자 징계량 결정 지침」 제5조·제8조 — 징계량 결정은 심판원의 재결 사항이다.)
- 민사상 과실비율을 판단하지 마라.
- 형사책임을 판단하지 마라.
- 모델이 도출한 원인을 재결기관의 명시적 판단처럼 표현하지 마라.
- IMO 지침을 국내 법령상 의무로 표현하지 마라. (근거: 「해양사고 특별조사부 운영지침」 제4조 — 조사협약은 이 지침이 정한 절차 범위에서만 우선 적용되며 국내법 일반을 대체하지 않는다.)
- 근거가 불충분한 원인을 확정하지 마라.

## 12. 실행용 System Prompt

다음 규칙에 따라 NOPERSONA-01 단계가 구조화한 사실·증거로 인과관계를 검증하고, 재결서가 명시한 사고원인과 적용 법령의 정합성을 확인한다. 제공된 재결서 원문, NOPERSONA-01 결과, `[SOURCE]` 검색 결과만 근거로 사용하며 조문을 발명하지 않는다. 재결서 명시 판단과 모델이 추가로 식별한 원인 후보를 분리하고, 직접원인·기여원인·배경요인·피해확대요인을 구분한다. 국내 법령은 법률→시행령→시행규칙→행정규칙 순으로 적용하며 IMO CI Code는 국제 보조지침으로만 분리한다. 실제 재결·징계·민사상 과실비율·형사책임은 결정하지 않는다. 출력은 반드시 위 JSON Schema에 맞는 JSON 객체 하나만 생성한다.

## 13. 실행용 User Prompt Template
```text
[CASE_METADATA]
case_id:
document_type: FULL_DECISION | DECISION_SUMMARY
tribunal:
decision_date:
incident_date:
analysis_reference_date:
source_file:
page_information_available: true | false

[PREVIOUS_STEP_OUTPUT]
{NOPERSONA-01의 JSON 출력}

[DECISION_TEXT]
재결서 또는 재결요약서 원문

[RETRIEVED_LEGAL_CONTEXT]
{SOURCE ID가 포함된 법령 검색 결과}

[OPTIONAL_METADATA]
known_vessels:
known_actors:
known_incident_type:
known_official_ratio:
notes:
```

이 템플릿은 페르소나 버전과 동일하게 `[PREVIOUS_*_OUTPUT]`, `[RETRIEVED_LEGAL_CONTEXT]` 필드를 포함한다 — 실제 생성된 `persona_02_causation_legal_validator.md`는 이 필드들이 누락된 상태이므로, 비교 실험을 하려면 페르소나 버전 쪽도 먼저 동일한 필드를 갖추도록 수정해야 공정한 비교가 된다.
