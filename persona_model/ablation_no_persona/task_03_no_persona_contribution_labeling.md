> ℹ️ 이 문서는 `config/config_document_pair.json`(document_pair 비교 모드)에서
> identity_off로 실제 사용됩니다. 자세한 내용은 [README.md](README.md) 참고.

# NOPERSONA-03 원인기여도·레이블링 품질관리 작업 지침

이 문서는 `persona_03_contribution_labeling_qa.md`(KMST-P03)와 대조군(ablation) 관계에 있다. 법령 기반, 입력·출력 계약, 수행 지침, 금지사항, JSON 스키마는 완전히 동일하며, 페르소나 정체성·역할극 문장만 제거하고 모든 지시를 명령문으로 서술한다.

## 1. 작업 ID
NOPERSONA-03 (비교 대상: KMST-P03)

## 2. 작업 목적
NOPERSONA-02 단계가 검증한 사고원인을 연구용 정량지표와 예측모델 학습용 레이블로 변환하고, 공식 비율과 모델 추정치를 분리하며, 결과의 품질을 검증한다. 이 작업은 연구·시스템 설계 목적이며 법령상 공식 절차가 아니다.

핵심 질문: 검증된 각 원인은 사고 발생 또는 피해 확대에 어느 정도 기여했으며, 이를 재현 가능한 연구용 레이블로 어떻게 표현할 수 있는가?

## 3. 법령 기반
- 「해양사고의 조사 및 심판에 관한 법률」
- 「해양사고관련자 징계량 결정 지침」
- 「해양사고의 조사 및 심판에 관한 법률 시행령」

## 4. 입력 계약
- NOPERSONA-01 단계의 사실·증거 구조
- NOPERSONA-02 단계의 원인·법령 검증 결과
- 재결서의 공식 원인제공비율
- 레이블 분류체계 (`cause_label_taxonomy.json`)
- 정량화 기준, 품질검증 규칙

## 5. 공식 원인제공비율 추출 규칙
명시된 공식 원인제공비율만 `official_ratio`로 원문 그대로 추출하라. 공식 비율이 없으면 추정하지 말고 `present: false`로 표시하라. (근거: 「해양사고의 조사 및 심판에 관한 법률」 제4조(해양사고의 원인규명 등) 제2항 — "심판원은 제1항에 따른 해양사고의 원인을 밝힐 때 해양사고의 발생에 2명 이상이 관련되어 있는 경우에는 각 관련자에 대하여 원인의 제공 정도를 밝힐 수 있다." 및 「해양사고의 조사 및 심판에 관한 사무 처리 요령」 제116조(과실정도에 대한 재결) — "심판원은 법 제4조제2항에 따라 2명 이상이 관련되어 있는 해양사고 중 각 관련자에 대한 원인의 제공 정도를 밝힐 수 있는 경우는 다음 각 호에 따른다.")

## 6. 분석용 기여도 산정 (`analytic_contribution_score`)
(출처: 연구설계 지정 — 법령 근거 아님. 재결서는 원인제공비율을 "밝힐 수 있다"고만 규정할 뿐 산정식·가중치를 정하지 않으며, 아래 방식은 본 비교실험이 도입한 연구용 지표다.)

각 원인에 대해 다음 항목을 0~4점으로 평가하라.

| 항목 | 설명 | 가중치 |
|---|---|---|
| 증거 강도 | 객관적·복수 증거가 원인을 지지하는 정도 | 0.30 |
| 인과적 근접성 | 해당 원인과 사고 결과의 직접성 | 0.25 |
| 반사실적 방지 가능성 | 해당 원인이 없었을 때 사고가 방지될 가능성 | 0.20 |
| 재결서 명시성 | 재결서가 해당 원인을 명시적으로 인정한 정도 | 0.15 |
| 자료 간 일치성 | 진술·기록·법령 검토 결과의 일치 정도 | 0.10 |

```
raw_score = (evidence_strength*0.30 + causal_proximity*0.25 + counterfactual_preventability*0.20
             + decision_explicitness*0.15 + cross_source_consistency*0.10) / 4
```
결과 범위: `0.00 ≤ analytic_contribution_score ≤ 1.00`. 사건 내 원인점수의 합을 1 또는 100으로 강제하지 마라. 상대적 비중이 필요하면 `normalized_case_share`를 별도로 생성하되, 이를 공식 원인제공비율이나 법적 과실비율로 표현하지 마라.

## 7. 점수 산정 제한
다음 경우에는 점수를 산정하지 말고 `{"analytic_contribution_score": null, "score_status": "NOT_SCORABLE", "reason": ""}`를 출력하라: NOPERSONA-02의 근거 수준이 `INSUFFICIENT`인 경우, 원문 위치가 없는 경우, 원인과 증거가 연결되지 않은 경우, 재결서 내용이 불완전한 경우, 서로 모순되는 증거를 해결할 수 없는 경우, 원인 범주가 결정되지 않은 경우. (출처: 연구설계 지정 — 법령 근거 아님)

## 8. 신뢰도 분류
`HIGH`(객관적 증거 복수 + 재결서 명시 인정), `MEDIUM`(근거는 있으나 진술 충돌·정보 누락), `LOW`(제한된 진술·간접증거 의존), `NOT_SCORABLE`(정량화 근거 부족). (출처: 연구설계 지정 — 법령 근거 아님)

## 9. 레이블 부여 규칙
- 명시된 공식 원인제공비율만 `official_ratio`로 원문 그대로 추출하라. (근거: 5번 항목과 동일 — 법 제4조제2항)
- 모델 점수는 반드시 `analytic_contribution_score`라는 이름으로만 표현하고 공식 비율·민사상 과실비율·손해배상책임비율·형사책임·징계수준과 동일시하지 마라. (근거: 「해양사고관련자 징계량 결정 지침」 제5조(징계량의 결정)·제8조(징계량 결정 시 고려 사항) — 징계량은 별도 법정 절차와 기준으로 결정되며 본 작업의 점수와 무관하다.)
- 근거 강도·인과적 근접성·반사실성·재결 명시성·자료 일치성을 평가하라. (출처: 연구설계 지정 — 법령 근거 아님)
- 점수 근거, 범위, 신뢰도와 정량화 불가 사유를 기록하라. (출처: 연구설계 지정 — 법령 근거 아님)
- 전문가 검증 전 결과를 `GOLDSET_CANDIDATE`로 표시하고 데이터 누출을 점검하라. (출처: 연구설계 지정 — 법령 근거 아님. 이 명칭 체계는 본 연구가 도입한 검증 상태 표기이며 재결·GoldSet 관련 법령 규정은 존재하지 않는다.)

## 10. 데이터 누출 방지
재결서는 사고 발생 후 작성되는 문서이므로 재결서에서 추출한 정보는 정답 레이블 생성에 사용한다. 다만 향후 예측모델 입력변수로는 다음 정보를 사용하지 마라: 사고 이후 작성된 재결 내용, 최종 사고원인, 공식 원인제공비율, 사고 결과를 직접 나타내는 사후정보, 징계 또는 재결 결과, 사고 이후 수집된 조사결과, 실제 알람 시점 이후에 생성된 정보. 예측 입력은 반드시 위험 알람 기준시점 이전에 이용 가능했던 정보로 제한하라. (출처: 연구설계 지정 — 법령 근거 아님. 예측모델 설계는 이 작업의 범위 밖이며, 이 절은 향후 확장을 대비한 경계 표시일 뿐이다.)

## 11. 출력 계약과 JSON 스키마
Markdown 설명 없이 JSON 객체 하나만 출력하라. (스키마는 `persona_03_output_schema.json`과 완전히 동일)

```json
{
  "case_id": "",
  "official_ratio": { "present": false, "subjects": [], "source_excerpt": "", "source_location": "" },
  "cause_labels": [
    {
      "cause_id": "C001", "label_code": "", "label_name_ko": "", "label_name_en": "",
      "cause_category": "", "causal_level": "", "actor_ids": [],
      "official_ratio": null, "analytic_contribution_score": null, "normalized_case_share": null,
      "score_components": {
        "evidence_strength": 0, "causal_proximity": 0, "counterfactual_preventability": 0,
        "decision_explicitness": 0, "cross_source_consistency": 0
      },
      "confidence": "HIGH | MEDIUM | LOW | NOT_SCORABLE",
      "evidence_ids": [], "legal_basis": [], "source_excerpt": "", "source_location": "",
      "is_explicit_in_decision": false, "legal_liability_indicator": false,
      "human_review_required": true, "review_status": "GOLDSET_CANDIDATE"
    }
  ],
  "incident_labels": {
    "incident_type": "", "severity": "", "fatality_present": false, "injury_present": false,
    "pollution_present": false, "primary_cause_category": "", "secondary_cause_categories": [],
    "data_completeness": ""
  },
  "quality_assurance": {
    "schema_valid": false, "source_traceable": false, "official_and_analytic_separated": false,
    "legal_consistency_checked": false, "data_leakage_checked": false, "expert_review_required": true
  }
}
```

## 12. 오류 및 처리 조건
점수 산정 제한 조건(7번)에 해당하면 `NOT_SCORABLE`로 처리하고 강제로 수치화하지 마라. `official_ratio`와 `analytic_contribution_score`가 분리되지 않은 출력은 반려하라.

## 13. 금지사항
- 분석점수를 공식 원인제공비율로 표현하지 마라.
- 분석점수를 민사상 과실비율로 표현하지 마라.
- 공식 비율이 없는 사건에 official_ratio를 생성하지 마라. (근거: 법 제4조제2항 — 원인의 제공 정도는 심판원이 "밝힐 수 있다"고만 규정되어 있으며, 명시되지 않은 사건에 비율을 만들어낼 법적 근거가 없다.)
- 근거가 없는 정밀한 수치를 생성하지 마라.
- 전문가 검증 전 결과를 확정 GoldSet으로 표시하지 마라.
- OBD 특징이나 예측모델을 임의로 설계하지 마라.
- 법적 책임 또는 징계수준을 자동 산정하지 마라. (근거: 「해양사고관련자 징계량 결정 지침」 제5조·제8조 — 징계량 결정은 별도 법정 기준과 절차를 따른다.)

## 14. 실행용 System Prompt

다음 규칙에 따라 NOPERSONA-02 단계가 검증한 사고원인을 연구용 정량지표(`analytic_contribution_score`)와 예측모델 학습용 레이블로 변환한다. 공식 원인제공비율(`official_ratio`)은 재결서에 명시된 경우에만 원문 그대로 추출하고, 명시되지 않으면 추정하지 않는다. 두 값을 혼동하거나 동일시하지 않는다. 점수 산정 제한 조건에 해당하면 `NOT_SCORABLE`로 처리한다. 전문가 검증 전 모든 레이블은 `GOLDSET_CANDIDATE`로 표시한다. OBD 연계, Prediction Model 구현, 실시간 알람 생성은 이 작업의 범위가 아니다. 출력은 반드시 위 JSON Schema에 맞는 JSON 객체 하나만 생성한다.

## 15. 실행용 User Prompt Template
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
{NOPERSONA-01과 NOPERSONA-02의 JSON 출력}

[DECISION_TEXT]
재결서 또는 재결요약서 원문 (공식 원인제공비율 확인용)

[LABEL_TAXONOMY]
{cause_label_taxonomy.json}

[OPTIONAL_METADATA]
known_vessels:
known_actors:
known_incident_type:
known_official_ratio:
notes:
```
