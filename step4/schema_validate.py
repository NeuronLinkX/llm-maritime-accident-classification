"""모델 출력에서 JSON 객체 추출 + persona_0N_output_schema.json 검증."""
from __future__ import annotations

import json
from dataclasses import dataclass

import jsonschema


@dataclass
class ParseResult:
    parsed: dict | None
    schema_valid: bool
    parse_error: str | None
    schema_errors: list[str]


def _extract_json_object(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def parse_and_validate(clean_text: str, schema: dict) -> ParseResult:
    parsed = _extract_json_object(clean_text)
    if parsed is None:
        return ParseResult(parsed=None, schema_valid=False, parse_error="JSON_DECODE_FAILED", schema_errors=[])

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(parsed), key=lambda e: list(e.path))
    error_msgs = [f"{list(e.path)}: {e.message}" for e in errors]
    return ParseResult(parsed=parsed, schema_valid=not error_msgs, parse_error=None, schema_errors=error_msgs)
