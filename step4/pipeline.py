"""체인(P01->P02->P03) x 조건(identity_on/off) 오케스트레이션 — 군집(STEP3 K-means) 단위.

분석 단위는 개별 재결서가 아니라 STEP3 K-means 군집이다. 군집 하나를 "이 군집을
대표하는 사건 패턴"으로 보고, P01(패턴 구조화) -> P02(대표 원인 검증) -> P03(KMST
레이블 확정)을 identity_on/identity_off 각각에 대해 돌린다.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from step4 import assets as assets_mod
from step4 import backend as backend_mod
from step4 import cluster_corpus
from step4 import determinism as determinism_mod
from step4 import legal_retrieval
from step4 import metrics as metrics_mod
from step4 import prompt_builder
from step4 import report as report_mod
from step4 import schema_validate
from step4 import taxonomy as taxonomy_mod
from step4.config import Config

UNIT_LABEL = "군집"


def _prepare(config: Config, logger: logging.Logger):
    persona_dir = config.data["paths"]["persona_dir"]
    diff_dir = config.output_root / "artifacts" / "ablation_diff"
    comparison_mode = config.data["experiment"].get("comparison_mode", "identity_marker")

    persona_assets = assets_mod.discover_persona_assets(persona_dir, logger)
    schemas = {a.persona_id: json.loads(a.schema_path.read_text(encoding="utf-8")) for a in persona_assets}

    stage_prompts = {}
    if comparison_mode == "document_pair":
        # identity_on/off는 정체성 문장 유무만 다르지만, 이 모드는 처음부터 독립적으로
        # 작성된 두 문서(persona_0N.md vs ablation_no_persona/task_0N_no_persona_*.md)를
        # 그대로 on/off로 비교한다 — persona_0N.md 자체는 전혀 건드리지 않는다.
        no_persona_dir = config.data["paths"]["no_persona_dir"]
        no_persona_map = assets_mod.discover_no_persona_assets(no_persona_dir, logger)
        for a in persona_assets:
            if a.persona_id not in no_persona_map:
                raise FileNotFoundError(
                    f"{a.persona_id}에 대응하는 no-persona 문서가 {no_persona_dir}에 없습니다."
                )
            stage_prompts[a.persona_id] = prompt_builder.load_document_pair_prompt(
                a.md_path, no_persona_map[a.persona_id], a.persona_id, logger
            )
    else:
        for a in persona_assets:
            stage_prompts[a.persona_id] = prompt_builder.load_stage_prompt(
                a.md_path, a.persona_id, diff_dir, logger
            )

    legal_chunks = legal_retrieval.load_legal_chunks(Path(persona_dir) / "data", logger)
    taxonomy = taxonomy_mod.load_taxonomy(persona_dir, logger)

    # cause_labels[].label_code에 공식 목록 enum을 주입해, Schema 유효성과 동일한 수준의
    # 디코딩 제약(grammar-constrained decoding)을 받게 한다 — 이전엔 이 필드만 자유 문자열이라
    # 새 label_code를 지어내는 것(hallucination)을 막을 수단이 없었다. persona_01/02처럼
    # cause_labels가 없는 스키마는 그대로 반환되므로 다른 단계엔 영향 없다. 이 patched schema는
    # identity_on/off 조건 루프 바깥(real_run의 schemas[stage] 조회)에서 그대로 공유되므로,
    # 두 조건이 서로 다른 스키마를 보게 되는 일은 없다 — 통제 변인 성격이 유지된다.
    if "persona_03" in schemas:
        schemas["persona_03"] = taxonomy_mod.apply_label_code_enum(schemas["persona_03"], taxonomy)

    return persona_assets, schemas, stage_prompts, legal_chunks, taxonomy


def _load_clusters(config: Config, logger: logging.Logger, limit: int | None = None):
    cluster_cfg = config.data["cluster"]
    units = cluster_corpus.load_clusters(
        clusters_csv=cluster_cfg["paths"]["clusters_csv"],
        cluster_keywords_csv=cluster_cfg["paths"]["cluster_keywords_csv"],
        step1_jsonl=cluster_cfg["paths"]["step1_jsonl"],
        samples_per_cluster=cluster_cfg["samples_per_cluster"],
        samples_per_cluster_default=cluster_cfg["samples_per_cluster_default"],
        sample_text_maxlen=cluster_cfg["sample_text_maxlen"],
        logger=logger,
    )
    if limit is not None:
        units = units[:limit]
    return units


def dry_run(config: Config, logger: logging.Logger) -> None:
    persona_assets, schemas, stage_prompts, legal_chunks, taxonomy = _prepare(config, logger)
    units = _load_clusters(config, logger)
    unit_count = len(units)
    conditions = config.condition_names
    chain_order = config.chain_order

    print("=== STEP4 dry-run (군집 단위) ===")
    print(f"{UNIT_LABEL} 수: {unit_count} ({[u.cluster_id for u in units]})")
    print(f"체인 단계: {chain_order}")
    print(f"조건: {conditions}")
    print(
        f"예상 호출 수 = {UNIT_LABEL} 수({unit_count}) x 단계({len(chain_order)}) x 조건({len(conditions)}) "
        f"= {unit_count * len(chain_order) * len(conditions)}"
    )
    for condition in conditions:
        for stage in chain_order:
            print(f"  - condition={condition}, stage={stage}: {unit_count}회 호출 예정")
    comparison_mode = config.data["experiment"].get("comparison_mode", "identity_marker")
    if comparison_mode == "document_pair":
        print("문서쌍 비교 (persona_0N.md vs ablation_no_persona/task_0N_no_persona_*.md):")
        for a in persona_assets:
            sp = stage_prompts[a.persona_id]
            same = sp.system_prompt_on.strip() == sp.system_prompt_off.strip()
            print(f"  - {a.persona_id}: 로드 완료 (on/off 실행용 System Prompt 동일 여부: {same})")
    else:
        print("ablation diff:")
        for a in persona_assets:
            print(f"  - {a.persona_id}: artifacts/ablation_diff/{a.persona_id}.diff 생성 및 검증 완료")
    print("(--dry-run: 실제 생성은 수행하지 않음)")


# 3단계 전체에 걸쳐 flat 테이블에 항상 존재하는 컬럼 집합 (해당 없는 단계는 None으로 채움).
_STAGE_SUMMARY_COLUMNS = [
    # persona_01
    "n_facts",
    "n_evidence",
    "n_actors",
    "n_vessels",
    "n_missing_information",
    "handoff_status",
    # persona_02
    "n_cause_candidates",
    "n_alternative_causes",
    "n_legal_conflicts",
    "n_unresolved_issues",
    # support_level이 HIGH/MEDIUM인데 fact_ids/evidence_ids가 둘 다 빈 항목 수(최종 산출물
    # 기준 — 재시도·강제 하향 조정 이후). 0이면 이 결함이 남아있지 않다는 뜻.
    "n_causes_unsupported",
    # persona_03 (최종 레이블)
    "accident_type",
    "root_cause_primary",
    "root_cause_secondary",
    "n_causes",
    "analytic_contribution",
    "official_ratio",
    "taxonomy_valid",
    # 문서 단위 all-or-nothing인 taxonomy_valid만으로는 항목 하나만 틀려도 문서 전체가
    # False가 되어 표본이 작을 때 결론을 쉽게 왜곡한다(07_유효성지표_생성량분석_정리.md 6-5절).
    # 항목(label_code) 단위로도 집계할 수 있도록 분자/분모를 별도로 남긴다.
    "n_taxonomy_ok_items",
    "n_taxonomy_total_items",
]


def _extract_persona_01_summary(parsed: dict) -> dict:
    return {
        "n_facts": len(parsed.get("facts") or []),
        "n_evidence": len(parsed.get("evidence") or []),
        "n_actors": len(parsed.get("actors") or []),
        "n_vessels": len(parsed.get("vessels") or []),
        "n_missing_information": len(parsed.get("missing_information") or []),
        "handoff_status": parsed.get("handoff_status"),
    }


def has_unsupported_high_confidence_cause(parsed: dict) -> bool:
    """support_level이 HIGH/MEDIUM인데 fact_ids와 evidence_ids가 둘 다 빈 원인이 있는지 확인.

    persona_02_output_schema.json은 causes[]의 fact_ids/evidence_ids를 required로
    강제하지 않고(선택 필드), support_level과의 교차 제약도 없다 — "확신도는 높다면서
    근거 인용은 0개"인 조합이 스키마상 완전히 유효하게 통과한다. 이건 P03의
    label_code/confidence 정합성 문제(taxonomy.py::has_scored_blank_label)와 정확히
    같은 클래스의 결함이며, cluster_0의 "환경적 요인 - 날씨 등"(support_level=HIGH,
    evidence_ids=[])이 실제로 관측된 사례다(08_코드수정_실측검증.md 후속 조사).
    """
    for cause in parsed.get("causes") or []:
        if not isinstance(cause, dict):
            continue
        if cause.get("support_level") in ("HIGH", "MEDIUM") and not cause.get("fact_ids") and not cause.get(
            "evidence_ids"
        ):
            return True
    return False


def force_downgrade_unsupported_causes(parsed: dict) -> tuple[dict, int]:
    """재시도로도 못 고친 미근거 고신뢰 원인을 코드로 결정적으로 마무리한다.

    taxonomy.py::force_resolve_scored_blank_labels()와 동일한 설계 — 모델의 다음 생성
    결과를 한 번 더 신뢰하는 대신, 재시도 예산을 다 쓰고도 결함이 남아 있으면 그 원인의
    support_level만 코드에서 직접 `INSUFFICIENT`(스키마에 이미 있는 정직한 하락 값)로
    덮어쓴다. fact_ids/evidence_ids가 이미 채워진 다른 원인은 건드리지 않는다.
    """
    downgraded = 0
    for cause in parsed.get("causes") or []:
        if not isinstance(cause, dict):
            continue
        if cause.get("support_level") in ("HIGH", "MEDIUM") and not cause.get("fact_ids") and not cause.get(
            "evidence_ids"
        ):
            cause["support_level"] = "INSUFFICIENT"
            downgraded += 1
    return parsed, downgraded


def merge_p02_retry_preserving_good_causes(original_parsed: dict, retry_parsed: dict) -> dict:
    """taxonomy.py::merge_retry_preserving_good_items()와 동일한 이유·설계 — 전체 causes[]를
    다시 생성하는 교정 재시도가 원래 정상이던 원인까지 훼손하는 사례가 실측됐으므로(cluster_0/on,
    같은 날 저녁 조사), cause_id로 매칭해 원래 결함(미근거 고신뢰)이 없던 원인은 재시도 응답과
    무관하게 원본을 그대로 보존한다.
    """
    original_causes = original_parsed.get("causes") or []
    retry_by_id = {
        c.get("cause_id"): c
        for c in (retry_parsed.get("causes") or [])
        if isinstance(c, dict) and c.get("cause_id") is not None
    }
    merged_causes = []
    for orig in original_causes:
        if not isinstance(orig, dict):
            merged_causes.append(orig)
            continue
        was_defective = (
            orig.get("support_level") in ("HIGH", "MEDIUM")
            and not orig.get("fact_ids")
            and not orig.get("evidence_ids")
        )
        if not was_defective:
            merged_causes.append(orig)
            continue
        replacement = retry_by_id.get(orig.get("cause_id"))
        merged_causes.append(replacement if replacement is not None else orig)
    merged = dict(retry_parsed)
    merged["causes"] = merged_causes
    return merged


def _extract_persona_02_summary(parsed: dict) -> dict:
    return {
        "n_cause_candidates": len(parsed.get("causes") or []),
        "n_alternative_causes": len(parsed.get("alternative_causes") or []),
        "n_legal_conflicts": len(parsed.get("legal_conflicts") or []),
        "n_unresolved_issues": len(parsed.get("unresolved_issues") or []),
        "n_causes_unsupported": sum(
            1
            for c in (parsed.get("causes") or [])
            if isinstance(c, dict)
            and c.get("support_level") in ("HIGH", "MEDIUM")
            and not c.get("fact_ids")
            and not c.get("evidence_ids")
        ),
        "handoff_status": parsed.get("handoff_status"),
    }


def _extract_persona_03_summary(parsed: dict, known_label_codes: set[str] | None) -> dict:
    cause_labels = parsed.get("cause_labels") or []
    cause_labels_sorted = sorted(
        cause_labels,
        key=lambda c: (c.get("analytic_contribution_score") if isinstance(c, dict) else None) or -1,
        reverse=True,
    )
    root_cause_primary = cause_labels_sorted[0].get("label_code") if cause_labels_sorted else None
    root_cause_secondary = [c.get("label_code") for c in cause_labels_sorted[1:]] if len(cause_labels_sorted) > 1 else []
    incident_labels = parsed.get("incident_labels") or {}
    accident_type = incident_labels.get("accident_type") if isinstance(incident_labels, dict) else None
    all_codes = [c.get("label_code") for c in cause_labels]
    # NFC 정규화 + strip 후 비교 — 공백/유니코드 정규형 차이로 인한 오탐을 막는 방어 코드
    # (taxonomy.py::is_known_label_code, 07_유효성지표_생성량분석_정리.md 6-4절 참고).
    code_is_ok = (
        [taxonomy_mod.is_known_label_code(code, known_label_codes) for code in all_codes]
        if known_label_codes
        else []
    )
    taxonomy_valid = all(code_is_ok) if (known_label_codes and all_codes) else None
    return {
        "accident_type": accident_type,
        "root_cause_primary": root_cause_primary,
        "root_cause_secondary": json.dumps(root_cause_secondary, ensure_ascii=False),
        "n_causes": len(cause_labels),
        "analytic_contribution": json.dumps(
            [{"label_code": c.get("label_code"), "score": c.get("analytic_contribution_score")} for c in cause_labels],
            ensure_ascii=False,
        ),
        "official_ratio": json.dumps(parsed.get("official_ratio"), ensure_ascii=False) if parsed.get("official_ratio") is not None else None,
        "taxonomy_valid": taxonomy_valid,
        "n_taxonomy_ok_items": sum(code_is_ok) if code_is_ok else None,
        "n_taxonomy_total_items": len(all_codes) if known_label_codes else None,
    }


def _extract_stage_summary(stage: str, parsed: dict | None, known_label_codes: set[str] | None = None) -> dict:
    row = dict.fromkeys(_STAGE_SUMMARY_COLUMNS, None)
    if not parsed:
        return row
    if stage == "persona_01":
        row.update(_extract_persona_01_summary(parsed))
    elif stage == "persona_02":
        row.update(_extract_persona_02_summary(parsed))
    elif stage == "persona_03":
        row.update(_extract_persona_03_summary(parsed, known_label_codes))
    return row


def real_run(
    config: Config,
    logger: logging.Logger,
    limit: int | None = None,
    skip_determinism: bool = False,
) -> Path:
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    output_root = config.output_root
    raw_dir = output_root / "raw"
    parsed_dir = output_root / "parsed"
    flat_dir = output_root / "flat"
    metrics_dir = output_root / "metrics"
    artifacts_dir = output_root / "artifacts"
    for d in (raw_dir, parsed_dir, flat_dir, metrics_dir, artifacts_dir):
        d.mkdir(parents=True, exist_ok=True)

    _persona_assets, schemas, stage_prompts, legal_chunks, taxonomy = _prepare(config, logger)
    candidate_labels_block = taxonomy_mod.format_candidate_labels_block(taxonomy)
    known_label_codes = taxonomy_mod.label_codes(taxonomy)

    units = _load_clusters(config, logger, limit=limit)
    unit_count = len(units)
    logger.info("실행 대상 %s 수: %d (limit=%s)", UNIT_LABEL, unit_count, limit)

    model_family = config.model_family
    append_no_think = model_family == "qwen3"
    logger.info(
        "모델 계열 판별: %s (id=%s) — Qwen3 전용 대응(/no_think, repetition_retry)이 %s",
        model_family,
        config.data["model"].get("id"),
        "활성화됩니다" if model_family == "qwen3" else "비활성화됩니다",
    )

    backend = backend_mod.VLLMBackend(config.data["model"], config.data["generation"], logger)
    backend.load()

    # identity_on/off 프롬프트 토큰 수 차이 기록 (지시서 2-3)
    token_diff_report = {}
    for persona_id, sp in stage_prompts.items():
        on_tokens = backend.count_tokens(sp.system_prompt_on)
        off_tokens = backend.count_tokens(sp.system_prompt_off)
        pct = (on_tokens - off_tokens) / off_tokens * 100 if off_tokens else float("nan")
        token_diff_report[persona_id] = {"on_tokens": on_tokens, "off_tokens": off_tokens, "diff_pct": pct}
        if abs(pct) > 10:
            logger.warning(
                "%s: identity_on/off 시스템프롬프트 토큰 차이가 %.1f%%로 10%% 초과 — 교란변수 가능성",
                persona_id,
                pct,
            )

    save_failures = []
    repetition_retry_cases = []
    taxonomy_defect_retry_cases = []
    p02_evidence_retry_cases = []
    rows: list[dict] = []
    retry_on_parse_error = config.data["validation"].get("retry_on_parse_error", 0)
    repetition_retry_cfg = config.data["validation"].get("repetition_retry", {})
    # 2026-08-07 초반엔 "Qwen3 전용 우회"로 보고 model_family=="qwen3"로 게이팅했었다 —
    # 근거는 Qwen2.5-14B-Instruct가 persona_01 5/5를 기본 파라미터로 성공한 것이었다.
    # 하지만 이어진 persona_02/identity_on에서 Qwen2.5도 cluster_0/cluster_1이 동일한
    # 종류(문법은 안 깨지지만 문자열 내부 공백 반복으로 max_new_tokens 소진)의 반복루프에
    # 빠지는 것이 실측되어, 이 현상이 Qwen3 고유가 아니라 greedy 디코딩 일반의 약점이라는
    # 원래 진단이 맞았음이 재확인됐다. 그래서 model_family 게이팅을 제거하고 모델 계열과
    # 무관하게 config.enabled로만 제어한다 — /no_think, enable_thinking 같은 진짜
    # Qwen3 아키텍처 전용 항목만 계속 model_family로 게이팅한다.
    repetition_retry_enabled = repetition_retry_cfg.get("enabled", False)
    max_new_tokens = config.data["generation"]["max_new_tokens"]
    repetition_retry_token_threshold = (
        repetition_retry_cfg.get("completion_token_ratio_threshold", 0.95) * max_new_tokens
    )
    determinism_pairs: list[tuple[str, str]] | None = None
    determinism_ids: list[str] | None = None

    for condition in config.data["experiment"]["conditions"]:
        condition_name = condition["name"]
        use_identity = condition["use_persona_identity"]
        previous_outputs: dict[str, dict | None] = {u.cluster_id: None for u in units}

        for stage in config.chain_order:
            stage_prompt = stage_prompts[stage]
            schema = schemas[stage]

            pending_units = []
            for unit in units:
                parsed_path = parsed_dir / unit.cluster_id / stage / f"{condition_name}.json"
                if parsed_path.exists():
                    cached = json.loads(parsed_path.read_text(encoding="utf-8"))
                    if cached.get("parsed") is not None:
                        previous_outputs[unit.cluster_id] = cached["parsed"]
                    rows.append(cached["flat_row"])
                    continue
                pending_units.append(unit)

            if not pending_units:
                logger.info("체크포인트: condition=%s stage=%s 전체 스킵(이미 완료)", condition_name, stage)
                continue

            system_prompt = prompt_builder.build_cluster_system_prompt(
                stage_prompt, use_identity=use_identity, schema=schema, append_no_think=append_no_think
            )
            pairs = []
            for unit in pending_units:
                query_text = " ".join(unit.freq_keywords + unit.sample_sentences)
                legal_context = legal_retrieval.retrieve(query_text, legal_chunks)
                user_prompt = prompt_builder.build_cluster_user_prompt(
                    cluster_id=unit.cluster_id,
                    n_docs=unit.n_docs,
                    freq_keywords=unit.freq_keywords,
                    distinctive_keywords=unit.distinctive_keywords,
                    sample_sentences=unit.sample_sentences,
                    previous_output=previous_outputs.get(unit.cluster_id),
                    legal_context=legal_context,
                    candidate_labels_block=candidate_labels_block if stage == "persona_03" else None,
                )
                pairs.append((system_prompt, user_prompt))

            if condition_name == "identity_on" and stage == config.chain_order[0]:
                determinism_pairs = pairs
                determinism_ids = [u.cluster_id for u in pending_units]

            (artifacts_dir / "prompts").mkdir(parents=True, exist_ok=True)
            sample_prompt_path = artifacts_dir / "prompts" / f"{stage}_{condition_name}.txt"
            sample_prompt_path.write_text(
                "=== SYSTEM ===\n" + system_prompt + "\n\n=== USER (예시: " + pending_units[0].cluster_id + ") ===\n" + pairs[0][1],
                encoding="utf-8",
            )

            logger.info(
                "생성 시작: condition=%s stage=%s %s수=%d", condition_name, stage, UNIT_LABEL, len(pending_units)
            )
            gen_results = backend.generate_batch(pairs, schema=schema)

            for unit_idx, (unit, gen) in enumerate(zip(pending_units, gen_results)):
                parse_result = schema_validate.parse_and_validate(gen.clean_text, schema)

                if not parse_result.schema_valid and retry_on_parse_error:
                    corrective_user_prompt = pairs[unit_idx][1] + "\n\n[SYSTEM_NOTE] 출력은 JSON 객체만 허용된다. 다시 JSON만 출력하라."
                    retry_gen = backend.generate_batch([(system_prompt, corrective_user_prompt)], schema=schema)[0]
                    retry_parse = schema_validate.parse_and_validate(retry_gen.clean_text, schema)
                    gen = retry_gen
                    parse_result = retry_parse
                    parse_retry_flag = 1
                else:
                    parse_retry_flag = 0

                # 반복루프(예: cluster_3/identity_on persona_01이 존재하지 않는 evidence_id를
                # max_new_tokens까지 무한 나열)로 인한 실패만 국소적으로 재시도한다. 구조화 출력
                # (backend.py의 structured_outputs)이 JSON 문법 자체는 항상 보장하므로, 예전처럼
                # 페널티를 올렸을 때 따옴표·콜론이 깨지는 부작용은 이제 걱정할 필요가 없다.
                # 프롬프트/스키마는 그대로 재사용하므로 페르소나 산출물이나 프롬프트 텍스트는
                # 전혀 바뀌지 않는다.
                #
                # 단계적 강화(escalation ladder): 1회 재시도로도 못 뚫리는 케이스가 실측됐다
                # (2026-08-07 — Qwen2.5-14B에서도 frequency_penalty=0.6+repetition_penalty=1.3
                # 재시도가 다시 반복루프로 실패하는 사례 확인). validation.repetition_retry.attempts
                # 리스트를 앞에서부터 순서대로 시도하고, 하나라도 성공하면 즉시 멈춘다. 뒤로 갈수록
                # temperature>0 + 고정 seed를 섞어 "greedy가 스스로 못 빠져나오는 결정적 반복"
                # 자체를 깨뜨린다 — 이건 greedy(temperature=0) 100% 재현성 전제에서 벗어나는
                # 의도적 예외이므로 매 시도를 repetition_retry_cases에 전부 기록해 리포트에서
                # 추적 가능하게 한다.
                repetition_retry_attempted = 0
                repetition_retry_succeeded = 0
                repetition_retry_attempts_used = 0
                repetition_retry_last_overrides = None
                attempts_list = repetition_retry_cfg.get("attempts") or []
                is_runaway_repetition = (
                    not parse_result.schema_valid
                    and parse_result.parse_error == "JSON_DECODE_FAILED"
                    and gen.completion_tokens >= repetition_retry_token_threshold
                )
                if repetition_retry_enabled and is_runaway_repetition and attempts_list:
                    repetition_retry_attempted = 1
                    for attempt_idx, attempt_overrides in enumerate(attempts_list, start=1):
                        repetition_retry_attempts_used = attempt_idx
                        repetition_retry_last_overrides = attempt_overrides
                        logger.warning(
                            "반복루프 의심(doc_id=%s stage=%s condition=%s completion_tokens=%d) — "
                            "시도 %d/%d: %s로 국소 재시도",
                            unit.cluster_id, stage, condition_name, gen.completion_tokens,
                            attempt_idx, len(attempts_list), attempt_overrides,
                        )
                        rep_gen = backend.generate_batch(
                            [pairs[unit_idx]],
                            sampling_overrides=attempt_overrides,
                            schema=schema,
                        )[0]
                        rep_parse = schema_validate.parse_and_validate(rep_gen.clean_text, schema)
                        gen = rep_gen
                        parse_result = rep_parse
                        if rep_parse.schema_valid:
                            repetition_retry_succeeded = 1
                            break
                    repetition_retry_cases.append(
                        {
                            "doc_id": unit.cluster_id,
                            "stage": stage,
                            "condition": condition_name,
                            "succeeded": repetition_retry_succeeded,
                            "attempts_used": repetition_retry_attempts_used,
                            "attempts_available": len(attempts_list),
                        }
                    )

                # cause_labels[].label_code에 enum을 걸어도(taxonomy.py::apply_label_code_enum)
                # confidence와의 정합성까지는 디코딩 단계에서 강제되지 않는다 — "점수는 매겼는데
                # 이름은 빈 문자열"(결측 결함, 07_유효성지표_생성량분석_정리.md 6-3절의 Type B)이
                # 여전히 나올 수 있다. Schema 위반과 달리 이전엔 이 패턴이 재시도 없이 그대로
                # 최종 산출물로 확정됐다.
                #
                # 2026-08-12 실측(08_코드수정_실측검증.md)에서 자연어 교정 재시도가 불안정함이
                # 드러났다: 1차 시도("채우거나 비워라")는 전체 cause_labels를 원본 user_prompt부터
                # 통째로 다시 생성하다 보니 정상이던 항목까지 NOT_SCORABLE로 밀어버렸고(cluster_0/3
                # identity_on), 2차 시도(모델 직전 출력을 보여주고 "결함만 고치라" + temperature>0
                # 재시도)조차 cluster_0/on을 오히려 "confidence는 그대로 둔 채 이름만 빈" 더 위험한
                # 상태로 악화시켰다 — 재시도를 더 쓴다고 단조롭게 좋아지지 않는다는 뜻이다.
                #
                # 같은 날 저녁 추가 조사: cluster_0/identity_on에서 재시도가 원래 멀쩡했던 항목까지
                # 훼손한 정확한 사례를 확인했다 — P02가 낸 "환경적 요인 - 날씨 등"은 공식 목록의
                # "기상 등 불가항력"과 명확히 매칭되는데도, 다른 항목의 결함을 고치는 재시도 과정에서
                # 이 멀쩡한 항목까지 빈 값으로 바뀌었다. "결함 항목만 고치고 나머지는 건드리지 말라"는
                # 지시는 자연어일 뿐 강제가 아니었다는 뜻이다. 그래서 이제 재시도 응답을 그대로 쓰지
                # 않고, taxonomy_mod.merge_retry_preserving_good_items()로 **원래 결함이 없던
                # 항목은 cause_id 기준으로 무조건 원본을 그대로 복원**한다 — 결함이 있던 항목만
                # 재시도 결과로 교체되고, 그래도 여전히 결함이면 아래
                # taxonomy_mod.force_resolve_scored_blank_labels()로 결정적으로 마무리한다. 이
                # 보정은 identity_on/off 양쪽에 동일하게 적용되는 구조적 코드라 어느 조건에 유리하게
                # 작용하지 않는다.
                taxonomy_defect_retry_attempted = 0
                taxonomy_defect_retry_succeeded = 0
                taxonomy_defect_force_resolved_items = 0
                if (
                    stage == "persona_03"
                    and parse_result.schema_valid
                    and parse_result.parsed
                    and taxonomy_mod.has_scored_blank_label(parse_result.parsed)
                ):
                    taxonomy_defect_retry_attempted = 1
                    pre_retry_parsed = parse_result.parsed
                    logger.warning(
                        "taxonomy 결측 결함 의심(doc_id=%s condition=%s) — 점수는 있는데 "
                        "label_code가 빈 항목이 있어 1회 교정 재시도",
                        unit.cluster_id, condition_name,
                    )
                    corrective_user_prompt = (
                        pairs[unit_idx][1]
                        + "\n\n[SYSTEM_NOTE] 방금 아래 JSON을 생성했다:\n"
                        + json.dumps(pre_retry_parsed, ensure_ascii=False)
                        + "\n\n이 중 confidence가 NOT_SCORABLE이 아닌데 label_code가 빈 문자열인 "
                        "항목이 결함이다. 다음 규칙으로 그 결함 항목만 고치고, 이미 label_code가 "
                        "채워져 있는 나머지 항목은 절대 바꾸지 말고 그대로 유지하라.\n"
                        "1순위: [CANDIDATE_LABELS] 목록 중 조금이라도 관련 있는 항목이 하나라도 "
                        "있다면 label_code를 그 항목으로 채우고 confidence를 LOW로 낮춰라. "
                        "완벽히 일치하지 않아도 된다 — 비워두는 것보다 가장 가까운 항목을 고르고 "
                        "confidence로 불확실성을 표현하는 쪽을 우선한다.\n"
                        "2순위: 정말로 이 사고와 무관하다고 판단될 때만 label_code를 빈 문자열로, "
                        "confidence를 NOT_SCORABLE로, official_ratio와 analytic_contribution_score를 "
                        "null로 맞춰라.\n"
                        "전체 JSON을 다시 출력하라."
                    )
                    defect_retry_gen = backend.generate_batch(
                        [(system_prompt, corrective_user_prompt)], schema=schema
                    )[0]
                    defect_retry_parse = schema_validate.parse_and_validate(defect_retry_gen.clean_text, schema)
                    if defect_retry_parse.schema_valid and defect_retry_parse.parsed:
                        gen = defect_retry_gen
                        merged_parsed = taxonomy_mod.merge_retry_preserving_good_items(
                            pre_retry_parsed, defect_retry_parse.parsed
                        )
                        parse_result = defect_retry_parse
                        parse_result.parsed = merged_parsed
                        if not taxonomy_mod.has_scored_blank_label(merged_parsed):
                            taxonomy_defect_retry_succeeded = 1

                    if parse_result.parsed and taxonomy_mod.has_scored_blank_label(parse_result.parsed):
                        fixed_parsed, taxonomy_defect_force_resolved_items = taxonomy_mod.force_resolve_scored_blank_labels(
                            parse_result.parsed
                        )
                        parse_result.parsed = fixed_parsed
                        logger.warning(
                            "taxonomy 결측 결함 재시도 실패(doc_id=%s condition=%s) — 항목 %d개를 "
                            "confidence=NOT_SCORABLE로 코드에서 강제 확정",
                            unit.cluster_id, condition_name, taxonomy_defect_force_resolved_items,
                        )

                    taxonomy_defect_retry_cases.append(
                        {
                            "doc_id": unit.cluster_id,
                            "condition": condition_name,
                            "succeeded": taxonomy_defect_retry_succeeded,
                            "force_resolved_items": taxonomy_defect_force_resolved_items,
                        }
                    )

                # persona_02_output_schema.json은 causes[]의 fact_ids/evidence_ids를 required로
                # 강제하지 않고, support_level과의 교차 제약(if/then)도 xgrammar가 무시함이 실측
                # 확인됐다(위 persona_03 블록·08_코드수정_실측검증.md 참고) — 그래서 "support_level=
                # HIGH인데 근거 인용은 0개"인 원인이 스키마를 그대로 통과한다(cluster_0의 "환경적
                # 요인 - 날씨 등" 사례). persona_03과 완전히 같은 3단계(프롬프트 지시 추가 →
                # 1회 교정 재시도 + 원래 정상이던 원인 보존 → 그래도 안 되면 코드가 결정적으로
                # support_level을 INSUFFICIENT로 하향)로 대응한다.
                p02_evidence_retry_attempted = 0
                p02_evidence_retry_succeeded = 0
                p02_evidence_force_downgraded = 0
                if (
                    stage == "persona_02"
                    and parse_result.schema_valid
                    and parse_result.parsed
                    and has_unsupported_high_confidence_cause(parse_result.parsed)
                ):
                    p02_evidence_retry_attempted = 1
                    pre_retry_parsed = parse_result.parsed
                    logger.warning(
                        "P02 미근거 고신뢰 원인 의심(doc_id=%s condition=%s) — support_level은 "
                        "HIGH/MEDIUM인데 fact_ids/evidence_ids가 둘 다 빈 원인이 있어 1회 교정 재시도",
                        unit.cluster_id, condition_name,
                    )
                    corrective_user_prompt = (
                        pairs[unit_idx][1]
                        + "\n\n[SYSTEM_NOTE] 방금 아래 JSON을 생성했다:\n"
                        + json.dumps(pre_retry_parsed, ensure_ascii=False)
                        + "\n\n이 중 support_level이 HIGH나 MEDIUM인데 fact_ids와 evidence_ids가 "
                        "둘 다 비어 있는 원인이 결함이다. 다음 규칙으로 그 결함 원인만 고치고, 이미 "
                        "fact_ids/evidence_ids가 채워져 있거나 support_level이 낮은 나머지 원인은 "
                        "절대 바꾸지 말고 그대로 유지하라.\n"
                        "1순위: 페르소나 1(KMST-P01) 결과의 facts[]/evidence[] 중 실제로 이 원인을 "
                        "뒷받침하는 항목이 있다면 그 fact_id/evidence_id를 채워라.\n"
                        "2순위: 정말로 연결할 근거가 없다면 support_level을 INSUFFICIENT로 낮춰라.\n"
                        "전체 JSON을 다시 출력하라."
                    )
                    defect_retry_gen = backend.generate_batch(
                        [(system_prompt, corrective_user_prompt)], schema=schema
                    )[0]
                    defect_retry_parse = schema_validate.parse_and_validate(defect_retry_gen.clean_text, schema)
                    if defect_retry_parse.schema_valid and defect_retry_parse.parsed:
                        gen = defect_retry_gen
                        merged_parsed = merge_p02_retry_preserving_good_causes(
                            pre_retry_parsed, defect_retry_parse.parsed
                        )
                        parse_result = defect_retry_parse
                        parse_result.parsed = merged_parsed
                        if not has_unsupported_high_confidence_cause(merged_parsed):
                            p02_evidence_retry_succeeded = 1

                    if parse_result.parsed and has_unsupported_high_confidence_cause(parse_result.parsed):
                        fixed_parsed, p02_evidence_force_downgraded = force_downgrade_unsupported_causes(
                            parse_result.parsed
                        )
                        parse_result.parsed = fixed_parsed
                        logger.warning(
                            "P02 미근거 고신뢰 원인 재시도 실패(doc_id=%s condition=%s) — 원인 %d개를 "
                            "support_level=INSUFFICIENT로 코드에서 강제 확정",
                            unit.cluster_id, condition_name, p02_evidence_force_downgraded,
                        )

                    p02_evidence_retry_cases.append(
                        {
                            "doc_id": unit.cluster_id,
                            "condition": condition_name,
                            "succeeded": p02_evidence_retry_succeeded,
                            "force_downgraded": p02_evidence_force_downgraded,
                        }
                    )

                if not parse_result.schema_valid:
                    save_failures.append({"doc_id": unit.cluster_id, "stage": stage, "condition": condition_name})

                previous_outputs[unit.cluster_id] = parse_result.parsed

                raw_path = raw_dir / unit.cluster_id / stage
                raw_path.mkdir(parents=True, exist_ok=True)
                (raw_path / f"{condition_name}.json").write_text(
                    json.dumps(
                        {
                            "raw_text": gen.raw_text,
                            "system_prompt": system_prompt,
                            "prompt_tokens": gen.prompt_tokens,
                            "completion_tokens": gen.completion_tokens,
                            "latency_sec": gen.latency_sec,
                            "think_block_stripped": gen.think_block_stripped,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                final_labels = _extract_stage_summary(stage, parse_result.parsed, known_label_codes)
                flat_row = {
                    "doc_id": unit.cluster_id,
                    "n_docs": unit.n_docs,
                    "stage": stage,
                    "condition": condition_name,
                    "schema_valid": parse_result.schema_valid,
                    "parse_retry": parse_retry_flag,
                    "repetition_retry_attempted": repetition_retry_attempted,
                    "repetition_retry_succeeded": repetition_retry_succeeded,
                    "repetition_retry_attempts_used": repetition_retry_attempts_used,
                    "repetition_retry_overrides": json.dumps(repetition_retry_last_overrides, ensure_ascii=False)
                    if repetition_retry_attempted
                    else None,
                    "taxonomy_defect_retry_attempted": taxonomy_defect_retry_attempted,
                    "taxonomy_defect_retry_succeeded": taxonomy_defect_retry_succeeded,
                    "taxonomy_defect_force_resolved_items": taxonomy_defect_force_resolved_items,
                    "p02_evidence_retry_attempted": p02_evidence_retry_attempted,
                    "p02_evidence_retry_succeeded": p02_evidence_retry_succeeded,
                    "p02_evidence_force_downgraded": p02_evidence_force_downgraded,
                    "think_block_stripped": gen.think_block_stripped,
                    "prompt_tokens": gen.prompt_tokens,
                    "completion_tokens": gen.completion_tokens,
                    "latency_sec": gen.latency_sec,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "raw_text": gen.raw_text,
                    "parsed": parse_result.parsed,
                    "parse_error": parse_result.parse_error,
                    "extra": json.dumps(parse_result.schema_errors, ensure_ascii=False),
                    **final_labels,
                }
                rows.append(flat_row)

                parsed_path = parsed_dir / unit.cluster_id / stage
                parsed_path.mkdir(parents=True, exist_ok=True)
                (parsed_path / f"{condition_name}.json").write_text(
                    json.dumps(
                        {
                            "parsed": parse_result.parsed,
                            "schema_valid": parse_result.schema_valid,
                            "schema_errors": parse_result.schema_errors,
                            "flat_row": flat_row,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

    labels_df = pd.DataFrame(rows)
    flat_dir.mkdir(parents=True, exist_ok=True)
    labels_df.drop(columns=["parsed"], errors="ignore").to_parquet(flat_dir / "labels.parquet", index=False)
    labels_df.drop(columns=["parsed"], errors="ignore").to_csv(flat_dir / "labels.csv", index=False)
    logger.info("flat/labels.parquet 저장 완료: %d행", len(labels_df))

    if skip_determinism:
        # 이미 저장된 결정성 검사 결과를 그대로 재사용한다(예: 일부 군집만 재생성하는 부분 재실행).
        # persona_01 프롬프트는 이 재실행에서 변경되지 않았으므로 재검증할 필요가 없다.
        existing = metrics_dir / "determinism_check.json"
        if existing.exists():
            determinism_result = json.loads(existing.read_text(encoding="utf-8"))
            logger.info("결정성 검사 스킵 — 기존 결과 재사용: %s", existing)
        else:
            determinism_result = {"skipped": True, "reason": "이전 결정성 검사 결과 없음"}
            logger.warning("결정성 검사 스킵 요청됐지만 기존 결과가 없어 빈 값으로 대체합니다.")
    else:
        if determinism_pairs is None:
            # 체크포인트로 전부 스킵된 경우(재실행) — persona_01/identity_on을 새로 조립해 재확인한다.
            stage0 = config.chain_order[0]
            sp0 = stage_prompts[stage0]
            determinism_pairs = []
            determinism_ids = []
            for unit in units:
                legal_context = legal_retrieval.retrieve(" ".join(unit.freq_keywords), legal_chunks)
                up = prompt_builder.build_cluster_user_prompt(
                    cluster_id=unit.cluster_id,
                    n_docs=unit.n_docs,
                    freq_keywords=unit.freq_keywords,
                    distinctive_keywords=unit.distinctive_keywords,
                    sample_sentences=unit.sample_sentences,
                    previous_output=None,
                    legal_context=legal_context,
                )
                sys_p = prompt_builder.build_cluster_system_prompt(
                    sp0, use_identity=True, schema=schemas[stage0], append_no_think=append_no_think
                )
                determinism_pairs.append((sys_p, up))
                determinism_ids.append(unit.cluster_id)

        determinism_result = determinism_mod.run_determinism_check(
            unit_ids=determinism_ids,
            pairs=determinism_pairs,
            backend=backend,
            sample_size=config.data["determinism_check"]["sample_size"],
            metrics_dir=metrics_dir,
            logger=logger,
            stage_tested=config.chain_order[0],
            schema=schemas[config.chain_order[0]],
        )

    all_metrics = metrics_mod.compute_all_metrics(labels_df, metrics_dir)

    finished_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "config_sha256": config.sha256,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_sec": time.time() - t0,
        "unit": UNIT_LABEL,
        "doc_count": unit_count,
        "failure_count": len(save_failures),
        "failures": save_failures,
        "repetition_retry_cases": repetition_retry_cases,
        "taxonomy_defect_retry_cases": taxonomy_defect_retry_cases,
        "p02_evidence_retry_cases": p02_evidence_retry_cases,
        "identity_prompt_token_diff": token_diff_report,
        "limit": limit,
    }
    (artifacts_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report_path = report_mod.generate_report(
        output_root=output_root,
        config=config,
        labels_df=labels_df,
        determinism_result=determinism_result,
        all_metrics=all_metrics,
        doc_count=unit_count,
        manifest=manifest,
        unit_label=UNIT_LABEL,
    )
    logger.info("리포트 생성 완료: %s", report_path)
    return report_path
