"""benchmark_report.md + figures 생성."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager

# 시스템에 이미 설치된 Noto Sans CJK KR을 등록해 그림 속 한글 글리프 누락(□□□)을 막는다.
_CJK_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
if Path(_CJK_FONT_PATH).exists():
    font_manager.fontManager.addfont(_CJK_FONT_PATH)
    plt.rcParams["font.family"] = "Noto Sans CJK KR"
plt.rcParams["axes.unicode_minus"] = False


def _fig_label_distribution(labels_df: pd.DataFrame, out_path: Path) -> bool:
    p03 = labels_df[labels_df["stage"] == "persona_03"].copy()
    if p03.empty:
        return False
    p03["root_cause_primary"] = p03["root_cause_primary"].fillna("NULL")
    pivot = p03.groupby(["condition", "root_cause_primary"]).size().unstack(fill_value=0)
    if pivot.empty:
        return False
    fig, ax = plt.subplots(figsize=(8, 5))
    pivot.plot(kind="bar", stacked=True, ax=ax)
    ax.set_title("조건별 root_cause_primary 분포 (persona_03)")
    ax.set_xlabel("condition")
    ax.set_ylabel("문서 수")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return True


def _fig_transition_heatmap(ablation_metrics: dict, out_path: Path) -> bool:
    p03 = ablation_metrics.get("persona_03", {})
    transition = p03.get("root_cause_primary_transition_on_to_off")
    if not transition:
        return False
    on_labels = sorted(transition.keys())
    off_labels = sorted({b for row in transition.values() for b in row.keys()})
    if not on_labels or not off_labels:
        return False
    mat = [[transition.get(a, {}).get(b, 0) for b in off_labels] for a in on_labels]
    fig, ax = plt.subplots(figsize=(max(5, len(off_labels) * 0.6), max(4, len(on_labels) * 0.6)))
    im = ax.imshow(mat, cmap="Blues")
    ax.set_xticks(range(len(off_labels)))
    ax.set_xticklabels(off_labels, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(on_labels)))
    ax.set_yticklabels(on_labels, fontsize=7)
    ax.set_xlabel("identity_off")
    ax.set_ylabel("identity_on")
    ax.set_title("root_cause_primary 전이 행렬 (on -> off)")
    for i in range(len(on_labels)):
        for j in range(len(off_labels)):
            ax.text(j, i, mat[i][j], ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return True


def _fmt(x, nd=3) -> str:
    if x is None:
        return "N/A"
    if isinstance(x, float):
        if x != x:  # NaN
            return "N/A"
        return f"{x:.{nd}f}"
    return str(x)


def generate_report(
    *,
    output_root: Path,
    config,
    labels_df: pd.DataFrame,
    determinism_result: dict,
    all_metrics: dict,
    doc_count: int,
    manifest: dict,
    unit_label: str = "문서",
) -> Path:
    report_dir = output_root / "report"
    figures_dir = report_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    has_dist_fig = _fig_label_distribution(labels_df, figures_dir / "label_distribution.png")
    has_trans_fig = _fig_transition_heatmap(
        all_metrics["ablation_comparison"], figures_dir / "root_cause_primary_transition.png"
    )

    lines: list[str] = []
    lines.append("# STEP4 페르소나 identity ablation — 벤치마크 리포트\n")

    lines.append("## 1. 실험 조건 요약\n")
    lines.append(f"- 모델: {config.data['model']['id']} (`{config.data['model']['path']}`)")
    lines.append("- 디코딩: greedy (temperature=0.0), 조건당 1회 실행")
    lines.append(
        "- 페르소나 구조: persona_01(사실추출) -> persona_02(인과.법령검증) -> persona_03(레이블링) "
        f"순차 체인 (병렬 3-rater 아님), 분석 단위: {unit_label}"
    )
    lines.append("- 조건: identity_on / identity_off (2개)")
    lines.append(f"- {unit_label} 수: {doc_count}")
    lines.append(f"- 총 실행(호출) 수: {doc_count} x 3단계 x 2조건 = {doc_count * 6}")
    lines.append(f"- config sha256: `{config.sha256}`\n")

    lines.append("## 2. 결정성 확인 결과\n")
    byte_rate = determinism_result.get("byte_identical_rate")
    lines.append(f"- 검증 대상: stage={determinism_result.get('stage_tested')}, condition={determinism_result.get('condition_tested')}")
    lines.append(f"- 표본 수: {determinism_result.get('sample_size_used')} (요청값 {determinism_result.get('sample_size_requested')})")
    lines.append(f"- byte_identical_rate: {_fmt(byte_rate)}")
    lines.append(f"- label_identical_rate: {_fmt(determinism_result.get('label_identical_rate'))}")
    if byte_rate is not None and byte_rate < 1.0:
        lines.append(
            f"- **100% 미달** — mismatch 문서: {determinism_result.get('mismatch_cases')}. "
            "greedy decoding임에도 불일치가 발생했다면 vLLM 배치 스케줄링 비결정성 또는 "
            "DGX Spark(ECC 미탑재) 하드웨어 요인일 수 있다."
        )
    else:
        lines.append("- 100% 일치 — greedy decoding 기대값 충족.")
    lines.append("")

    lines.append("## 3. 조건별 유효성 지표\n")
    lines.append("| stage | condition | n | schema_validity_rate | parse_failure_rate | think_leak_rate | refusal_rate | mean_completion_tokens |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for key, m in all_metrics["schema_validity"].items():
        stage, condition = key.split("__")
        lines.append(
            f"| {stage} | {condition} | {m['n']} | {_fmt(m['schema_validity_rate'])} | "
            f"{_fmt(m['parse_failure_rate'])} | {_fmt(m['think_leak_rate'])} | {_fmt(m['refusal_rate'])} | "
            f"{_fmt(m['mean_completion_tokens'], 1)} |"
        )
    lines.append("")

    lines.append("## 4. Ablation 결론 (identity_on vs identity_off)\n")
    ablation = all_metrics["ablation_comparison"]
    p03 = ablation.get("persona_03", {})
    if p03:
        mcnemar = p03.get("schema_validity_mcnemar", {})
        lines.append(
            f"- (persona_03) schema_validity: on={_fmt(p03.get('schema_validity_rate_on'))} vs "
            f"off={_fmt(p03.get('schema_validity_rate_off'))}, McNemar p={_fmt(mcnemar.get('p_value'))}, "
            f"odds_ratio={_fmt(mcnemar.get('odds_ratio'))}"
        )
        lines.append(
            f"- (persona_03) root_cause_primary 변경률: {_fmt(p03.get('label_change_rate'))} "
            f"(95% bootstrap CI {p03.get('label_change_rate_bootstrap_ci95')})"
        )
        lines.append(f"- (persona_03) root_cause_primary JS divergence(on vs off): {_fmt(p03.get('root_cause_primary_js_divergence'))}")
        lines.append(f"- (persona_03) root_cause_primary Cohen's kappa(on vs off): {_fmt(p03.get('root_cause_primary_cohens_kappa_on_vs_off'))}")
        wil = p03.get("completion_tokens_wilcoxon", {})
        lines.append(
            f"- (persona_03) completion_tokens: on={_fmt(p03.get('completion_tokens_mean_on'), 1)} vs "
            f"off={_fmt(p03.get('completion_tokens_mean_off'), 1)}, Wilcoxon p={_fmt(wil.get('p_value'))}, "
            f"paired Cliff's delta={_fmt(p03.get('completion_tokens_cliffs_delta_paired'))}"
        )
    else:
        lines.append("- persona_03 단계에서 identity_on/off 대응쌍이 부족해 비교를 산출하지 못했습니다.")
    lines.append("")

    lines.append("## 5. 레이블 전이 분석 (identity_on -> identity_off)\n")
    if has_trans_fig:
        lines.append("![transition](figures/root_cause_primary_transition.png)\n")
    transition = p03.get("root_cause_primary_transition_on_to_off")
    if transition:
        lines.append("| on \\ off | " + " | ".join(sorted({b for row in transition.values() for b in row})) + " |")
        off_cats = sorted({b for row in transition.values() for b in row})
        lines.append("|" + "---|" * (len(off_cats) + 1))
        for a in sorted(transition.keys()):
            row = [str(transition[a].get(b, 0)) for b in off_cats]
            lines.append(f"| {a} | " + " | ".join(row) + " |")
    else:
        lines.append("(전이 데이터 없음)")
    lines.append("")

    if has_dist_fig:
        lines.append("## 5-1. 조건별 레이블 분포\n")
        lines.append("![distribution](figures/label_distribution.png)\n")

    lines.append("## 6. 체인 단계별 특성\n")
    for stage in sorted(labels_df["stage"].unique()):
        sdf = labels_df[labels_df["stage"] == stage]
        extra = ""
        if stage == "persona_03" and "taxonomy_valid" in sdf.columns:
            tv = sdf["taxonomy_valid"].dropna()
            if len(tv):
                extra = f", cause_label_taxonomy 준수율={_fmt(tv.mean())}"
        lines.append(f"- **{stage}**: schema_valid 평균={_fmt(sdf['schema_valid'].mean())}, "
                      f"평균 completion_tokens={_fmt(sdf['completion_tokens'].mean(), 1)}{extra}")
    lines.append("")

    lines.append("## 7. 실패 사례 분석 (상위 10건)\n")
    fail_df = labels_df[~labels_df["schema_valid"].fillna(False)].head(10)
    if fail_df.empty:
        lines.append("실패 사례 없음.")
    else:
        lines.append("| doc_id | stage | condition | parse_error |")
        lines.append("|---|---|---|---|")
        for _, row in fail_df.iterrows():
            lines.append(f"| {row['doc_id']} | {row['stage']} | {row['condition']} | {row.get('parse_error')} |")
    lines.append("")

    lines.append("## 8. 한계\n")
    lines.append(
        "- 조건당 1회(greedy) 실행이므로 실행 간 분산(self-consistency)을 추정하지 못한다. "
        "identity_on vs identity_off 차이가 '정체성 효과'인지 단일 실행의 우연한 변동인지 "
        "완전히 분리할 수는 없다."
    )
    lines.append(
        "- persona_01/02/03은 병렬 3-rater가 아니라 순차 체인이므로, 지시서 원안의 "
        "3-페르소나 간 Fleiss' kappa 합의 분석은 구조적으로 산출하지 않았다."
    )
    lines.append(
        "- 입력 코퍼스가 재결서 원문이 아니라 재결요약서이며 '사실' 섹션 헤더가 없어, "
        "keyword_sentences의 사건 개요/일시/장소/사고 경위 4개 필드로 사실 입력을 근사했다."
    )
    lines.append("- gold set이 없어 정확도(F1 등)를 평가하지 못했다.")
    lines.append(
        "- [RETRIEVED_LEGAL_CONTEXT]는 임베딩 기반 RAG가 아니라 조문 단위 키워드 부분일치 근사 검색이다."
    )
    identity_on_tokens = manifest.get("identity_prompt_token_diff")
    if identity_on_tokens:
        lines.append(f"- identity_on/off 프롬프트 토큰 수 차이: {identity_on_tokens}")
    lines.append("")

    lines.append("## 9. 재현 정보\n")
    lines.append(f"- config sha256: `{config.sha256}`")
    lines.append(f"- 시작: {manifest.get('started_at')}, 종료: {manifest.get('finished_at')}")
    lines.append(f"- 처리 문서 수: {manifest.get('doc_count')}, 실패 건수: {manifest.get('failure_count')}")
    lines.append("")

    report_path = report_dir / "benchmark_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
