"""6-2(유효성), 6-5(ablation 비교) 지표 산출. 6-3/6-4(3-rater 합의)는 페르소나 체인이
병렬 rater 구조가 아니므로 구조적으로 해당 없음 — 생성하지 않는다 (계획 문서 참고)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

REFUSAL_MARKERS = ["죄송", "답변할 수 없", "i cannot", "i can't", "as an ai"]


def bootstrap_ci(values: np.ndarray, statistic_fn, n_boot: int = 1000, alpha: float = 0.05, seed: int = 0):
    if len(values) == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    n = len(values)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        boots[i] = statistic_fn(values[idx])
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return (float(lo), float(hi))


def schema_validity_metrics(df: pd.DataFrame) -> dict:
    out = {}
    for (stage, condition), group in df.groupby(["stage", "condition"]):
        n = len(group)
        schema_valid_rate = float(group["schema_valid"].mean()) if n else float("nan")
        semantic_valid_rate = float(group["semantic_valid"].mean()) if ("semantic_valid" in group and n) else float("nan")
        parse_failure_rate = float((~group["schema_valid"] & group["parsed"].isna()).mean()) if n else float("nan")
        think_leak_rate = float(group["think_block_stripped"].mean()) if n else float("nan")
        refusal_rate = float(
            group["raw_text"].fillna("").str.lower().apply(
                lambda t: any(m in t.lower() for m in REFUSAL_MARKERS)
            ).mean()
        ) if n else float("nan")
        mean_completion_tokens = float(group["completion_tokens"].mean()) if n else float("nan")
        out[f"{stage}__{condition}"] = {
            "n": n,
            "schema_validity_rate": schema_valid_rate,
            "semantic_validity_rate": semantic_valid_rate,
            "parse_failure_rate": parse_failure_rate,
            "think_leak_rate": think_leak_rate,
            "refusal_rate": refusal_rate,
            "mean_completion_tokens": mean_completion_tokens,
        }
    return out


def _mcnemar(on_bool: np.ndarray, off_bool: np.ndarray) -> dict:
    b = int(np.sum(on_bool & ~off_bool))  # on=True, off=False
    c = int(np.sum(~on_bool & off_bool))  # on=False, off=True
    if b + c == 0:
        stat, p = 0.0, 1.0
    else:
        stat = (abs(b - c) - 1) ** 2 / (b + c)
        p = float(stats.chi2.sf(stat, df=1))
    odds_ratio = (b / c) if c > 0 else (float("inf") if b > 0 else float("nan"))
    return {"b_on_only": b, "c_off_only": c, "statistic": float(stat), "p_value": p, "odds_ratio": odds_ratio}


def _paired_cliffs_delta(on_vals: np.ndarray, off_vals: np.ndarray) -> float:
    n_pos = int(np.sum(on_vals > off_vals))
    n_neg = int(np.sum(on_vals < off_vals))
    n_total = len(on_vals)
    if n_total == 0:
        return float("nan")
    return (n_pos - n_neg) / n_total


def _cohens_kappa(a: list, b: list) -> float:
    labels = sorted(set(a) | set(b))
    if len(labels) < 2:
        return float("nan")
    idx = {lab: i for i, lab in enumerate(labels)}
    n = len(a)
    mat = np.zeros((len(labels), len(labels)))
    for x, y in zip(a, b):
        mat[idx[x], idx[y]] += 1
    po = np.trace(mat) / n
    row_marg = mat.sum(axis=1) / n
    col_marg = mat.sum(axis=0) / n
    pe = float(np.sum(row_marg * col_marg))
    if pe == 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


def _js_divergence(a_labels: list, b_labels: list) -> float:
    from scipy.spatial.distance import jensenshannon

    cats = sorted(set(a_labels) | set(b_labels))
    if not cats:
        return float("nan")
    a_counts = np.array([a_labels.count(c) for c in cats], dtype=float)
    b_counts = np.array([b_labels.count(c) for c in cats], dtype=float)
    a_p = a_counts / a_counts.sum() if a_counts.sum() else a_counts
    b_p = b_counts / b_counts.sum() if b_counts.sum() else b_counts
    val = jensenshannon(a_p, b_p, base=2)
    return float(val) if not math.isnan(val) else 0.0


def ablation_comparison(df: pd.DataFrame) -> dict:
    result: dict = {}
    for stage in sorted(df["stage"].unique()):
        stage_df = df[df["stage"] == stage]
        on = stage_df[stage_df["condition"] == "identity_on"].set_index("doc_id")
        off = stage_df[stage_df["condition"] == "identity_off"].set_index("doc_id")
        common = sorted(set(on.index) & set(off.index))
        if not common:
            continue
        on = on.loc[common]
        off = off.loc[common]

        stage_result: dict = {"n_paired_docs": len(common)}

        # 유효성 차이: McNemar + odds ratio
        stage_result["schema_validity_mcnemar"] = _mcnemar(
            on["schema_valid"].to_numpy(dtype=bool), off["schema_valid"].to_numpy(dtype=bool)
        )
        stage_result["schema_validity_rate_on"] = float(on["schema_valid"].mean())
        stage_result["schema_validity_rate_off"] = float(off["schema_valid"].mean())

        # 출력 길이 차이: Wilcoxon + paired Cliff's delta
        on_tok = on["completion_tokens"].to_numpy(dtype=float)
        off_tok = off["completion_tokens"].to_numpy(dtype=float)
        try:
            wstat, wp = stats.wilcoxon(on_tok, off_tok)
        except ValueError:
            wstat, wp = float("nan"), float("nan")
        stage_result["completion_tokens_wilcoxon"] = {"statistic": float(wstat), "p_value": float(wp)}
        stage_result["completion_tokens_cliffs_delta_paired"] = _paired_cliffs_delta(on_tok, off_tok)
        stage_result["completion_tokens_mean_on"] = float(np.mean(on_tok)) if len(on_tok) else float("nan")
        stage_result["completion_tokens_mean_off"] = float(np.mean(off_tok)) if len(off_tok) else float("nan")

        if stage == "persona_03":
            on_labels = on["root_cause_primary"].fillna("NULL").tolist()
            off_labels = off["root_cause_primary"].fillna("NULL").tolist()

            change_mask = [a != b for a, b in zip(on_labels, off_labels)]
            stage_result["label_change_rate"] = float(np.mean(change_mask)) if change_mask else float("nan")

            transition: dict[str, dict[str, int]] = {}
            for a, b in zip(on_labels, off_labels):
                transition.setdefault(a, {}).setdefault(b, 0)
                transition[a][b] += 1
            stage_result["root_cause_primary_transition_on_to_off"] = transition

            stage_result["root_cause_primary_js_divergence"] = _js_divergence(on_labels, off_labels)
            stage_result["root_cause_primary_cohens_kappa_on_vs_off"] = _cohens_kappa(on_labels, off_labels)

            def _change_rate_stat(idx_vals):
                return float(np.mean(idx_vals))

            change_arr = np.array(change_mask, dtype=float)
            stage_result["label_change_rate_bootstrap_ci95"] = bootstrap_ci(change_arr, _change_rate_stat)

        result[stage] = stage_result
    return result


def gold_evaluation_stub() -> dict:
    return {"evaluated": False, "reason": "gold set이 존재하지 않아 정확도 평가를 스킵함 (config.paths.gold_set=null)"}


def _sanitize_non_finite(obj):
    """NaN/Infinity는 표준 JSON 토큰이 아니므로 저장 전에 null로 치환한다."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize_non_finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_non_finite(v) for v in obj]
    return obj


def _dump_json(obj, path: Path) -> None:
    path.write_text(
        json.dumps(_sanitize_non_finite(obj), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def compute_all_metrics(labels_df: pd.DataFrame, metrics_dir: Path) -> dict:
    metrics_dir.mkdir(parents=True, exist_ok=True)

    validity = schema_validity_metrics(labels_df)
    _dump_json(validity, metrics_dir / "schema_validity.json")

    ablation = ablation_comparison(labels_df)
    _dump_json(ablation, metrics_dir / "ablation_comparison.json")

    gold = gold_evaluation_stub()
    (metrics_dir / "gold_evaluation.json").write_text(
        json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {"schema_validity": validity, "ablation_comparison": ablation, "gold_evaluation": gold}
