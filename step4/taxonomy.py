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

import json
import logging
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


def label_codes(taxonomy: dict) -> set[str]:
    return {item for items in taxonomy.get("categories", {}).values() for item in items}


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
