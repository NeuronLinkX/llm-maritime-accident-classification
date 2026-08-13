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
from step4 import semantic_validate
from step4 import slack_notify
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
    # persona_03 (최종 레이블)
    "accident_type",
    "root_cause_primary",
    "root_cause_secondary",
    "n_causes",
    "analytic_contribution",
    "official_ratio",
    "taxonomy_valid",
    "semantic_valid",
    "semantic_error_count",
    "semantic_error_codes",
    "n_taxonomy_ok_items",
    "n_taxonomy_total_items",
    "n_empty_label_code_items",
    "n_unknown_label_code_items",
]


def _build_runtime_schema(stage: str, base_schema: dict, known_label_codes: set[str]) -> dict:
    schema = json.loads(json.dumps(base_schema))
    if stage != "persona_03":
        return schema

    cause_labels = schema.get("properties", {}).get("cause_labels", {})
    items = cause_labels.get("items", {})
    properties = items.get("properties", {})
    label_code_schema = properties.get("label_code")
    if isinstance(label_code_schema, dict):
        label_code_schema["enum"] = sorted(known_label_codes)
        label_code_schema["minLength"] = 1
    return schema


def _extract_persona_01_summary(parsed: dict) -> dict:
    return {
        "n_facts": len(parsed.get("facts") or []),
        "n_evidence": len(parsed.get("evidence") or []),
        "n_actors": len(parsed.get("actors") or []),
        "n_vessels": len(parsed.get("vessels") or []),
        "n_missing_information": len(parsed.get("missing_information") or []),
        "handoff_status": parsed.get("handoff_status"),
    }


def _extract_persona_02_summary(parsed: dict) -> dict:
    return {
        "n_cause_candidates": len(parsed.get("causes") or []),
        "n_alternative_causes": len(parsed.get("alternative_causes") or []),
        "n_legal_conflicts": len(parsed.get("legal_conflicts") or []),
        "n_unresolved_issues": len(parsed.get("unresolved_issues") or []),
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
    taxonomy_valid = (
        all(code in known_label_codes for code in all_codes) if (known_label_codes and all_codes) else None
    )
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
    notifier = slack_notify.SlackNotifier.from_env(logger)
    batch_mode = bool(config.data["generation"].get("batch_mode", False))
    if batch_mode:
        logger.warning(
            "generation.batch_mode=True — 처리량을 위해 vLLM 배치 실행을 사용합니다. "
            "출력 품질은 유지되지만 byte-level 결정성은 약해질 수 있습니다."
        )

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
            schema = _build_runtime_schema(stage, schemas[stage], known_label_codes)

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
                    sample_doc_ids=unit.sample_doc_ids,
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
            gen_results = backend.generate_batch(pairs, schema=schema, batch_mode=batch_mode)

            for unit_idx, (unit, gen) in enumerate(zip(pending_units, gen_results)):
                parse_result = schema_validate.parse_and_validate(gen.clean_text, schema)

                if not parse_result.schema_valid and retry_on_parse_error:
                    corrective_user_prompt = pairs[unit_idx][1] + "\n\n[SYSTEM_NOTE] 출력은 JSON 객체만 허용된다. 다시 JSON만 출력하라."
                    retry_gen = backend.generate_batch(
                        [(system_prompt, corrective_user_prompt)], schema=schema, batch_mode=False
                    )[0]
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
                        notifier.record_retry(
                            cluster_id=unit.cluster_id,
                            stage=stage,
                            condition=condition_name,
                            attempt_idx=attempt_idx,
                            attempts_total=len(attempts_list),
                            completion_tokens=gen.completion_tokens,
                        )
                        rep_gen = backend.generate_batch(
                            [pairs[unit_idx]],
                            sampling_overrides=attempt_overrides,
                            schema=schema,
                            batch_mode=False,
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

                if not parse_result.schema_valid:
                    save_failures.append({"doc_id": unit.cluster_id, "stage": stage, "condition": condition_name})
                    notifier.record_error(
                        cluster_id=unit.cluster_id,
                        stage=stage,
                        condition=condition_name,
                        parse_error=parse_result.parse_error,
                        schema_errors=parse_result.schema_errors,
                        semantic_errors=[],
                    )

                previous_outputs[unit.cluster_id] = parse_result.parsed

                allowed_source_ids = {f"S{i:02d}" for i in range(1, len(unit.sample_sentences) + 1)}
                semantic_result = semantic_validate.validate_stage_output(
                    stage=stage,
                    parsed=parse_result.parsed,
                    allowed_source_ids=allowed_source_ids,
                    known_label_codes=known_label_codes,
                )

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
                final_labels.update(
                    {
                        "semantic_valid": semantic_result.valid,
                        "semantic_error_count": semantic_result.counters.get("semantic_error_count"),
                        "semantic_error_codes": json.dumps(semantic_result.error_codes, ensure_ascii=False),
                        "n_taxonomy_ok_items": semantic_result.counters.get("n_taxonomy_ok_items"),
                        "n_taxonomy_total_items": semantic_result.counters.get("n_taxonomy_total_items"),
                        "n_empty_label_code_items": semantic_result.counters.get("n_empty_label_code_items"),
                        "n_unknown_label_code_items": semantic_result.counters.get("n_unknown_label_code_items"),
                    }
                )
                flat_row = {
                    "doc_id": unit.cluster_id,
                    "n_docs": unit.n_docs,
                    "stage": stage,
                    "condition": condition_name,
                    "schema_valid": parse_result.schema_valid,
                    "semantic_valid": semantic_result.valid,
                    "parse_retry": parse_retry_flag,
                    "repetition_retry_attempted": repetition_retry_attempted,
                    "repetition_retry_succeeded": repetition_retry_succeeded,
                    "repetition_retry_attempts_used": repetition_retry_attempts_used,
                    "repetition_retry_overrides": json.dumps(repetition_retry_last_overrides, ensure_ascii=False)
                    if repetition_retry_attempted
                    else None,
                    "think_block_stripped": gen.think_block_stripped,
                    "prompt_tokens": gen.prompt_tokens,
                    "completion_tokens": gen.completion_tokens,
                    "latency_sec": gen.latency_sec,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "raw_text": gen.raw_text,
                    "parsed": parse_result.parsed,
                    "parse_error": parse_result.parse_error,
                    "extra": json.dumps(
                        {
                            "schema_errors": parse_result.schema_errors,
                            "semantic_errors": semantic_result.errors,
                        },
                        ensure_ascii=False,
                    ),
                    **final_labels,
                }
                rows.append(flat_row)
                if stage == "persona_03":
                    notifier.record_cluster_result(unit.cluster_id, condition_name, flat_row)
                if semantic_result.errors:
                    notifier.record_error(
                        cluster_id=unit.cluster_id,
                        stage=stage,
                        condition=condition_name,
                        parse_error=parse_result.parse_error,
                        schema_errors=parse_result.schema_errors,
                        semantic_errors=semantic_result.errors,
                    )

                parsed_path = parsed_dir / unit.cluster_id / stage
                parsed_path.mkdir(parents=True, exist_ok=True)
                (parsed_path / f"{condition_name}.json").write_text(
                    json.dumps(
                        {
                            "parsed": parse_result.parsed,
                            "schema_valid": parse_result.schema_valid,
                            "schema_errors": parse_result.schema_errors,
                            "semantic_valid": semantic_result.valid,
                            "semantic_error_codes": semantic_result.error_codes,
                            "semantic_errors": semantic_result.errors,
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
                    sample_doc_ids=unit.sample_doc_ids,
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
    notifier.record_run_complete(
        doc_count=unit_count,
        failure_count=len(save_failures),
        started_at=started_at,
        finished_at=finished_at,
        report_path=str(report_path),
    )
    logger.info("리포트 생성 완료: %s", report_path)
    return report_path
