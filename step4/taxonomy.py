"""persona_model/kmst_official_taxonomy.json — persona_03이 실제로 골라 써야 할 label_code 통제 어휘.

해양안전심판원(KMST)이 자체 제정한 사고원인 분류체계(대분류 3종 아래 세부항목)다. 구 STEP4
파이프라인(config/deprecated/config.json의 prompt.candidate_labels)이 쓰던 것과 동일한 공식
목록이며, 이 목록을 프롬프트에 넣지 않으면 모델이 label_code를 마음대로 지어내
(예: "DC-001", "DIRECT_CAUSE") 실제 KMST 분류체계와 무관한 값을 낸다.

persona_model/cause_label_taxonomy.json(HF_*/TF_* 영문 코드, status=INITIAL_TAXONOMY_REQUIRES_EXPERT_REVIEW)은
페르소나 생성 과정에서 별도로 만들어진 초안이라 이 공식 목록과 다르다 — 실제 레이블링은
kmst_official_taxonomy.json 기준으로 한다.
"""
from __future__ import annotations

import copy
import json
import logging
import unicodedata
from pathlib import Path


def load_taxonomy(persona_dir: str | Path, logger: logging.Logger) -> dict:
    path = Path(persona_dir) / "kmst_official_taxonomy.json"
    if not path.exists():
        raise FileNotFoundError(
            f"kmst_official_taxonomy.json이 없습니다: {path} — persona_03 레이블링에 필수 자산입니다."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    categories = data.get("categories", {})
    n_items = sum(len(v) for v in categories.values())
    logger.info(
        "kmst_official_taxonomy 로드: 대분류 %d종, 세부항목 %d개", len(categories), n_items
    )
    return data


def _norm(code: str) -> str:
    """공백/유니코드 정규형(NFC-NFD) 차이로 인한 오탐을 막기 위한 비교용 정규화.

    2026-08-11 재검토에서 이번 실행분엔 실제 불일치가 없었음을 확인했지만, 이 저장소에
    선례(커밋 e20009e, 대표문장 NFC/NFD 정규화 버그)가 있어 방어적으로 넣는다.
    """
    return unicodedata.normalize("NFC", code).strip()


def label_codes(taxonomy: dict) -> set[str]:
    return {item for items in taxonomy.get("categories", {}).values() for item in items}


def is_known_label_code(code: str | None, known_label_codes: set[str]) -> bool:
    if code is None:
        return False
    normalized_known = {_norm(k) for k in known_label_codes}
    return _norm(code) in normalized_known


def apply_label_code_enum(schema: dict, taxonomy: dict) -> dict:
    """cause_labels[].label_code에 공식 목록 + 빈 문자열(NOT_SCORABLE용 미확정 표시) enum을 주입한다.

    원래 label_code는 스키마상 `type: string`뿐이라 디코딩 단계에서 새 이름을 지어내는 것을
    막을 수단이 없었다(test_20260811/07_유효성지표_생성량분석_정리.md 6-1절). Schema 유효성이
    받는 grammar-constrained decoding(vLLM StructuredOutputsParams)과 동일한 강제 수준을
    label_code에도 걸기 위해, 실행 시 로드된 스키마 dict를 여기서 패치한다 — 원본
    persona_0N_output_schema.json 파일은 건드리지 않는다(단일 출처는 여전히 파일이고, enum
    목록의 실제 출처도 여전히 kmst_official_taxonomy.json이다). cause_labels가 없는 스키마
    (persona_01/02)는 그대로 반환한다.
    """
    patched = copy.deepcopy(schema)
    cause_labels_schema = patched.get("properties", {}).get("cause_labels")
    if not cause_labels_schema:
        return patched
    label_props = cause_labels_schema.get("items", {}).get("properties", {})
    if "label_code" not in label_props:
        return patched
    allowed = sorted(label_codes(taxonomy)) + [""]
    label_props["label_code"] = {
        "type": "string",
        "enum": allowed,
        "_note": (
            "실행 시 step4/taxonomy.py::apply_label_code_enum()이 kmst_official_taxonomy.json"
            "(공식 22항목) + 빈 문자열(NOT_SCORABLE 미확정 표시용)로 주입한 enum. "
            "원본 persona_03_output_schema.json 파일에는 이 enum이 없다."
        ),
    }
    return patched


def has_scored_blank_label(parsed: dict) -> bool:
    """confidence가 NOT_SCORABLE이 아닌데 label_code가 빈 문자열인 '결측 결함' 항목이 있는지 확인.

    enum(위 apply_label_code_enum)은 label_code 하나만 제약할 뿐, confidence와의 정합성
    (점수를 매겼으면 이름도 있어야 한다)까지는 강제하지 못한다. JSON Schema의 필드 간 교차
    제약(if/then)을 쓰지 않은 건 추측이 아니라 실측 결과다 — 2026-08-12 xgrammar(vLLM
    structured_outputs 백엔드)로 if/then이 있는 스키마와 없는 스키마를 각각
    `Grammar.from_json_schema()`로 컴파일해 문자열 비교했더니 **완전히 동일한 문법**이
    나왔다(if/then이 조용히 무시됨, 에러도 안 남). 그래서 이 검사는 생성 후 파이프라인의
    재시도 경로(pipeline.py)에서만 쓰이고, 재시도로도 못 고치면 아래
    force_resolve_scored_blank_labels()가 결정적으로 마무리한다.
    """
    for item in parsed.get("cause_labels") or []:
        if not isinstance(item, dict):
            continue
        if item.get("label_code") == "" and item.get("confidence") != "NOT_SCORABLE":
            return True
    return False


def merge_retry_preserving_good_items(original_parsed: dict, retry_parsed: dict) -> dict:
    """교정 재시도 응답에서, 원래 결함이 없던 항목은 재시도 결과를 무시하고 원본을 그대로 쓴다.

    2026-08-12 cluster_0/identity_on 실측에서 발견된 결함: 교정 재시도 프롬프트가 "결함 항목만
    고치고 나머지는 절대 건드리지 말라"고 자연어로 지시했지만, 이건 강제가 아니라 지시일 뿐이라
    모델이 응답 전체를 다시 쓰는 과정에서 원래 정상이었던 항목(예: "환경적 요인 - 날씨 등" ->
    "기상 등 불가항력"처럼 명확히 매칭됐던 항목)까지 빈 값으로 바꿔버린 사례가 실측됐다. 자연어
    지시에 기대는 대신, cause_id로 항목을 매칭해 **원래 결함이 없던 항목은 재시도 응답과 무관하게
    무조건 원본 그대로 보존**한다 — 결함이 있던 항목만 재시도 결과로 교체 대상이 된다(그래도 여전히
    결함이면 force_resolve_scored_blank_labels가 마무리한다). identity_on/off 양쪽에 동일하게
    적용되는 구조적 보정이라 어느 조건에 유리하게 작용하지 않는다.
    """
    original_items = original_parsed.get("cause_labels") or []
    retry_by_id = {
        it.get("cause_id"): it
        for it in (retry_parsed.get("cause_labels") or [])
        if isinstance(it, dict) and it.get("cause_id") is not None
    }

    merged_items = []
    for orig_item in original_items:
        if not isinstance(orig_item, dict):
            merged_items.append(orig_item)
            continue
        was_defective = orig_item.get("label_code") == "" and orig_item.get("confidence") != "NOT_SCORABLE"
        if not was_defective:
            merged_items.append(orig_item)
            continue
        replacement = retry_by_id.get(orig_item.get("cause_id"))
        merged_items.append(replacement if replacement is not None else orig_item)

    merged = dict(retry_parsed)
    merged["cause_labels"] = merged_items
    return merged


def force_resolve_scored_blank_labels(parsed: dict) -> tuple[dict, int]:
    """재시도로도 못 고친 결측 결함을 코드로 결정적으로 마무리한다.

    2026-08-12 실측(test_20260811/08_코드수정_실측검증.md)에서 자연어 교정 재시도가
    불안정함이 드러났다 — 재시도를 반복할수록 항상 나아지는 게 아니라, 오히려 "confidence는
    그대로 두고 label_code만 빈 값"이라는 같은 결함이 반복되거나(cluster_2/on) 어떤 경우엔
    직전 시도보다 더 확신도 높은 결측 결함으로 악화되기도 했다(cluster_0/on, 2차 재시도 후).
    모델의 다음 생성 결과를 더 신뢰하는 대신, 재시도 예산을 다 쓰고도 결함이 남아 있으면
    그 항목만 `confidence=NOT_SCORABLE`, `official_ratio`/`analytic_contribution_score`를
    `null`로 코드에서 직접 덮어써 "confidence는 있는데 이름이 없는" 상태가 최종 산출물에
    남는 일은 없도록 100% 보장한다. label_code가 이미 채워진 다른 항목은 건드리지 않는다.
    반환값은 (수정된 dict, 강제로 고친 항목 수) — 0이면 애초에 손댈 필요가 없었다는 뜻이다.
    """
    forced = 0
    for item in parsed.get("cause_labels") or []:
        if not isinstance(item, dict):
            continue
        if item.get("label_code") == "" and item.get("confidence") != "NOT_SCORABLE":
            item["confidence"] = "NOT_SCORABLE"
            item["official_ratio"] = None
            item["analytic_contribution_score"] = None
            forced += 1
    return parsed, forced


def format_candidate_labels_block(taxonomy: dict) -> str:
    # 구 파이프라인의 교훈(lib_llm_common.php 주석): 대분류 태그를 프롬프트에 그대로 넣으면
    # 모델이 대괄호 태그째로 베껴 쓰는 문제가 있었다. 세부항목만 평평하게 이어붙인다.
    items = sorted(label_codes(taxonomy))
    lines = [
        "(해양안전심판원(KMST) 자체 사고원인 분류체계 — 아래 목록에 있는 이름을 반드시 그대로 사용할 것. "
        "새 label_code를 지어내지 말고, 목록 중 실제로 근거가 뒷받침하는 항목만 고를 것.)"
    ]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)
