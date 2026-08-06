"""persona_0N.md에서 런타임 System Prompt만 추출해 조립한다 (identity_on/off 포함).

persona_0N.md의 1~18절은 설계 문서(사람이 읽는 근거·체크리스트)이고, 실제로 모델에
보내는 것은 "실제 실행용 System Prompt" 절 + persona_0N_output_schema.json뿐이다.
User Prompt는 군집(cluster) 단위 입력에 맞춰 코드에서 직접 조립한다 — persona_0N.md의
"실제 실행용 User Prompt Template"(단일 사건 전제)은 군집엔 맞지 않아 쓰지 않는다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from step4 import ablation

_HEADER_RE = re.compile(r"^##\s+\d+\.\s+(.+?)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*?)\n```\s*$", re.DOTALL)


def _extract_section(full_text: str, title_substr: str) -> str:
    headers = list(_HEADER_RE.finditer(full_text))
    for i, h in enumerate(headers):
        if title_substr in h.group(1):
            start = h.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(full_text)
            body = full_text[start:end].strip()
            m = _FENCE_RE.match(body)
            if m:
                body = m.group(1).strip()
            return body
    raise ValueError(f"섹션을 찾지 못했습니다: '{title_substr}'")


@dataclass
class StagePrompt:
    persona_id: str
    system_prompt_on: str
    system_prompt_off: str


def load_stage_prompt(md_path: Path, persona_id: str, diff_dir: Path, logger) -> StagePrompt:
    raw_text = md_path.read_text(encoding="utf-8")
    system_section = _extract_section(raw_text, "실제 실행용 System Prompt")

    on_text, off_text = ablation.build_and_verify_diff(
        persona_id=persona_id, raw_text=system_section, diff_dir=diff_dir, logger=logger
    )
    return StagePrompt(
        persona_id=persona_id,
        system_prompt_on=on_text.strip(),
        system_prompt_off=off_text.strip(),
    )


NO_THINK_SUFFIX = "\n\n/no_think"


def build_system_prompt(stage_prompt: StagePrompt, use_identity: bool, schema: dict) -> str:
    # "실제 실행용 System Prompt"는 "위 JSON Schema에 맞는 JSON 객체..."라고만 말하고 실제 스키마는
    # persona_0N.md의 별도 설계 섹션(런타임에는 보내지 않음)에 있으므로, 여기서 명시적으로 붙여준다.
    base = stage_prompt.system_prompt_on if use_identity else stage_prompt.system_prompt_off
    schema_block = "\n\n[OUTPUT_JSON_SCHEMA]\n" + json.dumps(schema, ensure_ascii=False, indent=2)
    return base + schema_block + NO_THINK_SUFFIX


# ---------------------------------------------------------------------------
# 군집(cluster) 모드 — persona_0N.md는 건드리지 않고, identity_on/off 프롬프트
# 뒤에 "이 입력은 단일 사건이 아니라 군집이다"라는 재해석 지침을 코드에서 덧붙인다.
# on/off 양쪽에 동일하게 붙으므로 ablation diff 순수성(정체성 블록 외 무변경)은 유지된다.
# User Prompt Template도 persona_0N.md의 것을 재사용하지 않고 군집 전용 템플릿을 새로 쓴다
# (CASE_METADATA/DECISION_TEXT 같은 단일사건 필드가 군집엔 없기 때문).
# ---------------------------------------------------------------------------

_CLUSTER_MODE_NOTE = {
    "persona_01": (
        "이 군집에서 반복적으로 관찰되는 상황 요소와 증거 패턴만 facts/evidence로 채택하십시오. "
        "대표 문장 하나에만 등장하는 특이 사항을 이 군집 전체의 사실인 것처럼 일반화하지 마십시오."
    ),
    "persona_02": (
        "페르소나 1이 정리한 이 군집의 패턴을 바탕으로, 이 군집을 대표할 만한 원인 후보들의 "
        "인과관계와 법령 정합성을 검증하십시오. 대표 문장 하나에서만 나온 원인을 군집 전체의 "
        "대표 원인으로 확정하지 말고, 반복성·근거 수를 함께 고려하십시오."
    ),
    "persona_03": (
        "페르소나 2가 검증한 이 군집의 대표 원인 후보를 KMST 공식 분류체계(아래 [CANDIDATE_LABELS])의 "
        "이름 그대로 매핑하고, 이 군집을 대표하는 정도를 analytic_contribution_score로 표현하십시오."
    ),
}


def cluster_mode_preamble(persona_id: str) -> str:
    note = _CLUSTER_MODE_NOTE.get(persona_id, "")
    return (
        "\n\n[군집 분석 모드]\n"
        "지금 입력은 하나의 사건이 아니라, K-means로 묶인 유사 사건 군집의 대표 키워드와 "
        "서로 다른 사건에서 뽑힌 대표 문장 여러 개입니다. '사건', '이 사건' 같은 표현은 "
        "'이 군집을 대표하는 사건 패턴'으로 해석하십시오. 특정 사건번호나 개별 사건의 세부사항을 "
        "지어내지 마십시오. 타임라인(timeline)은 군집에 적용되지 않으므로 빈 배열로 두십시오. "
        "actor_ids/evidence_ids/fact_ids 같은 ID 배열은 각 항목당 최대 5개까지만 실제로 "
        "존재하는 근거만 인용하고, 그 이상 순차적으로 번호를 늘려가며 나열하지 마십시오 — "
        "이 군집의 대표 문장은 최대 15개뿐이므로 ID가 두 자릿수를 넘어가면 잘못된 것입니다. "
        + note
    )


def build_cluster_system_prompt(stage_prompt: StagePrompt, use_identity: bool, schema: dict) -> str:
    base = build_system_prompt(stage_prompt, use_identity=use_identity, schema=schema)
    # NO_THINK_SUFFIX가 이미 맨 끝에 붙어있으므로, 그 앞에 군집 모드 안내를 끼워넣는다.
    if base.endswith(NO_THINK_SUFFIX):
        base = base[: -len(NO_THINK_SUFFIX)]
    return base + cluster_mode_preamble(stage_prompt.persona_id) + NO_THINK_SUFFIX


def build_cluster_user_prompt(
    *,
    cluster_id: str,
    n_docs: int,
    freq_keywords: list[str],
    distinctive_keywords: list[str],
    sample_sentences: list[str],
    previous_output: dict | None,
    legal_context: str,
    candidate_labels_block: str | None = None,
) -> str:
    prev_json = json.dumps(previous_output, ensure_ascii=False) if previous_output is not None else "null"
    samples_block = "\n".join(f"- {s}" for s in sample_sentences) if sample_sentences else "(대표 문장 없음)"

    parts = [
        "[CLUSTER_METADATA]",
        f"cluster_id: {cluster_id}",
        f"n_docs: {n_docs}",
        "",
        "[CLUSTER_KEYWORDS]",
        f"빈도 상위: {', '.join(freq_keywords) if freq_keywords else '(없음)'}",
        f"특징 상위: {', '.join(distinctive_keywords) if distinctive_keywords else '(없음)'}",
        "",
        "[CLUSTER_SAMPLE_SENTENCES]",
        samples_block,
        "",
        "[PREVIOUS_PERSONA_OUTPUT]",
        prev_json,
        "",
        "[RETRIEVED_LEGAL_CONTEXT]",
        legal_context,
    ]
    if candidate_labels_block:
        parts += ["", "[CANDIDATE_LABELS]", candidate_labels_block]
    return "\n".join(parts)
