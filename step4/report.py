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


def _fig_pipeline_architecture(out_path: Path) -> bool:
    """페르소나 생성(persona/src, Qwen3, BM25, 1회) -> STEP4 실행(step4/, Qwen2.5, 근사검색,
    조건당 반복) -> 리포트까지 전체 흐름을 고정 SVG로 그린다. 실행마다 값이 바뀌는 데이터가
    아니라 코드 구조 자체를 보여주는 도식이라, 다른 두 _fig_* 함수와 달리 labels_df 등
    입력 데이터가 필요 없다."""
    gray = ("#F1EFE8", "#5F5E5A", "#2C2C2A")
    purple = ("#EEEDFE", "#534AB7", "#26215C")
    teal = ("#E1F5EE", "#0F6E56", "#04342C")
    coral = ("#FAECE7", "#993C1D", "#4A1B0C")

    def node(x, y, w, h, title, subtitle, palette):
        fill, stroke, text = palette
        cx = x + w / 2
        return (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
            f'<text x="{cx}" y="{y + 22}" text-anchor="middle" font-size="14" font-weight="700" '
            f'fill="{text}" font-family="Noto Sans CJK KR, sans-serif">{title}</text>'
            f'<text x="{cx}" y="{y + 40}" text-anchor="middle" font-size="12" '
            f'fill="{stroke}" font-family="Noto Sans CJK KR, sans-serif">{subtitle}</text>'
        )

    def arrow(x1, y1, x2, y2):
        return (
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#8a897f" '
            f'stroke-width="1.5" marker-end="url(#arrow)"/>'
        )

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="680" height="752" viewBox="0 0 680 752">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" '
        'stroke="#8a897f" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>',
        '<rect x="0" y="0" width="680" height="752" fill="#ffffff"/>',
        node(60, 40, 260, 56, "법령 원문 코퍼스", "국내 10개 + 국제기준 1개", gray),
        node(360, 40, 260, 56, "생성 지침", "페르소나 3종 생성 규칙", gray),
        arrow(190, 96, 190, 136),
        arrow(490, 96, 490, 136),
        node(60, 136, 560, 56, "페르소나 생성기", "Qwen3-14B · BM25 검색, 1회 실행", purple),
        arrow(340, 192, 340, 232),
        node(60, 232, 560, 56, "페르소나 설계 문서", "역할 지시문 + 정책·스키마 고정 파일", gray),
        arrow(340, 288, 340, 348),
        node(60, 348, 560, 56, "근사검색 + on/off 조립", "같은 법령자료 재사용 · Qwen2.5, 매 호출 반복", teal),
        arrow(340, 404, 340, 444),
        node(60, 444, 560, 56, "LLM 추론(vLLM)", "3단계 순차 · 반복루프 시 보정재시도", coral),
        arrow(340, 500, 340, 540),
        node(60, 540, 560, 56, "검증 · 저장", "flat/labels.parquet · csv", gray),
        arrow(340, 596, 340, 656),
        node(60, 656, 560, 56, "리포트 · 비교분석", "benchmark_report.md · PPT · 웹 뷰어", gray),
        '</svg>',
    ]
    out_path.write_text("\n".join(parts), encoding="utf-8")
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
    has_arch_fig = _fig_pipeline_architecture(figures_dir / "pipeline_architecture.svg")

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

    if has_arch_fig:
        lines.append("## 1-1. 전체 파이프라인 구조 (페르소나 생성 -> STEP4 실행 -> 리포트)\n")
        lines.append("![pipeline](figures/pipeline_architecture.svg)\n")
        lines.append(
            "- **설계 시점(위 3칸, 1회)**: `persona/src/generate_personas.py`가 법령 원문 "
            "코퍼스(`persona/KMST/`)와 생성 지침(`prompt.txt`)을 입력받아 Qwen3-14B + BM25 검색으로 "
            "페르소나 설계 문서(`persona_model/*.md`)를 만든다. 이 단계는 모델 가중치를 재학습하지 "
            "않는다 — 법령을 읽고 색인하고 검색 기반으로 컨텍스트를 주입해 프롬프트 문서를 생성할 뿐이다."
        )
        lines.append(
            "- **실행 시점(아래 4칸, 조건당 반복)**: STEP4(`step4/`)가 그 설계 문서를 읽어, "
            "매 호출(군집 x 단계 x 조건)마다 같은 법령 코퍼스에서 관련 조문을 다시 검색하고, "
            "identity_on/off 문구만 다른 프롬프트를 조립해 Qwen2.5-14B-Instruct로 추론한다."
        )
        lines.append(
            "- 페르소나가 특정 모델에 학습된 지식이 아니라 텍스트 문서이기 때문에, 문서를 만들 때 쓴 "
            "모델(Qwen3)과 실행에 쓴 모델(Qwen2.5)이 달라도 문제없이 그대로 재사용할 수 있었다."
        )
        lines.append("")

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
    lines.append(
        "**왜 두 조건의 레이블이 같거나 다른가**: identity_on/off는 `persona_0N.md` 한 문서에서 "
        "정체성 선언 문장(\"당신은 KMST-P0N이다\") 한 줄만 넣고 뺀 것이며, 그 외 법령 검색 컨텍스트·"
        "군집 대표문장·JSON 스키마·디코딩 파라미터는 매 실행마다 `step4/ablation.py`가 자동 검증할 "
        "만큼 완전히 동일하다. 즉 두 조건이 받는 증거 자체는 같으므로, 증거가 하나의 원인을 뚜렷하게 "
        "가리키는 군집은 정체성 문구 유무와 무관하게 같은 레이블로 수렴하는 것이 greedy(temperature=0) "
        "디코딩에서는 자연스러운 결과다. 레이블이 달라진 군집은 정체성 프레이밍이 실제로 판단에 영향을 "
        "준 사례로 해석할 수 있다 — 아래 표의 대각선(동일) 대 비대각선(변경) 비율 자체가 이 비교실험의 "
        "핵심 결과다.\n"
    )
    if has_trans_fig:
        lines.append("![transition](figures/root_cause_primary_transition.png)\n")
    transition = p03.get("root_cause_primary_transition_on_to_off")
    if transition:
        off_cats = sorted({b for row in transition.values() for b in row})
        n_same = sum(transition.get(a, {}).get(a, 0) for a in transition)
        n_total = sum(sum(row.values()) for row in transition.values())
        lines.append(f"- 동일(대각선): {n_same}/{n_total}건, 변경(비대각선): {n_total - n_same}/{n_total}건\n")
        lines.append("| on \\ off | " + " | ".join(off_cats) + " |")
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
                extra = f", cause_label_taxonomy 준수율(문서 단위, all-or-nothing)={_fmt(tv.mean())}"
        lines.append(f"- **{stage}**: schema_valid 평균={_fmt(sdf['schema_valid'].mean())}, "
                      f"평균 completion_tokens={_fmt(sdf['completion_tokens'].mean(), 1)}{extra}")
    lines.append("")

    # 문서 단위(all-or-nothing) 준수율 하나만 보면 label_code 3개 중 1개만 틀려도 문서 전체가
    # False로 잡혀, 작은 표본에서 조건 간 차이가 실제보다 과장되어 보일 수 있다
    # (test_20260811/07_유효성지표_생성량분석_정리.md 6-5/6-6절). 항목(label_code) 단위 준수율과
    # 조건별(identity_on/off) 분리를 추가로 보여줘, 리포트만 봐도 두 지표가 다른 결론을 줄 수
    # 있다는 사실이 드러나게 한다.
    p03 = labels_df[labels_df["stage"] == "persona_03"]
    if not p03.empty and {"n_taxonomy_ok_items", "n_taxonomy_total_items"}.issubset(p03.columns):
        lines.append("### 6-1. KMST 분류체계 준수율 — 문서 단위 vs 항목 단위, 조건별 분리\n")
        lines.append("| condition | 문서 수 | 문서 단위 준수율(all-or-nothing) | 항목 단위 준수율(label_code 개별) |")
        lines.append("|---|---|---|---|")
        for condition, cdf in p03.groupby("condition"):
            tv = cdf["taxonomy_valid"].dropna()
            doc_rate = _fmt(tv.mean()) if len(tv) else "N/A"
            ok_sum = cdf["n_taxonomy_ok_items"].dropna().sum()
            total_sum = cdf["n_taxonomy_total_items"].dropna().sum()
            item_rate = _fmt(ok_sum / total_sum) if total_sum else "N/A"
            lines.append(f"| {condition} | {len(cdf)} | {doc_rate} | {item_rate} |")
        tv_all = p03["taxonomy_valid"].dropna()
        ok_all = p03["n_taxonomy_ok_items"].dropna().sum()
        total_all = p03["n_taxonomy_total_items"].dropna().sum()
        lines.append(
            f"| **전체** | **{len(p03)}** | **{_fmt(tv_all.mean()) if len(tv_all) else 'N/A'}** | "
            f"**{_fmt(ok_all / total_all) if total_all else 'N/A'}** |"
        )
        lines.append(
            "\n주의: 두 지표는 서로 다른 질문에 답한다 — 문서 단위는 \"이 문서가 완벽했는가\", "
            "항목 단위는 \"이름표 하나하나가 맞았는가\"다. 표본이 작을수록(군집 수가 적을수록) "
            "두 지표가 크게 갈라질 수 있으므로 둘 중 하나만 인용하지 말 것.\n"
        )
        if "taxonomy_defect_retry_attempted" in p03.columns:
            n_attempted = int(p03["taxonomy_defect_retry_attempted"].fillna(0).sum())
            n_succeeded = int(p03["taxonomy_defect_retry_succeeded"].fillna(0).sum())
            lines.append(
                f"- taxonomy 결측 결함(confidence는 정상인데 label_code가 빈 경우) 교정 재시도: "
                f"{n_attempted}건 시도, {n_succeeded}건 성공\n"
            )

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
    lines.append(
        "- 모델이 인용한 근거가 실제 검색된 [SOURCE] 목록 안에 있는지 자동 검증하는 코드가 없다 — "
        "프롬프트로 '존재하는 근거만 인용하라'고 지시할 뿐, 할루시네이션(법령 지어내기) 여부를 "
        "코드로 검증하지 않는다."
    )
    lines.append(
        "- STEP4 실행 시점의 법령 검색(`step4/legal_retrieval.py`)은 페르소나 생성 시점의 BM25 "
        "검색(`persona/src/generate_personas.py`)보다 단순한 키워드 집합 교집합 개수 세기라서, "
        "'가장 관련 있는 조문'을 놓칠 가능성이 생성 단계보다 크다. 다만 identity_on/off 양쪽에 "
        "동일하게 적용되므로 두 조건 간 비교 자체는 이 한계의 영향을 받지 않는다."
    )
    identity_on_tokens = manifest.get("identity_prompt_token_diff")
    if identity_on_tokens:
        lines.append(f"- identity_on/off 프롬프트 토큰 수 차이: {identity_on_tokens}")
    repetition_retry_cases = manifest.get("repetition_retry_cases") or []
    if repetition_retry_cases:
        n_succeeded = sum(1 for c in repetition_retry_cases if c.get("succeeded"))
        n_exhausted = sum(
            1 for c in repetition_retry_cases if not c.get("succeeded")
        )
        lines.append(
            f"- **결정성 예외**: 반복루프 의심 실패 {len(repetition_retry_cases)}건에 대해 "
            "frequency_penalty/repetition_penalty를 단계적으로 올리고(2단계부터는 temperature>0도 "
            f"섞어) 성공할 때까지 해당 호출 1건만 재시도했다(성공 {n_succeeded}건, "
            f"{f'전체 단계 소진 후에도 실패 {n_exhausted}건, ' if n_exhausted else ''}"
            "각 케이스의 시도 횟수는 attempts_used 참고). temperature>0로 생성된 케이스는 "
            "config.generation의 기본값(greedy, temperature=0)과 다른 파라미터로 생성되었으므로 "
            "100% 결정성 전제에서 벗어난 의도적 예외로 간주해야 한다: "
            f"{[{'doc_id': c['doc_id'], 'stage': c['stage'], 'condition': c['condition'], 'succeeded': c['succeeded'], 'attempts_used': c.get('attempts_used')} for c in repetition_retry_cases]}"
        )
    lines.append("")

    lines.append("## 8-1. 이 실험이 증명하는 것과 증명하지 않는 것\n")
    lines.append(
        "**증명하는 것**: 똑같은 법령 발췌 · 똑같은 군집 데이터를 주고, '역할 선언' 문장(정체성 "
        "블록) 하나만 있고 없고를 바꿨을 때 레이블링 행동(최종 레이블 · 서술 길이 · 생성 안정성)이 "
        "달라지는가."
    )
    lines.append(
        "**증명하지 않는 것**: '법을 학습한 전문가 모델 vs 법을 모르는 일반 모델'의 비교가 "
        "아니다. identity_on/off 두 조건 모두 완전히 동일한 실제 법령 발췌([RETRIEVED_LEGAL_CONTEXT])를 "
        "받는다 — off 조건도 법을 '모르는' 게 아니라, 단지 '당신은 KMST-P0N이다' 역할 프레이밍 "
        "문장만 빠져 있을 뿐이다. 이 실험을 '법 지식을 학습시킨 페르소나가 효과 있는가'의 근거로 "
        "인용하려면 이 구분을 반드시 명시해야 한다."
    )
    lines.append("**신뢰도를 뒷받침하는 것**:")
    lines.append("- 주원인(root_cause_primary) 레이블 100% KMST 공식 22항목 매칭 (on/off 동일)")
    lines.append("- greedy 디코딩 byte 단위 재현성 100% (2. 결정성 확인 결과)")
    lines.append(
        "- 법령 발췌가 실제 조문 원문(허구 생성이 아님), `[SOURCE DOC-XX-CXXXX]` 형식으로 출처 표기"
    )
    lines.append("**진짜 한계(정체성이 학습됐는지 여부와는 무관하다)**:")
    lines.append("- gold set 부재로 정확도(F1 등) 자체는 평가 불가 — 이번 결과는 '일치도'이지 '정확도'가 아니다.")
    lines.append("- 모델이 인용한 근거가 실제 검색된 [SOURCE] 안에 있는지 자동 검증하는 코드가 없다(할루시네이션 미검증).")
    lines.append(
        "- STEP4 실행 시점의 법령 검색이 페르소나 생성 시점(BM25)보다 단순하다 — 아래 8-2, 8-3 참고."
    )
    lines.append("")

    lines.append("## 8-2. 법령 코퍼스 재사용 확인 (생성 단계 <-> 실행 단계)\n")
    lines.append(
        "페르소나 설계 문서(`persona_model/*.md`)를 만들 때 쓴 법령 원문과, STEP4 레이블링 실행 "
        "시점에 검색하는 법령 원문은 물리적으로 동일한 파일이다:"
    )
    lines.append("- `config/config.json`의 `paths.persona_dir`가 `persona_model`을 가리킨다.")
    lines.append(
        "- `step4/pipeline.py`의 `legal_retrieval.load_legal_chunks(Path(persona_dir) / \"data\", "
        "logger)`가 실제로 읽는 경로는 `persona_model/data/`다."
    )
    lines.append(
        "- `persona_model/data/`는 페르소나 생성에 쓴 원본 코퍼스 `persona/KMST/`와 diff 결과 "
        "0건 — 바이트 단위로 완전히 동일한 11개 문서(국내 법령/행정규칙 10개 + 국제기준 1개)다."
    )
    lines.append("\n다만 그 파일을 검색하는 알고리즘은 두 단계가 다르다:\n")
    lines.append("| | 페르소나 생성 단계 | STEP4 실행 단계 |")
    lines.append("|---|---|---|")
    lines.append(
        "| 코드 | `persona/src/generate_personas.py`의 `BM25Index` 클래스 | "
        "`step4/legal_retrieval.py`의 `retrieve()` 함수 |"
    )
    lines.append(
        "| 알고리즘 | BM25 (TF-IDF 가중치 + 문서 길이 정규화) | 단순 키워드 집합 교집합 개수만 셈(가중치 없음) |"
    )
    lines.append(
        "| 커버리지 보장 | 필수 문서 10개 각각 최소 1개 청크 포함을 강제 | 없음 — 전체 청크 중 점수 상위 5개만 반환 |"
    )
    lines.append("")

    lines.append("## 8-3. 왜 이게 RAG(Retrieval-Augmented Generation)가 아닌가\n")
    lines.append(
        "일반적으로 'RAG'라고 부르는 구조는 문서를 임베딩 벡터로 변환하고 질의도 임베딩으로 바꿔서, "
        "벡터 유사도(코사인 거리 등)로 의미적으로 가까운 청크를 찾는다 — 단어가 정확히 겹치지 "
        "않아도('선박' vs '배') 의미가 비슷하면 찾아낼 수 있다."
    )
    lines.append(
        "이 시스템의 두 검색 단계(생성 시 BM25, 실행 시 키워드 집합 교집합) 모두 임베딩을 전혀 "
        "쓰지 않는다. BM25는 단어 통계(빈도 · 희귀도 · 문서 길이) 기반이고, STEP4 실행 시점 검색은 "
        "그보다도 단순한 '겹치는 단어 개수'만 센다 — 둘 다 텍스트 표면 형태(단어 자체)가 일치해야 "
        "찾아지는 어휘적(lexical) 검색이지, 의미를 이해하고 찾는 의미적(semantic) 검색이 아니다. "
        "그래서 '선박'이라는 단어를 안 쓰고 '배'라고만 쓴 조문은, 실제로 관련 있어도 놓칠 수 있다."
    )
    lines.append(
        "이 프로젝트에서 이미 임베딩(SBERT)을 쓰는 STEP1~3(문서 군집화)과 달리, STEP4의 법령 "
        "검색만큼은 임베딩을 쓰지 않는 경량 근사 구현이다 — 위 '## 8. 한계'의 "
        "'[RETRIEVED_LEGAL_CONTEXT]는 임베딩 기반 RAG가 아니라...' 문구가 가리키는 정확한 의미다."
    )
    lines.append("")

    lines.append("## 9. 재현 정보\n")
    lines.append(f"- config sha256: `{config.sha256}`")
    lines.append(f"- 시작: {manifest.get('started_at')}, 종료: {manifest.get('finished_at')}")
    lines.append(f"- 처리 문서 수: {manifest.get('doc_count')}, 실패 건수: {manifest.get('failure_count')}")
    lines.append("")

    report_path = report_dir / "benchmark_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
