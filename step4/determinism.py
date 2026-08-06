"""동일 프롬프트를 2회 실행해 greedy decoding의 바이트 단위 재현성을 확인한다.

DGX Spark는 ECC 메모리가 없으므로 이 확인을 생략하지 않는다 (지시서 6-1).
호출자가 이미 조립한 (system_prompt, user_prompt) 쌍과 그 id 목록을 그대로 받는다 —
문서 단위든 군집 단위든 상관없이 재사용 가능하도록 일반화했다.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path


def run_determinism_check(
    *,
    unit_ids: list[str],
    pairs: list[tuple[str, str]],
    backend,
    sample_size: int,
    metrics_dir: Path,
    logger: logging.Logger,
    stage_tested: str,
    condition_tested: str = "identity_on",
) -> dict:
    n = min(sample_size, len(pairs))
    sample_ids = unit_ids[:n]
    sample_pairs = pairs[:n]
    if n < sample_size:
        logger.warning(
            "determinism_check 표본 크기를 %d에서 %d로 축소했습니다 (대상 수 부족).",
            sample_size,
            n,
        )

    run1 = backend.generate_batch(sample_pairs)
    run2 = backend.generate_batch(sample_pairs)

    byte_identical = []
    label_identical = []
    mismatch_cases = []
    for uid, r1, r2 in zip(sample_ids, run1, run2):
        is_byte_identical = r1.raw_text == r2.raw_text
        byte_identical.append(is_byte_identical)
        is_label_identical = r1.clean_text.strip() == r2.clean_text.strip()
        label_identical.append(is_label_identical)
        if not is_byte_identical:
            mismatch_cases.append(uid)

    result = {
        "stage_tested": stage_tested,
        "condition_tested": condition_tested,
        "sample_size_requested": sample_size,
        "sample_size_used": n,
        "byte_identical_rate": (sum(byte_identical) / n) if n else float("nan"),
        "label_identical_rate": (sum(label_identical) / n) if n else float("nan"),
        "mismatch_cases": mismatch_cases,
    }

    metrics_dir.mkdir(parents=True, exist_ok=True)
    dumpable = {
        k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in result.items()
    }
    (metrics_dir / "determinism_check.json").write_text(
        json.dumps(dumpable, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result["byte_identical_rate"] < 1.0:
        logger.warning(
            "determinism_check: byte_identical_rate=%.3f (100%% 미달 — vLLM 배치 스케줄링 비결정성 또는 "
            "하드웨어 요인일 수 있음, report에 명시할 것)",
            result["byte_identical_rate"],
        )
    else:
        logger.info("determinism_check: byte_identical_rate=100%% (기대값 충족)")
    return result
