# KMST 3-Type Persona Model

본 3-Type Persona는 「해양사고의 조사 및 심판에 관한 법률」, 같은 법 시행령·시행규칙 및 중앙해양안전심판원 관련 행정규칙의 조사·심판·재결 절차와 업무처리 지침을 참조하여 설계하였다. IMO 해양사고 조사협약(CI Code) 개요는 국제적 사고조사 원칙을 이해하기 위한 보조 지침으로만 활용하였다.

## 목적

Qwen3-14B와 법령 검색 컨텍스트를 이용해 재결서의 사실·증거 구조화, 사고원인·법령 검증, 연구용 원인기여도·레이블링을 담당하는 세 페르소나를 생성한다.

## 생성 정보

- 도구 버전: 1.2.0
- 실제 모델: Qwen3-14B
- 생성 엔진: transformers
- 생성시각(UTC): 2026-08-06T07:16:54.036230+00:00
- 기존 파일 백업: 없음

`prompt.txt`에는 Qwen2.5-14B와 Qwen3-14B 표기가 혼재하지만 실제 로딩 모델은 사용자 지정 경로의 Qwen3-14B다.

## 사전학습 용어

본 시스템의 사전학습은 모델 가중치 재학습이 아니라 법령 10개와 IMO 참고문서의 전체 로딩, 조문 단위 색인, 검색 기반 컨텍스트 주입을 의미한다.

## 실행 순서

`KMST-P01 → KMST-P02 → KMST-P03 → 인간 전문가 검토`

## 법적·연구적 경계

- `official_ratio`: 재결서에 명시된 공식 원인제공비율만 원문 그대로 추출
- `analytic_contribution_score`: 법적 효력이 없는 연구·학습용 분석지표
- 자동 생성 레이블: `GOLDSET_CANDIDATE`
- 전문가 검증 완료 레이블: `EXPERT_VALIDATED_GOLDSET`
- OBD 연계, Prediction Model, 실시간 알람은 현 단계에서 제외
- 본 시스템은 법률자문·재결·징계 자동화 시스템이 아님

## 로딩한 코퍼스

- `행정규칙_해양안전심판원 정보공개규정.md` (ADMINISTRATIVE_RULE)
- `행정규칙_해양사고관련자 징계량 결정 지침.md` (ADMINISTRATIVE_RULE)
- `행정규칙_해양안전심판원 심판관,조사관 등 연수교육 운영 지침.md` (ADMINISTRATIVE_RULE)
- `행정규칙_해양사고의 조사 및 심판에 관한 법률의 적용대상이 아닌 수상레저기구.md` (ADMINISTRATIVE_RULE)
- `행정규칙_해양사고의 조사 및 심판에 관한 법률에 따른 과태료의 가중처분에 관한 세부 지침.md` (ADMINISTRATIVE_RULE)
- `행정규칙_해양사고의 조사 및 심판에 관한 사무 처리 요령.md` (ADMINISTRATIVE_RULE)
- `행정규칙_해양사고 특별조사부 운영지침.md` (ADMINISTRATIVE_RULE)
- `법령_해양사고의 조사 및 심판에 관한 법률 시행규칙.md` (ENFORCEMENT_RULE)
- `법령_해양사고의 조사 및 심판에 관한 법률 시행령.md` (ENFORCEMENT_DECREE)
- `법령_해양사고의 조사 및 심판에 관한 법률.md` (LAW)
- `국제기준_IMO 해양사고 조사협약(CI Code) 개요.md` (INTERNATIONAL_GUIDANCE)
