# Persona Generation Validation Report

```json
{
  "generation_status": "PASS",
  "generated_at": "2026-08-06T07:57:54.847966+00:00",
  "tool_version": "1.2.0",
  "actual_model": "Qwen3-14B",
  "prompt_model_name_conflict_detected": false,
  "corpus_validation": "PASS",
  "persona_validation": "PASS",
  "schema_validation": "PASS",
  "legal_consistency_validation": "HUMAN_REVIEW_REQUIRED",
  "failed_checks": [],
  "required_corrections": [],
  "output_directory": "/home/jiwoo/Desktop/workspace/SBERT/llm_based_root_cause_classification_system/persona_model",
  "backup_directory": null,
  "persona_generation": {
    "KMST-P01": {
      "method": "QWEN3_14B_PLUS_IMMUTABLE_POLICY_APPENDIX",
      "retrieved_source_chunks": [
        "DOC-01-C0006",
        "DOC-02-C0001",
        "DOC-03-C0005",
        "DOC-04-C0001",
        "DOC-05-C0001",
        "DOC-06-C0032",
        "DOC-07-C0013",
        "DOC-08-C0006",
        "DOC-09-C0080",
        "DOC-10-C0033",
        "DOC-11-C0001",
        "DOC-07-C0016",
        "DOC-07-C0009",
        "DOC-07-C0003",
        "DOC-07-C0019",
        "DOC-06-C0204",
        "DOC-06-C0119",
        "DOC-06-C0200"
      ],
      "retrieved_source_files": [
        "국제기준_IMO 해양사고 조사협약(CI Code) 개요.md",
        "법령_해양사고의 조사 및 심판에 관한 법률 시행규칙.md",
        "법령_해양사고의 조사 및 심판에 관한 법률 시행령.md",
        "법령_해양사고의 조사 및 심판에 관한 법률.md",
        "행정규칙_해양사고 특별조사부 운영지침.md",
        "행정규칙_해양사고관련자 징계량 결정 지침.md",
        "행정규칙_해양사고의 조사 및 심판에 관한 법률에 따른 과태료의 가중처분에 관한 세부 지침.md",
        "행정규칙_해양사고의 조사 및 심판에 관한 법률의 적용대상이 아닌 수상레저기구.md",
        "행정규칙_해양사고의 조사 및 심판에 관한 사무 처리 요령.md",
        "행정규칙_해양안전심판원 심판관,조사관 등 연수교육 운영 지침.md",
        "행정규칙_해양안전심판원 정보공개규정.md"
      ],
      "validation_errors_before_fallback": []
    },
    "KMST-P02": {
      "method": "QWEN3_14B_PLUS_IMMUTABLE_POLICY_APPENDIX",
      "retrieved_source_chunks": [
        "DOC-01-C0001",
        "DOC-02-C0001",
        "DOC-03-C0004",
        "DOC-04-C0003",
        "DOC-05-C0003",
        "DOC-06-C0126",
        "DOC-07-C0031",
        "DOC-08-C0006",
        "DOC-09-C0016",
        "DOC-10-C0027",
        "DOC-11-C0001",
        "DOC-02-C0010",
        "DOC-09-C0017",
        "DOC-09-C0020",
        "DOC-06-C0107",
        "DOC-06-C0056",
        "DOC-09-C0022",
        "DOC-06-C0168"
      ],
      "retrieved_source_files": [
        "국제기준_IMO 해양사고 조사협약(CI Code) 개요.md",
        "법령_해양사고의 조사 및 심판에 관한 법률 시행규칙.md",
        "법령_해양사고의 조사 및 심판에 관한 법률 시행령.md",
        "법령_해양사고의 조사 및 심판에 관한 법률.md",
        "행정규칙_해양사고 특별조사부 운영지침.md",
        "행정규칙_해양사고관련자 징계량 결정 지침.md",
        "행정규칙_해양사고의 조사 및 심판에 관한 법률에 따른 과태료의 가중처분에 관한 세부 지침.md",
        "행정규칙_해양사고의 조사 및 심판에 관한 법률의 적용대상이 아닌 수상레저기구.md",
        "행정규칙_해양사고의 조사 및 심판에 관한 사무 처리 요령.md",
        "행정규칙_해양안전심판원 심판관,조사관 등 연수교육 운영 지침.md",
        "행정규칙_해양안전심판원 정보공개규정.md"
      ],
      "validation_errors_before_fallback": []
    },
    "KMST-P03": {
      "method": "DETERMINISTIC_FALLBACK_AFTER_LLM_VALIDATION_FAILURE",
      "retrieved_source_chunks": [
        "DOC-01-C0001",
        "DOC-02-C0008",
        "DOC-03-C0001",
        "DOC-04-C0001",
        "DOC-05-C0002",
        "DOC-06-C0126",
        "DOC-07-C0031",
        "DOC-08-C0019",
        "DOC-09-C0024",
        "DOC-10-C0054",
        "DOC-11-C0001",
        "DOC-09-C0010",
        "DOC-09-C0073",
        "DOC-02-C0004",
        "DOC-06-C0127",
        "DOC-06-C0132",
        "DOC-07-C0033",
        "DOC-09-C0078"
      ],
      "retrieved_source_files": [
        "국제기준_IMO 해양사고 조사협약(CI Code) 개요.md",
        "법령_해양사고의 조사 및 심판에 관한 법률 시행규칙.md",
        "법령_해양사고의 조사 및 심판에 관한 법률 시행령.md",
        "법령_해양사고의 조사 및 심판에 관한 법률.md",
        "행정규칙_해양사고 특별조사부 운영지침.md",
        "행정규칙_해양사고관련자 징계량 결정 지침.md",
        "행정규칙_해양사고의 조사 및 심판에 관한 법률에 따른 과태료의 가중처분에 관한 세부 지침.md",
        "행정규칙_해양사고의 조사 및 심판에 관한 법률의 적용대상이 아닌 수상레저기구.md",
        "행정규칙_해양사고의 조사 및 심판에 관한 사무 처리 요령.md",
        "행정규칙_해양안전심판원 심판관,조사관 등 연수교육 운영 지침.md",
        "행정규칙_해양안전심판원 정보공개규정.md"
      ],
      "validation_errors_before_fallback": [
        "OBD가 제외범위임을 명시하지 않음"
      ]
    }
  },
  "limitations": [
    "자동 생성 레이블은 GoldSet Candidate이며 전문가 검증 전 확정 GoldSet이 아님",
    "검색 기반 법령 주입이며 모델 가중치의 continued pre-training이 아님",
    "OBD 연계와 Prediction Model 구현은 범위에서 제외"
  ]
}
```
