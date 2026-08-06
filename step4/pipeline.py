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

    persona_assets = assets_mod.discover_persona_assets(persona_dir, logger)
    schemas = {a.persona_id: json.loads(a.schema_path.read_text(encoding="utf-8")) for a in persona_assets}

    stage_prompts = {}
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


def real_run(config: Config, logger: logging.Logger, limit: int | None = None) -> Path:
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
    rows: list[dict] = []
    retry_on_parse_error = config.data["validation"].get("retry_on_parse_error", 0)
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
                stage_prompt, use_identity=use_identity, schema=schema
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
            gen_results = backend.generate_batch(pairs)

            for unit_idx, (unit, gen) in enumerate(zip(pending_units, gen_results)):
                parse_result = schema_validate.parse_and_validate(gen.clean_text, schema)

                if not parse_result.schema_valid and retry_on_parse_error:
                    corrective_user_prompt = pairs[unit_idx][1] + "\n\n[SYSTEM_NOTE] 출력은 JSON 객체만 허용된다. 다시 JSON만 출력하라."
                    retry_gen = backend.generate_batch([(system_prompt, corrective_user_prompt)])[0]
                    retry_parse = schema_validate.parse_and_validate(retry_gen.clean_text, schema)
                    gen = retry_gen
                    parse_result = retry_parse
                    parse_retry_flag = 1
                else:
                    parse_retry_flag = 0

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
            sys_p = prompt_builder.build_cluster_system_prompt(sp0, use_identity=True, schema=schemas[stage0])
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
