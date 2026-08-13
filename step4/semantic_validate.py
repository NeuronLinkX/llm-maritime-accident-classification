"""JSON Schema 이후의 의미 검증기.

구조는 맞지만 의미적으로 잘못된 출력(빈 label_code, taxonomy 밖 자유서술, 존재하지 않는
source id 인용 등)을 별도 defect로 기록한다. 이 계층은 출력을 수정하지 않고 결함만 보고한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SemanticValidationResult:
    valid: bool
    error_codes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)


def _append_error(
    error_codes: list[str],
    errors: list[str],
    code: str,
    message: str,
) -> None:
    error_codes.append(code)
    errors.append(message)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_persona_02(parsed: dict, allowed_source_ids: set[str]) -> SemanticValidationResult:
    error_codes: list[str] = []
    errors: list[str] = []
    causes = parsed.get("causes") or []

    for idx, cause in enumerate(causes):
        if not isinstance(cause, dict):
            continue
        cause_name = cause.get("cause_name")
        if not _is_non_empty_string(cause_name):
            _append_error(
                error_codes,
                errors,
                "P02_EMPTY_CAUSE_NAME",
                f"causes[{idx}].cause_name이 비어 있습니다.",
            )

        support_level = cause.get("support_level")
        fact_ids = cause.get("fact_ids") or []
        evidence_ids = cause.get("evidence_ids") or []
        for field_name, ids in (("fact_ids", fact_ids), ("evidence_ids", evidence_ids)):
            invalid = [v for v in ids if v not in allowed_source_ids]
            if invalid:
                _append_error(
                    error_codes,
                    errors,
                    "P02_HALLUCINATED_SOURCE_ID",
                    f"causes[{idx}].{field_name}에 존재하지 않는 source id가 있습니다: {invalid}",
                )
        if support_level in {"HIGH", "MEDIUM"} and not (fact_ids or evidence_ids):
            _append_error(
                error_codes,
                errors,
                "P02_UNSUPPORTED_HIGH_CONFIDENCE",
                f"causes[{idx}]는 support_level={support_level}인데 fact_ids/evidence_ids가 비어 있습니다.",
            )

    counters = {
        "semantic_error_count": len(errors),
        "p02_cause_count": len(causes),
    }
    return SemanticValidationResult(valid=not errors, error_codes=error_codes, errors=errors, counters=counters)


def _validate_persona_03(parsed: dict, known_label_codes: set[str]) -> SemanticValidationResult:
    error_codes: list[str] = []
    errors: list[str] = []
    cause_labels = parsed.get("cause_labels") or []
    empty_count = 0
    invalid_count = 0

    for idx, item in enumerate(cause_labels):
        if not isinstance(item, dict):
            continue
        label_code = item.get("label_code")
        if not _is_non_empty_string(label_code):
            empty_count += 1
            _append_error(
                error_codes,
                errors,
                "P03_EMPTY_LABEL_CODE",
                f"cause_labels[{idx}].label_code가 비어 있습니다.",
            )
            continue
        if label_code not in known_label_codes:
            invalid_count += 1
            _append_error(
                error_codes,
                errors,
                "P03_UNKNOWN_LABEL_CODE",
                f"cause_labels[{idx}].label_code='{label_code}'는 KMST taxonomy에 없습니다.",
            )

    counters = {
        "semantic_error_count": len(errors),
        "n_taxonomy_total_items": len(cause_labels),
        "n_taxonomy_ok_items": len(cause_labels) - empty_count - invalid_count,
        "n_empty_label_code_items": empty_count,
        "n_unknown_label_code_items": invalid_count,
    }
    return SemanticValidationResult(valid=not errors, error_codes=error_codes, errors=errors, counters=counters)


def validate_stage_output(
    *,
    stage: str,
    parsed: dict | None,
    allowed_source_ids: set[str] | None = None,
    known_label_codes: set[str] | None = None,
) -> SemanticValidationResult:
    if not parsed:
        return SemanticValidationResult(valid=False, error_codes=["NO_PARSED_OBJECT"], errors=["parsed가 없습니다."])
    if stage == "persona_02":
        return _validate_persona_02(parsed, allowed_source_ids or set())
    if stage == "persona_03":
        return _validate_persona_03(parsed, known_label_codes or set())
    return SemanticValidationResult(valid=True, counters={"semantic_error_count": 0})
