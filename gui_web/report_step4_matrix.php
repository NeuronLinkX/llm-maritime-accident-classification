<?php
/**
 * STEP 4 — 프롬프트 3종(A/B/C) x temperature 2종(0.0/0.2) = 6칸 실험 매트릭스 전용 뷰어.
 *
 * report_template.html(STEP1~4 통합 리포트)과는 완전히 별도의 페이지다 — 이 실험은
 * "STEP4 파이프라인을 바꾸지 않고, Prompt Engineering 단계의 문구와 Gateway 단계의
 * temperature만 조합을 바꿔가며 다시 통과시킨" 것이므로, 기존 리포트에 섞기보다
 * 독립된 페이지에서 그 6칸을 나란히 비교하는 게 더 명확하다.
 *
 * 데이터는 이 파일이 직접 만들지 않고 api_prompt_matrix_stability.php를 브라우저가
 * 주기적으로 fetch한다(report_template.html과 같은 패턴) — 배치 스크립트
 * (step_4_process/run_prompt_temp_matrix.py)가 백그라운드에서 계속 기록을 쌓는 동안
 * 이 페이지를 열어두면 진행률이 자동으로 갱신된다.
 *
 * 실행: php -S 0.0.0.0:PORT (이 파일이 있는 디렉토리에서)
 * 접속: http://localhost:PORT/report_step4_matrix.php
 */

declare(strict_types=1);

header("Content-Type: text/html; charset=utf-8");
header("Cache-Control: no-store");

require_once __DIR__ . "/lib_llm_common.php";

// 히트맵·표 곳곳에 "C0"~"C4"로만 나오는 군집이 실제로 뭔지 상단에 짧게 알려주기 위한
// 범례. STEP3 산출물(clusters.csv/cluster_keywords.csv)에서 그대로 읽어온다 — 이 실험
// 동안 군집 자체는 바뀌지 않으므로 정적으로 한 번만 계산해서 페이지에 박아 넣는다.
$clusterLegend = [];
$idsByCluster = \LlmCommon\load_cluster_ids();
$keywordsByCluster = \LlmCommon\load_keywords();
foreach ($idsByCluster as $c => $ids) {
    $clusterLegend[$c] = [
        "n_docs" => count($ids),
        "keywords" => array_slice($keywordsByCluster[$c] ?? [], 0, 6),
    ];
}
ksort($clusterLegend);
?>
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>STEP4 프롬프트x온도 실험 매트릭스</title>
<style>
  :root {
    --ink-900: #1b2431; --ink-700: #3d4657; --ink-500: #69728a;
    --sea-700: #22415a; --sea-600: #305877; --sea-500: #4c7592; --sea-100: #e7dfca;
    --paper-50: #f5f1e6; --panel: #fffdf8; --line: #ddd4c0; --line-strong: #c2b89d;
    --ok-fg: #2f6b4f; --ok-bg: #e4eee0;
    --warn-fg: #8a5a1c; --warn-bg: #f3e7d0;
    --bad-fg: #9c3a2e; --bad-bg: #f5e1da;
    --shadow: 0 1px 0 rgba(27, 36, 49, 0.08);
    --series-1: #2a78d6; --series-2: #1baf7a; --series-3: #eda100;
  }
  :root[data-theme="dark"] {
    --ink-900: #f1ece0; --ink-700: #d0c9b8; --ink-500: #9c9686;
    --sea-700: #9bc2dd; --sea-600: #7ea7c3; --sea-500: #5c86a3; --sea-100: #2a2a1e;
    --paper-50: #141209; --panel: #1d1a13; --line: #322d21; --line-strong: #453e2f;
    --ok-fg: #8fd6ac; --ok-bg: #1c2e1f;
    --warn-fg: #e2b877; --warn-bg: #332812;
    --bad-fg: #e2988a; --bad-bg: #331e17;
    --shadow: 0 1px 0 rgba(0, 0, 0, 0.4);
    --series-1: #3987e5; --series-2: #199e70; --series-3: #c98500;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --ink-900: #f1ece0; --ink-700: #d0c9b8; --ink-500: #9c9686;
      --sea-700: #9bc2dd; --sea-600: #7ea7c3; --sea-500: #5c86a3; --sea-100: #2a2a1e;
      --paper-50: #141209; --panel: #1d1a13; --line: #322d21; --line-strong: #453e2f;
      --ok-fg: #8fd6ac; --ok-bg: #1c2e1f;
      --warn-fg: #e2b877; --warn-bg: #332812;
      --bad-fg: #e2988a; --bad-bg: #331e17;
      --shadow: 0 1px 0 rgba(0, 0, 0, 0.4);
      --series-1: #3987e5; --series-2: #199e70; --series-3: #c98500;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--paper-50); color: var(--ink-900);
    font-family: -apple-system, "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", BlinkMacSystemFont, sans-serif;
    font-size: 15px; line-height: 1.55; -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1880px; margin: 0 auto; padding: 26px 28px 64px; }
  [id] { scroll-margin-top: 20px; }
  header.page-head { margin-bottom: 22px; }
  .eyebrow { font-size: 11.5px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--sea-600); }
  h1 { font-size: 25px; margin: 4px 0 6px; text-wrap: balance; }
  .lede { color: var(--ink-700); max-width: 1400px; }
  .badge-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .badge { font-size: 11.5px; font-weight: 700; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--line-strong); color: var(--ink-700); background: var(--panel); }
  .badge.warn { color: var(--warn-fg); background: var(--warn-bg); border-color: transparent; }

  section.card {
    background: var(--panel); border: 1px solid var(--line-strong); border-radius: 10px;
    box-shadow: var(--shadow); padding: 20px 22px; margin-bottom: 20px;
  }
  section.card > h2 { font-size: 16px; margin: 0 0 4px; }
  section.card > .sub { font-size: 12.5px; color: var(--ink-500); margin: 0 0 16px; }

  /* ---- 군집 안내 ---- */
  .cluster-legend-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; }
  table.cluster-legend { border-collapse: collapse; width: 100%; font-size: 12px; }
  table.cluster-legend th, table.cluster-legend td { padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--line); }
  table.cluster-legend thead th { background: var(--paper-50); color: var(--ink-700); font-weight: 700; }
  table.cluster-legend tbody tr:last-child td { border-bottom: none; }
  table.cluster-legend .cl-id { font-weight: 700; color: var(--sea-700); white-space: nowrap; }
  table.cluster-legend .cl-n { color: var(--ink-500); white-space: nowrap; }
  table.cluster-legend .cl-kw { color: var(--ink-900); }

  /* ---- 실험 설계: 3종 프롬프트 카드 ---- */
  .variant-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
  .variant-card { border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; background: var(--paper-50); }
  .variant-card h3 { font-size: 13.5px; margin: 0 0 6px; color: var(--sea-700); }
  .variant-card p { font-size: 12.5px; color: var(--ink-700); margin: 0; }

  /* ---- 6칸 그리드 ---- */
  /* 칸 안 히트맵이 넓어지면(모델×군집 상세, 라벨+비율 2줄 표시) 3열을 억지로 1fr로
     욱여넣지 않고 가로 스크롤로 빠지게 한다 — 안 그러면 오른쪽 칸(C)이 카드 밖으로
     밀려나 잘려 보인다. 각 열에 최소 폭을 주고, 그 폭 합이 래퍼보다 넓어지면
     .matrix-grid-wrap이 가로 스크롤바를 낸다. */
  .matrix-grid-wrap { overflow-x: auto; padding-bottom: 6px; }
  .matrix-grid { display: grid; grid-template-columns: 52px repeat(3, minmax(480px, 1fr)); gap: 6px; align-items: stretch; }
  .matrix-grid .col-head, .matrix-grid .row-head {
    font-size: 11px; font-weight: 700; color: var(--ink-500); text-transform: uppercase; letter-spacing: 0.02em;
    white-space: nowrap; display: flex; align-items: center; justify-content: center; text-align: center; padding: 6px 2px;
  }
  .matrix-grid .row-head { justify-content: flex-start; writing-mode: horizontal-tb; padding-left: 0; }
  /* 가로 스크롤 중에도 "T=0.0/T=0.2" 라벨이 계속 보이도록 첫 열을 고정한다. */
  .matrix-grid .row-head, .matrix-grid > .col-head:first-child { position: sticky; left: 0; z-index: 2; background: var(--panel); }
  .cell-card { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; background: var(--paper-50); min-height: 118px; }
  .cell-card .cell-title { font-size: 12.5px; font-weight: 700; color: var(--ink-900); margin-bottom: 6px; }
  .cell-card .cell-progress-track { height: 8px; border-radius: 4px; background: var(--line); overflow: hidden; margin-bottom: 6px; }
  .cell-card .cell-progress-bar { height: 100%; background: var(--sea-600); }
  .cell-card .cell-meta { font-size: 11.5px; color: var(--ink-500); display: flex; justify-content: space-between; }
  .cell-card .cell-stability { font-size: 20px; font-weight: 700; margin-top: 8px; font-variant-numeric: tabular-nums; }
  .cell-card.tier-ok .cell-stability { color: var(--ok-fg); }
  .cell-card.tier-warn .cell-stability { color: var(--warn-fg); }
  .cell-card.tier-bad .cell-stability { color: var(--bad-fg); }
  .cell-card.tier-none .cell-stability { color: var(--ink-500); }
  .cell-card .cell-note { font-size: 10.5px; color: var(--ink-500); margin-top: 2px; }

  /* ---- 비교 막대그래프 ---- */
  .compare-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px; }
  .bar-row { display: flex; align-items: center; gap: 10px; font-size: 12.5px; margin-bottom: 8px; }
  .bar-label { flex: none; width: 130px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar-track { flex: 1 1 auto; height: 15px; background: var(--paper-50); border: 1px solid var(--line); border-radius: 3px; overflow: hidden; }
  .bar-fill { height: 100%; background: var(--sea-600); }
  .bar-val { flex: none; width: 50px; text-align: right; color: var(--ink-500); font-variant-numeric: tabular-nums; }

  /* ---- 상세 히트맵(모델x군집) ---- */
  .heat-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; margin-top: 10px; }
  table.heat { border-collapse: collapse; width: 100%; font-size: 11px; }
  table.heat th, table.heat td { padding: 6px 8px; text-align: center; white-space: nowrap; border-bottom: 1px solid var(--line); }
  table.heat thead th { background: var(--paper-50); color: var(--ink-700); font-weight: 700; }
  table.heat .rowhead { text-align: left; font-weight: 700; background: var(--paper-50); }
  .heat-ok { background: var(--ok-bg); color: var(--ok-fg); font-weight: 700; }
  .heat-warn { background: var(--warn-bg); color: var(--warn-fg); font-weight: 700; }
  .heat-bad { background: var(--bad-bg); color: var(--bad-fg); font-weight: 700; }
  .heat-none { color: var(--ink-500); }
  table.heat.stab td { white-space: normal; min-width: 92px; }
  table.heat td .stab-pct { display: block; font-size: 10px; font-weight: 500; color: var(--ink-500); margin-top: 2px; }
  table.heat .cross-model-row td, table.heat .cross-model-row th.rowhead { border-top: 2px solid var(--line-strong); }
  table.heat .cross-model-row .rowhead { font-style: italic; }
  details.cell-detail { margin-top: 8px; }
  details.cell-detail summary { cursor: pointer; font-size: 11.5px; color: var(--sea-600); font-weight: 700; }
  .mm-summary-pending { font-size: 13px; color: var(--ink-500); padding: 18px; text-align: center; border: 1px dashed var(--line-strong); border-radius: 8px; }

  /* ---- crosstab: 근거 문장 리포트 등에 쓰는 일반 표. 이 파일엔 report_template.html의
     .crosstab 스타일이 없어서(별도 파일, 스타일시트 공유 안 함) 여태 줄 없이 밋밋하게
     나왔다 — 열/행 구분이 뚜렷하게 보이도록 굵은 테두리·충분한 여백·줄무늬를 준다. ---- */
  .crosstab-wrap { overflow-x: auto; border: 1px solid var(--line-strong); border-radius: 6px; }
  table.crosstab { border-collapse: collapse; width: 100%; font-size: 12.5px; }
  table.crosstab th, table.crosstab td {
    padding: 10px 14px; text-align: center; white-space: nowrap;
    border-bottom: 1px solid var(--line-strong); border-right: 1px solid var(--line);
    font-variant-numeric: tabular-nums;
  }
  table.crosstab th:last-child, table.crosstab td:last-child { border-right: none; }
  table.crosstab thead th { background: var(--sea-100); color: var(--ink-900); font-weight: 700; border-bottom: 2px solid var(--line-strong); }
  table.crosstab .ct-rowhead { text-align: left; font-weight: 700; background: var(--sea-100); color: var(--ink-900); }
  table.crosstab tbody tr:nth-child(even) { background: var(--paper-50); }
  table.crosstab tbody tr:last-child td { border-bottom: none; }

  /* ---- 근거 문장 예시 카드 ---- */
  .rationale-example { border: 1px solid var(--line); border-radius: 7px; padding: 10px 12px; margin-top: 8px; background: var(--paper-50); }
  .rationale-example-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
  .rationale-example-tags .badge { font-size: 10.5px; padding: 2px 8px; }
  .rationale-example-text { font-size: 12.5px; color: var(--ink-900); line-height: 1.6; }

  .temp-explainer-line { display: block; margin-bottom: 4px; }
  .temp-explainer-line:last-child { margin-bottom: 0; }

  /* ---- 6칸 상세 비교(군집별) 필터 ---- */
  .cluster-compare-filter-label { display: inline-flex; align-items: center; gap: 8px; font-size: 12.5px; font-weight: 700; color: var(--ink-700); margin: 4px 0 14px; }
  .cluster-compare-filter-label select {
    font-size: 12.5px; font-weight: 600; color: var(--ink-900); background: var(--panel);
    border: 1px solid var(--line-strong); border-radius: 6px; padding: 5px 10px; cursor: pointer;
  }

  /* ---- 6칸 상세 비교(군집별) ---- */
  .cluster-compare-card { border: 1px solid var(--line-strong); border-radius: 8px; padding: 14px 16px; margin-bottom: 16px; background: var(--paper-50); }

  /* ---- 최종 요약: 산점도 + 순위 리스트 ---- */
  .final-rank-list { margin-top: 18px; display: flex; flex-direction: column; gap: 6px; max-width: 640px; }
  .final-rank-row { display: flex; align-items: center; gap: 10px; font-size: 12.5px; padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--paper-50); }
  .final-rank-no { flex: none; width: 18px; text-align: center; font-weight: 700; color: var(--ink-500); }
  .final-rank-dot { flex: none; width: 10px; height: 10px; border-radius: 50%; }
  .final-rank-label { flex: none; width: 190px; font-weight: 700; color: var(--ink-900); }
  .final-rank-vals { color: var(--ink-500); }

  /* ---- 최종 요약: 군집/프롬프트 부연설명 + 산점도/막대 토글 ---- */
  .final-legend-recap { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; margin-bottom: 16px; }
  .final-legend-title { font-size: 11.5px; font-weight: 700; color: var(--ink-500); text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 6px; }
  .final-legend-body { font-size: 12px; color: var(--ink-900); line-height: 1.7; }
  .final-legend-body .fl-chip { display: inline-block; font-weight: 700; color: var(--sea-700); margin-right: 4px; }
  .final-legend-body .fl-row { margin-bottom: 3px; }
  .cluster-compare-card h3 { font-size: 13.5px; margin: 0 0 8px; color: var(--sea-700); }
  table.compare6 { border-collapse: collapse; width: 100%; font-size: 11px; }
  table.compare6 th, table.compare6 td { padding: 6px 8px; text-align: center; border-bottom: 1px solid var(--line); white-space: normal; min-width: 90px; }
  table.compare6 thead th { background: var(--panel); color: var(--ink-700); font-weight: 700; }
  table.compare6 .rowhead { text-align: left; font-weight: 700; background: var(--panel); }
  table.compare6 td .stab-pct { display: block; font-size: 10px; font-weight: 500; color: var(--ink-500); margin-top: 2px; }

  .conclusion-box { border-left: 4px solid var(--warn-fg); background: var(--warn-bg); border-radius: 0 8px 8px 0; padding: 16px 18px; }
  .conclusion-box h3 { margin: 0 0 8px; font-size: 14px; color: var(--warn-fg); }
  .conclusion-box p { margin: 0 0 8px; font-size: 13px; color: var(--ink-900); }
  .conclusion-box ul { margin: 6px 0 0; padding-left: 20px; font-size: 13px; color: var(--ink-900); }
  .conclusion-box li { margin-bottom: 4px; }

  .status-line { font-size: 12px; color: var(--ink-500); margin-top: 8px; }
  .arch-svg-wrap { overflow-x: auto; }
  footer.foot { text-align: center; font-size: 11.5px; color: var(--ink-500); padding: 20px 0 6px; }
</style>
</head>
<body>
<div class="wrap">

  <header class="page-head">
    <div class="eyebrow">STEP 4 : 실험 · 프롬프트x온도 매트릭스</div>
    <h1>STEP4 프롬프트 3종 × Temperature 2종 실험 매트릭스</h1>
    <p class="lede">
      STEP4 파이프라인 구조(그라운딩 → 프롬프트 구성 → 게이트웨이 → 모델 추론 → 출력 가드레일)는
      그대로 두고, <b>3. Prompt Engineering</b> 단계의 System Prompt/Task Instruction만 전문가 관점이
      다른 3종(A/B/C)으로, <b>4. Gateway</b> 단계의 temperature만 0.0 / 0.2 두 값으로 바꿔가며
      같은 파이프라인을 6개 조합으로 반복 실행한 결과를 비교합니다.
    </p>
    <div class="badge-row" id="top-badges">
      <span class="badge">로딩 중…</span>
    </div>
  </header>

  <section class="card">
    <h2>군집(Cluster) 안내</h2>
    <p class="sub">아래 히트맵·표에 나오는 C0~C4는 STEP3(SBERT 임베딩 + K-Means)에서 만들어진 군집 번호입니다. 이 실험 동안 군집 자체는 바뀌지 않습니다.</p>
    <div class="cluster-legend-wrap">
      <table class="cluster-legend">
        <thead><tr><th>군집</th><th>건수</th><th>대표 키워드</th></tr></thead>
        <tbody>
<?php foreach ($clusterLegend as $c => $info): ?>
          <tr><td class="cl-id">C<?= (int)$c ?></td><td class="cl-n"><?= (int)$info["n_docs"] ?>건</td><td class="cl-kw"><?= htmlspecialchars(implode(", ", $info["keywords"]), ENT_QUOTES, "UTF-8") ?></td></tr>
<?php endforeach; ?>
        </tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <h2>실험 설계 : 프롬프트 3종</h2>
    <p class="sub">후보 라벨(KMST 택소노미)과 JSON 출력 계약은 세 변형 모두 동일합니다. 공정 비교를 위해 "전문가 관점" 서술만 다르게 했습니다.</p>
    <div class="variant-cards" id="variant-cards">
      <div class="variant-card"><h3>불러오는 중…</h3><p></p></div>
    </div>
  </section>

  <section class="card">
    <h2>실험 아키텍처</h2>
    <p class="sub">기존 STEP4 6단계 파이프라인은 변경하지 않았습니다. 3.프롬프트 엔지니어링과 4~5.게이트웨이/추론 단계만 각각 3갈래·2갈래로 나뉘어 6칸이 되고, 6.출력 가드레일을 통과한 결과가 이 페이지(신규 7단계: 실험 집계)로 모입니다.</p>
    <div class="arch-svg-wrap" id="arch-svg-wrap"><!-- SVG injected by JS --></div>
  </section>

  <section class="card">
    <h2>6칸 진행률 · 안정도</h2>
    <p class="sub">
      "안정도"는 같은 (모델, 군집) 조합을 여러 번 반복 실행했을 때 가장 많이 나온 라벨(최빈값)의 비율입니다.
      기존 다중모델 비교 탭과 같은 기준(70% 이상 초록=안정적, 40~70% 노랑, 40% 미만 빨강)을 그대로 씁니다.
      칸을 눌러 모델x군집 상세 히트맵을 펼칠 수 있습니다.
    </p>
    <div class="matrix-grid-wrap">
      <div class="matrix-grid" id="matrix-grid">
        <div class="col-head"></div>
        <div class="col-head">A(근거 대조형)</div><div class="col-head">B(인과사슬형)</div><div class="col-head">C(배제법형)</div>
      </div>
    </div>
  </section>

  <section class="card">
    <h2>온도 효과 · 프롬프트 효과 비교</h2>
    <p class="sub">6칸을 각각 하나의 관측치로 단순 평균한 탐색적 비교입니다(칸별 표본 수 편차가 있어 엄밀한 통계 검정은 아닙니다). "안정도"는 같은 모델을 반복 실행했을 때 라벨이 안 흔들리는 정도(모델 내부 재현성)이고, "모델 간 합의"는 서로 다른 모델끼리 같은 군집에 같은 라벨을 붙이는 정도로 서로 다른 축입니다.</p>
    <div class="compare-cols">
      <div>
        <div class="bar-row" style="font-weight:700; margin-bottom:10px;"><span>Temperature별 평균 안정도</span></div>
        <div id="bars-temperature"></div>
      </div>
      <div>
        <div class="bar-row" style="font-weight:700; margin-bottom:10px;"><span>프롬프트별 평균 안정도</span></div>
        <div id="bars-variant"></div>
      </div>
      <div>
        <div class="bar-row" style="font-weight:700; margin-bottom:10px;"><span>Temperature별 모델 간 합의</span></div>
        <div id="bars-temperature-cross"></div>
      </div>
      <div>
        <div class="bar-row" style="font-weight:700; margin-bottom:10px;"><span>프롬프트별 모델 간 합의</span></div>
        <div id="bars-variant-cross"></div>
      </div>
    </div>
  </section>

  <section class="card">
    <h2>근거 문장 규칙 준수 리포트</h2>
    <p class="sub">모든 프롬프트 변형 마지막 줄에 "그 근거를 한국어 2문장 이내로 설명해 주세요"를 공통으로 넣었습니다. 모델이 이 지시를 실제로 지키는지 모델별로 집계합니다.</p>
    <div id="rationale-report">
      <div class="mm-summary-pending" id="rationale-report-pending">실험이 아직 진행 중입니다. 6칸이 모두 채워지면 이 자리에 근거 문장 리포트가 표시됩니다.</div>
      <div id="rationale-report-content" style="display:none">
        <div class="crosstab-wrap">
          <table class="crosstab" id="rationale-report-table"></table>
        </div>
        <div id="rationale-report-examples"></div>
      </div>
    </div>
  </section>

  <section class="card">
    <h2>6칸 상세 비교 : 군집별</h2>
    <p class="sub">군집(C0~C5)마다, 3개 모델이 6칸(프롬프트 A/B/C × T0.0/0.2) 각각에서 어떤 라벨을 냈고 얼마나 안정적이었는지 한 표에 모았습니다. 같은 (모델, 군집) 행을 가로로 훑으면 프롬프트·temperature를 바꿀 때 라벨이 실제로 바뀌는지 바로 보입니다.</p>
    <label class="cluster-compare-filter-label">Temperature 필터
      <select id="cluster-compare-temp-filter">
        <option value="all">전체 (T0.0 + T0.2, 6칸)</option>
        <option value="0.0">T=0.0만 (3칸: A·B·C)</option>
        <option value="0.2">T=0.2만 (3칸: A·B·C)</option>
      </select>
    </label>
    <p class="cell-note temp-explainer" style="margin-top:-6px;">
      <span class="temp-explainer-line"><b>T=0.0</b>: 결정적(탐욕적) 디코딩입니다. 매 단계 확률이 가장 높은 토큰만 고릅니다. 같은 입력엔 이론상 항상 같은 답이 나와야 해서 "모델이 원래 얼마나 일관된가"를 봅니다.</span>
      <span class="temp-explainer-line"><b>T=0.2</b>: 약간의 확률적 다양성을 허용합니다. 매번 조금씩 다른 답이 나올 수 있어, 그 요동 폭 자체가 "이 라벨을 얼마나 신뢰할 수 있는가"의 참고 신호가 됩니다.</span>
    </p>
    <div id="cluster-comparison"></div>
  </section>

  <section class="card">
    <h2>최종 요약 : 6칸 한눈에 보기</h2>
    <p class="sub">점(또는 막대) 하나 = 칸 하나(프롬프트×temperature 조합). "안정도"는 같은 모델을 반복 실행해도 라벨이 안 흔들리는 정도, "모델 간 합의"는 서로 다른 모델끼리 같은 라벨을 붙이는 정도입니다.</p>

    <div class="final-legend-recap">
      <div class="final-legend-col">
        <div class="final-legend-title">군집(Cluster)이 뭔지</div>
        <div id="final-legend-clusters" class="final-legend-body"></div>
      </div>
      <div class="final-legend-col">
        <div class="final-legend-title">프롬프트 A/B/C 차이</div>
        <div id="final-legend-variants" class="final-legend-body"></div>
      </div>
    </div>

    <label class="cluster-compare-filter-label">보기 방식
      <select id="final-summary-view-select">
        <option value="scatter">산점도 (안정도 × 모델간합의)</option>
        <option value="bar">막대그래프 (칸별 두 지표 비교)</option>
      </select>
    </label>
    <p class="cell-note" style="margin-top:-6px;">
      산점도는 <b>오른쪽 위에 가까울수록</b> 두 기준 다 좋은 조합인지 위치로 보여주고, 막대그래프는 칸마다 두 지표의 정확한 크기 차이를 비교하기 쉽습니다(값이 서로 비슷할 때는 막대그래프가 더 잘 보입니다). 점선/기준선은 기존 70%/40% 기준입니다.
    </p>
    <div class="arch-svg-wrap" id="final-summary-svg"><!-- SVG injected by JS --></div>
    <div id="final-summary-ranking"></div>
  </section>

  <footer class="foot">
    <span id="footer-status">불러오는 중…</span>
  </footer>
</div>

<script>
const API = "api_prompt_matrix_stability.php";
const VARIANTS = ["A", "B", "C"];
const TEMPERATURES = [0.0, 0.2];
// PHP에서 이미 계산한 군집 안내(건수·대표 키워드)를 그대로 재사용 — "6칸 상세 비교" 표
// 제목에 "C0 — 액상, 중량, ..." 식으로 붙이기 위함.
const CLUSTER_LEGEND = <?= json_encode($clusterLegend, JSON_UNESCAPED_UNICODE) ?>;
// A/B/C만 보면 뭐가 뭔지 못 알아보니, 짧은 별칭을 라벨 옆에 괄호로 붙인다(전체 설명은
// "실험 설계" 카드의 variant-cards에 이미 있음 — 여긴 표·다이어그램용 짧은 태그).
const VARIANT_SHORT = { A: "근거 대조형", B: "인과사슬형", C: "배제법형" };
const VARIANT_COLOR = { A: "var(--series-1)", B: "var(--series-2)", C: "var(--series-3)" };
const REFRESH_MS = 20000;

function pct(x) { return x === null || x === undefined ? "—" : Math.round(x * 100) + "%"; }
function tier(ratio) {
  if (ratio === null || ratio === undefined) return "none";
  if (ratio >= 0.7) return "ok";
  if (ratio >= 0.4) return "warn";
  return "bad";
}
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

function renderArchSvg(data) {
  const el = document.getElementById("arch-svg-wrap");
  const cellId = (v, t) => `${v}_${t.toFixed(1)}`;
  const cellStability = (v, t) => data.cells?.[cellId(v, t)]?.cell_stability_avg ?? null;
  const tierColor = { ok: "var(--ok-fg)", warn: "var(--warn-fg)", bad: "var(--bad-fg)", none: "var(--ink-500)" };
  const target = data.target_per_cell ?? 30;

  // 세로 좌표(위→아래로 순서대로): 헤더 박스 → 분기 막대 → 프롬프트 A/B/C 박스 →
  // temperature 셀 2행 → 병합 막대 → Output Guardrail 박스. 각 구간의 시작/끝 y를
  // 변수로 미리 정해두고 모든 선·박스가 그 값만 참조하게 해서, 박스 위치를 바꿔도
  // 연결선이 항상 정확히 이어지게 한다(이전 버전은 값을 하드코딩해 셀 2행과 하단
  // 병합선의 y가 어긋나 있었다).
  const headerTop = 24, headerH = 70, headerBottom = headerTop + headerH; // 24..94
  const branchY = headerBottom + 30; // 124
  const variantTop = branchY + 20, variantH = 40, variantBottom = variantTop + variantH; // 144..184
  const cellH = 60, cellGap = 26;
  const cell0Top = variantBottom + 24; // 208
  const cell0Bottom = cell0Top + cellH; // 268
  const cell1Top = cell0Bottom + cellGap; // 294
  const cell1Bottom = cell1Top + cellH; // 354
  const mergeY = cell1Bottom + 26; // 380
  const guardTop = mergeY + 20, guardH = 34, guardBottom = guardTop + guardH; // 400..434
  const svgH = guardBottom + 20; // 454, 여유 20px

  const colX = VARIANTS.map((_, i) => 330 + i * 190); // 각 컬럼 박스 left
  const colCenterX = colX.map(x => x + 80);
  const leftMost = colCenterX[0], rightMost = colCenterX[colCenterX.length - 1];
  // 세로 줄기(헤더→분기, 병합→Output Guardrail)는 캔버스 중앙(620)이 아니라 실제
  // 3개 컬럼의 중앙에 맞춰야 시각적으로 어긋나 보이지 않는다. 위/아래 박스도 이
  // 값에 맞춰 다시 가운데 정렬한다.
  const groupCenter = (leftMost + rightMost) / 2; // (410+790)/2 = 600

  let branches = "";
  colCenterX.forEach(cx => {
    branches += `<line x1="${cx}" y1="${branchY}" x2="${cx}" y2="${variantTop}" stroke="var(--line-strong)"/>`;
  });
  let merges = "";
  colCenterX.forEach(cx => {
    merges += `<line x1="${cx}" y1="${cell1Bottom}" x2="${cx}" y2="${mergeY}" stroke="var(--line-strong)"/>`;
  });

  let variantCols = "";
  VARIANTS.forEach((v, i) => {
    const x = colX[i], cx = colCenterX[i];
    variantCols += `<rect x="${x}" y="${variantTop}" width="160" height="${variantH}" rx="7" fill="var(--panel)" stroke="var(--line-strong)"/>
      <text x="${cx}" y="${variantTop + 25}" text-anchor="middle" font-size="13" font-weight="700" fill="var(--sea-700)">프롬프트 ${v}(${VARIANT_SHORT[v]})</text>`;

    TEMPERATURES.forEach((t, j) => {
      const yTop = j === 0 ? cell0Top : cell1Top;
      const s = cellStability(v, t);
      const c = tierColor[tier(s)];
      variantCols += `${j === 0
          ? `<line x1="${cx}" y1="${variantBottom}" x2="${cx}" y2="${cell0Top}" stroke="var(--line-strong)"/>`
          : `<line x1="${cx}" y1="${cell0Bottom}" x2="${cx}" y2="${cell1Top}" stroke="var(--line-strong)"/>`}
        <rect x="${x}" y="${yTop}" width="160" height="${cellH}" rx="7" fill="var(--paper-50)" stroke="var(--line-strong)"/>
        <text x="${cx}" y="${yTop+20}" text-anchor="middle" font-size="11.5" font-weight="700" fill="var(--ink-900)">T=${t.toFixed(1)} · 3모델×${target}회</text>
        <text x="${cx}" y="${yTop+42}" text-anchor="middle" font-size="16" font-weight="700" fill="${c}">${pct(s)}</text>`;
    });
  });

  const headerW = 1200, headerX = groupCenter - headerW / 2;
  const guardW = 360, guardX = groupCenter - guardW / 2;

  el.innerHTML = `<svg viewBox="0 0 1240 ${svgH}" width="100%" style="min-width:900px" xmlns="http://www.w3.org/2000/svg">
    <rect x="${headerX}" y="${headerTop}" width="${headerW}" height="${headerH}" rx="8" fill="var(--sea-100)" stroke="var(--line-strong)"/>
    <text x="${headerX+20}" y="${headerTop+25}" font-size="12" fill="var(--ink-700)" font-weight="700">기존 STEP4 파이프라인 (변경 없음)</text>
    <text x="${headerX+20}" y="${headerTop+45}" font-size="11.5" fill="var(--ink-500)">1.데이터·클러스터링 → 2.Grounding&amp;Context → 3.Prompt Engineering → 4.Gateway → 5A/5B.Cloud/Local 추론 → 6.Output Guardrail</text>

    <line x1="${groupCenter}" y1="${headerBottom}" x2="${groupCenter}" y2="${branchY}" stroke="var(--line-strong)"/>
    <line x1="${leftMost}" y1="${branchY}" x2="${rightMost}" y2="${branchY}" stroke="var(--line-strong)"/>
    <text x="${groupCenter}" y="${branchY-6}" text-anchor="middle" font-size="10.5" fill="var(--ink-500)">3+4단계만 6갈래로 분기</text>
    ${branches}

    ${variantCols}

    ${merges}
    <line x1="${leftMost}" y1="${mergeY}" x2="${rightMost}" y2="${mergeY}" stroke="var(--line-strong)"/>
    <line x1="${groupCenter}" y1="${mergeY}" x2="${groupCenter}" y2="${guardTop}" stroke="var(--line-strong)"/>
    <rect x="${guardX}" y="${guardTop}" width="${guardW}" height="${guardH}" rx="7" fill="var(--sea-100)" stroke="var(--line-strong)"/>
    <text x="${groupCenter}" y="${guardTop+22}" text-anchor="middle" font-size="12" font-weight="700" fill="var(--sea-700)">6. Output Guardrail (공통, 변경 없음)</text>
  </svg>`;
}

function renderVariantCards(info) {
  const el = document.getElementById("variant-cards");
  el.innerHTML = VARIANTS.map(v => {
    const meta = info?.[v] || {};
    return `<div class="variant-card"><h3>${esc(meta.label || v)}</h3><p>${esc(meta.persona || "")}</p></div>`;
  }).join("");
}

function renderTopBadges(data) {
  const el = document.getElementById("top-badges");
  const totalRuns = Object.values(data.cells || {}).reduce((a, c) => a + Math.min(c.n_runs || 0, c.target || 0), 0);
  const totalTarget = 6 * (data.target_per_cell || 30);
  el.innerHTML = `
    <span class="badge">목표: 6칸 × ${data.target_per_cell}회 = ${totalTarget}회</span>
    <span class="badge">현재 누적: ${totalRuns}회</span>
    <span class="badge warn">제외 모델: ${(data.excluded_models||[]).join(", ")}</span>
  `;
}

// 레거시 "여러 번 실행한 기록 종합"(report_template.html의 renderStabilityReport)과 같은
// 표현 방식 — 칸 안 큰 글자는 최빈 라벨, 아래 작은 글자는 "count/total회 (비율%)".
// 마지막 행("모델 간 합의")만 이 실험에서 새로 추가한 것으로, 같은 군집에서 모델끼리
// 서로 얼마나 같은 라벨에 동의했는지(모델 내부 재현성과는 다른 축)를 보여준다.
function renderHeat(cellData) {
  if (!cellData || !cellData.models || !cellData.models.length) {
    return `<p class="cell-note">아직 저장된 기록이 없습니다.</p>`;
  }
  const clusters = cellData.clusters || [];
  let html = `<div class="heat-wrap"><table class="heat stab"><thead><tr><th class="rowhead">모델 \\ 군집</th>${clusters.map(c => `<th>C${c}</th>`).join("")}</tr></thead><tbody>`;
  cellData.models.forEach(m => {
    html += `<tr><td class="rowhead">${esc(m.split("/").pop())}</td>`;
    clusters.forEach(c => {
      const s = cellData.stats?.[m]?.[String(c)];
      if (!s) { html += `<td class="heat-none">—</td>`; return; }
      const ratio = s.total ? s.count / s.total : null;
      const cls = "heat-" + tier(ratio);
      html += `<td class="${cls}" title="${esc(s.mode)}: ${s.count}/${s.total}회">${esc(s.mode)}<span class="stab-pct">${s.count}/${s.total}회 (${pct(ratio)})</span></td>`;
    });
    html += `</tr>`;
  });
  html += `<tr class="cross-model-row"><td class="rowhead">모델 간 합의</td>`;
  clusters.forEach(c => {
    const cm = cellData.cross_model && cellData.cross_model[c];
    if (!cm) { html += `<td class="heat-none">—</td>`; return; }
    const cls = "heat-" + tier(cm.ratio);
    html += `<td class="${cls}" title="${esc(cm.consensus)}: ${cm.agree}/${cm.total}개 모델 일치">${esc(cm.consensus)}<span class="stab-pct">${cm.agree}/${cm.total}개 모델 (${pct(cm.ratio)})</span></td>`;
  });
  html += `</tr>`;
  html += `</tbody></table></div>`;
  return html;
}

function renderMatrixGrid(data) {
  const grid = document.getElementById("matrix-grid");
  // 헤더(첫 4개 노드)는 고정 마크업에 이미 있으므로, 그 뒤부터 다시 그린다.
  while (grid.children.length > 4) grid.removeChild(grid.lastChild);

  TEMPERATURES.forEach(t => {
    const rowHead = document.createElement("div");
    rowHead.className = "row-head";
    rowHead.textContent = `T=${t.toFixed(1)}`;
    grid.appendChild(rowHead);

    VARIANTS.forEach(v => {
      const key = `${v}_${t.toFixed(1)}`;
      const c = data.cells?.[key];
      const s = c?.cell_stability_avg ?? null;
      const wrap = document.createElement("div");
      wrap.className = `cell-card tier-${tier(s)}`;
      const progressPct = c ? Math.round((c.progress || 0) * 100) : 0;
      // 목표를 낮춘 뒤에도 이전 목표 기준으로 이미 쌓인 회차가 있으면 n_runs가 target을
      // 넘을 수 있다(예: 목표를 30→5로 낮췄는데 그 전에 8회를 채운 칸). 통계 계산(cell_stability_avg)은
      // 실제 표본 전부를 그대로 쓰지만, 진행률 표시는 다른 칸들과 헷갈리지 않도록 목표치에서 자른다.
      const shownRuns = c ? Math.min(c.n_runs, c.target) : 0;
      wrap.innerHTML = `
        <div class="cell-title">프롬프트 ${v}(${VARIANT_SHORT[v]}) · T${t.toFixed(1)}</div>
        <div class="cell-progress-track"><div class="cell-progress-bar" style="width:${progressPct}%"></div></div>
        <div class="cell-meta"><span>${shownRuns} / ${c?.target ?? 30}회</span><span>${progressPct}%</span></div>
        <div class="cell-stability">${pct(s)}</div>
        <div class="cell-note">평균 최빈라벨 비율</div>
        <details class="cell-detail" open><summary>모델×군집 상세(라벨·안정도·모델 간 합의)</summary>${renderHeat(c)}</details>
      `;
      grid.appendChild(wrap);
    });
  });
}

function renderBars(container, obj, labelFn) {
  const el = document.getElementById(container);
  const keys = Object.keys(obj);
  el.innerHTML = keys.map(k => {
    const v = obj[k];
    const w = v === null ? 0 : Math.round(v * 100);
    return `<div class="bar-row">
      <span class="bar-label" title="${esc(labelFn(k))}">${esc(labelFn(k))}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${w}%"></span></span>
      <span class="bar-val">${pct(v)}</span>
    </div>`;
  }).join("");
}

// 모든 칸이 목표치를 채웠는지(목표를 낮춘 뒤에도 안전하도록 칸별로 min(n_runs,target)해서 합산).
function isMatrixComplete(data) {
  const totalTarget = 6 * (data.target_per_cell || 0);
  const totalRuns = Object.values(data.cells || {}).reduce((a, c) => a + Math.min(c.n_runs || 0, c.target || 0), 0);
  return { done: totalTarget > 0 && totalRuns >= totalTarget, totalRuns, totalTarget };
}

function renderRationaleReport(data) {
  const pendingEl = document.getElementById("rationale-report-pending");
  const contentEl = document.getElementById("rationale-report-content");
  const { done, totalRuns, totalTarget } = isMatrixComplete(data);
  if (!done) {
    pendingEl.style.display = "";
    pendingEl.textContent = `실험이 아직 진행 중입니다 (${totalRuns} / ${totalTarget}회차). 6칸이 모두 채워지면 이 자리에 근거 문장 리포트가 표시됩니다.`;
    contentEl.style.display = "none";
    return;
  }
  pendingEl.style.display = "none";
  contentEl.style.display = "";

  const maxN = data.rationale_max_sentences ?? 2;
  const rc = data.rationale_compliance || {};
  const rows = Object.keys(rc).map(model => {
    const r = rc[model];
    const cls = "heat-" + tier(r.compliance_rate);
    return `<tr><td class="ct-rowhead">${esc(model.split("/").pop())}</td>
      <td>${r.n_total}건</td>
      <td class="${cls}">${pct(r.compliance_rate)}</td>
      <td>${r.avg_sentences?.toFixed(2) ?? "—"}</td>
      <td>${r.max_sentences ?? "—"}</td></tr>`;
  }).join("");
  document.getElementById("rationale-report-table").innerHTML =
    `<thead><tr><th>모델</th><th>표본(rationale) 수</th><th>${maxN}문장 이내 비율</th><th>평균 문장 수</th><th>최대 문장 수</th></tr></thead><tbody>${rows}</tbody>`;

  // 숫자만으로는 실제로 뭐라고 썼는지 보이지 않는다는 피드백 반영 — 모델별로 실제
  // rationale 예시(위반 사례 우선, 짧은 사례로 대비)를 그대로 보여준다.
  const exEl = document.getElementById("rationale-report-examples");
  exEl.innerHTML = Object.keys(rc).map(model => {
    const examples = rc[model].examples || [];
    if (!examples.length) return "";
    const cards = examples.map(ex => {
      const badgeCls = ex.compliant ? "heat-ok" : "heat-bad";
      const t = (typeof ex.temperature === "number") ? ex.temperature.toFixed(1) : ex.temperature;
      return `<div class="rationale-example">
        <div class="rationale-example-tags">
          <span class="badge ${badgeCls}">${ex.sentence_count}문장 ${ex.compliant ? "(준수)" : "(위반)"}</span>
          <span class="badge">${esc(ex.prompt_variant)}(${esc(VARIANT_SHORT[ex.prompt_variant] || "")}) · T${esc(t)}</span>
          <span class="badge">C${esc(ex.cluster)} · ${esc(ex.proposed_label || "")}</span>
        </div>
        <div class="rationale-example-text">${esc(ex.rationale)}</div>
      </div>`;
    }).join("");
    return `<details class="cell-detail" style="margin-top:14px;">
      <summary>${esc(model.split("/").pop())} : rationale 예시 ${examples.length}건 보기</summary>
      ${cards}
    </details>`;
  }).join("");
}

let lastData = null; // temperature 필터를 새로 fetch 없이 즉시 다시 그리기 위한 캐시

function renderClusterComparison(data) {
  lastData = data;
  const el = document.getElementById("cluster-comparison");
  const cc = data.cluster_comparison;
  if (!cc || !Object.keys(cc).length) { el.innerHTML = `<p class="cell-note">아직 데이터가 없습니다.</p>`; return; }

  const tempFilter = document.getElementById("cluster-compare-temp-filter")?.value || "all";
  const tempsToShow = tempFilter === "all" ? TEMPERATURES : TEMPERATURES.filter(t => t.toFixed(1) === tempFilter);
  const cellKeys = [];
  VARIANTS.forEach(v => tempsToShow.forEach(t => cellKeys.push(`${v}_${t.toFixed(1)}`)));

  el.innerHTML = Object.keys(cc).sort((a, b) => Number(a) - Number(b)).map(cluster => {
    const byModel = cc[cluster];
    const legend = CLUSTER_LEGEND[cluster];
    const legendText = legend ? `${legend.n_docs}건 · ${legend.keywords.join(", ")}` : "";
    const headCols = cellKeys.map(k => {
      const [v, t] = k.split("_");
      return `<th>${v}(${VARIANT_SHORT[v]})<br>T=${t}</th>`;
    }).join("");
    const rows = Object.keys(byModel).map(model => {
      const cells = cellKeys.map(k => {
        const s = byModel[model][k];
        if (!s) return `<td class="heat-none">—</td>`;
        const cls = "heat-" + tier(s.ratio);
        return `<td class="${cls}" title="${esc(s.mode)}: ${s.count}/${s.total}회">${esc(s.mode)}<span class="stab-pct">${pct(s.ratio)}</span></td>`;
      }).join("");
      return `<tr><td class="rowhead">${esc(model.split("/").pop())}</td>${cells}</tr>`;
    }).join("");
    return `<div class="cluster-compare-card">
      <h3>C${esc(cluster)}${legendText ? " : " + esc(legendText) : ""}</h3>
      <div class="heat-wrap"><table class="heat compare6"><thead><tr><th class="rowhead">모델 \\ 칸</th>${headCols}</tr></thead><tbody>${rows}</tbody></table></div>
    </div>`;
  }).join("");
}

function renderFinalLegendRecap(data) {
  const clusterEl = document.getElementById("final-legend-clusters");
  const variantEl = document.getElementById("final-legend-variants");
  if (clusterEl) {
    clusterEl.innerHTML = Object.keys(CLUSTER_LEGEND).sort((a, b) => Number(a) - Number(b)).map(c => {
      const info = CLUSTER_LEGEND[c];
      return `<div class="fl-row"><span class="fl-chip">C${esc(c)}</span>${esc(info.n_docs)}건 · ${esc(info.keywords.slice(0, 4).join(", "))}</div>`;
    }).join("");
  }
  if (variantEl) {
    const info = data.variant_info || {};
    variantEl.innerHTML = VARIANTS.map(v => {
      const meta = info[v] || {};
      return `<div class="fl-row"><span class="fl-chip" style="color:${VARIANT_COLOR[v]}">${v}(${VARIANT_SHORT[v]})</span>${esc(meta.persona || "")}</div>`;
    }).join("");
  }
}

function collectFinalPoints(data) {
  const points = [];
  VARIANTS.forEach(v => TEMPERATURES.forEach(t => {
    const key = `${v}_${t.toFixed(1)}`;
    const c = data.cells && data.cells[key];
    if (!c || c.cell_stability_avg === null || c.cell_stability_avg === undefined) return;
    points.push({
      key, variant: v, temperature: t,
      x: c.cell_stability_avg, y: c.cell_cross_model_avg ?? 0,
      n: c.n_runs,
    });
  }));
  return points;
}

// 산점도 — 오른쪽 위에 가까울수록 두 기준 다 좋은 조합. 값이 한쪽에 몰려 있으면(예: 안정도가
// 다들 80%대 이상) 라벨이 차트 오른쪽 바깥으로 튀어나가 잘리므로, 점이 플롯 폭의 78%를 넘는
// 위치에 있으면 라벨을 점 왼쪽으로 뒤집어서 붙인다.
function renderScatterView(el, points) {
  const W = 720, H = 560, padL = 56, padR = 30, padT = 20, padB = 50;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const sx = v => padL + v * plotW;
  const sy = v => padT + (1 - v) * plotH;
  const colorFor = v => VARIANT_COLOR[v];

  let gridSvg = "";
  [0, 0.4, 0.7, 1].forEach(f => {
    gridSvg += `<line x1="${sx(f)}" y1="${padT}" x2="${sx(f)}" y2="${H - padB}" stroke="var(--line)" stroke-dasharray="${f===0||f===1?"0":"4 3"}"/>`;
    gridSvg += `<line x1="${padL}" y1="${sy(f)}" x2="${W - padR}" y2="${sy(f)}" stroke="var(--line)" stroke-dasharray="${f===0||f===1?"0":"4 3"}"/>`;
    gridSvg += `<text x="${sx(f)}" y="${H - padB + 16}" text-anchor="middle" font-size="10" fill="var(--ink-500)">${Math.round(f*100)}%</text>`;
    gridSvg += `<text x="${padL - 8}" y="${sy(f)+3}" text-anchor="end" font-size="10" fill="var(--ink-500)">${Math.round(f*100)}%</text>`;
  });

  // 라벨 겹침 방지: 점이 몰려 있으면 초기 위치(점 옆)에 그냥 붙였을 때 서로 겹치거나
  // 차트 오른쪽 바깥으로 잘려나간다(실측 문제). 그래서 (1) 점이 플롯 오른쪽 78% 밖에
  // 있으면 라벨을 점 왼쪽으로 뒤집어 붙이고, (2) 그렇게 정한 라벨 상자가 이미 배치된
  // 다른 라벨과 겹치면 겹치지 않을 때까지 세로로 한 줄씩(14px) 밀어낸다 — 점이 몇 개든
  // 태그 글씨가 항상 온전히 보이게 하기 위함이다.
  const CHAR_W = 7.3, LABEL_PAD = 6, LINE_H = 14;
  const estWidth = s => s.length * CHAR_W + LABEL_PAD;
  const placedBoxes = []; // {left, right, top, bottom}
  const overlaps = (a, b) => a.left < b.right + 3 && a.right > b.left - 3 && a.top < b.bottom + 2 && a.bottom > b.top - 2;

  // 점이 많은 좁은 영역(오른쪽 위쪽 등)부터 처리해야 서로 잘 비켜간다 — x 기준 정렬.
  const ordered = points.slice().sort((a, b) => sx(b.x) - sx(a.x));

  let pointsSvg = "";
  ordered.forEach(p => {
    const cx = sx(p.x), cy = sy(p.y);
    const shortLabel = `${p.variant}·T${p.temperature.toFixed(1)}`;
    const w = estWidth(shortLabel);
    const nearRightEdge = cx > padL + plotW * 0.78;
    const labelAnchor = nearRightEdge ? "end" : "start";
    const baseX = cx + (nearRightEdge ? -10 : 10);

    // 세로로 한 줄씩 밀어보며(점과 가까운 순서: 0, +1줄, -1줄, +2줄, -2줄 …) 안 겹치는
    // 자리를 찾는다. 6개 남짓한 점에서 몇 줄만 시도해도 충분하다.
    let chosenY = cy + 4;
    for (let step = 0; step < 12; step++) {
      const dir = step === 0 ? 0 : (step % 2 === 1 ? Math.ceil(step / 2) : -Math.ceil(step / 2));
      const tryY = cy + 4 + dir * LINE_H;
      const box = labelAnchor === "start"
        ? { left: baseX, right: baseX + w, top: tryY - 11, bottom: tryY + 3 }
        : { left: baseX - w, right: baseX, top: tryY - 11, bottom: tryY + 3 };
      if (!placedBoxes.some(b => overlaps(b, box))) {
        chosenY = tryY;
        placedBoxes.push(box);
        break;
      }
      if (step === 11) { chosenY = tryY; placedBoxes.push(box); } // 그래도 못 찾으면 마지막 시도 자리라도 씀
    }

    pointsSvg += `
      <circle cx="${cx}" cy="${cy}" r="7" fill="${colorFor(p.variant)}" stroke="var(--panel)" stroke-width="2">
        <title>${shortLabel}: 안정도 ${pct(p.x)}, 모델간합의 ${pct(p.y)} (${p.n}회)</title>
      </circle>
      ${chosenY !== cy + 4 ? `<line x1="${cx}" y1="${cy}" x2="${labelAnchor === "start" ? baseX - 2 : baseX + 2}" y2="${chosenY - 3}" stroke="var(--ink-500)" stroke-width="1"/>` : ""}
      <text x="${baseX}" y="${chosenY}" text-anchor="${labelAnchor}" font-size="11" font-weight="700" fill="var(--ink-900)">${shortLabel}</text>`;
  });

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="min-width:520px; max-width:760px;" xmlns="http://www.w3.org/2000/svg">
    ${gridSvg}
    <line x1="${padL}" y1="${H-padB}" x2="${W-padR}" y2="${H-padB}" stroke="var(--line-strong)"/>
    <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H-padB}" stroke="var(--line-strong)"/>
    <text x="${(padL + W - padR) / 2}" y="${H - 8}" text-anchor="middle" font-size="12" font-weight="700" fill="var(--ink-700)">안정도 (모델 내부 재현성) →</text>
    <text x="16" y="${(padT + H - padB) / 2}" text-anchor="middle" font-size="12" font-weight="700" fill="var(--ink-700)" transform="rotate(-90 16 ${(padT + H - padB) / 2})">모델 간 합의 →</text>
    ${pointsSvg}
  </svg>`;
}

// 막대그래프 — 값이 서로 몰려 있어(예: 81~100% vs 41~53%) 산점도에서 차이가 잘 안 보일 때,
// 칸마다 두 지표를 나란히 세워 정확한 크기 차이를 비교하기 쉽게 한다.
function renderBarView(el, points) {
  const barW = 16, groupGap = 34, leftPad = 60, topPad = 24, bottomPad = 70, rightPad = 20;
  const plotH = 340;
  const groupW = barW * 2 + 6;
  const W = leftPad + rightPad + points.length * (groupW + groupGap);
  const H = topPad + plotH + bottomPad;
  const sy = v => topPad + (1 - v) * plotH;
  const yAxis = [0, 0.4, 0.7, 1];

  let gridSvg = "";
  yAxis.forEach(f => {
    gridSvg += `<line x1="${leftPad}" y1="${sy(f)}" x2="${W - rightPad}" y2="${sy(f)}" stroke="var(--line)" stroke-dasharray="${f===0?"0":"4 3"}"/>`;
    gridSvg += `<text x="${leftPad - 8}" y="${sy(f)+3}" text-anchor="end" font-size="10" fill="var(--ink-500)">${Math.round(f*100)}%</text>`;
  });

  let barsSvg = "";
  points.forEach((p, i) => {
    const gx = leftPad + i * (groupW + groupGap);
    const xStab = gx, xAgree = gx + barW + 6;
    const yStab = sy(p.x), yAgree = sy(p.y);
    const label = `${p.variant}·T${p.temperature.toFixed(1)}`;
    barsSvg += `
      <rect x="${xStab}" y="${yStab}" width="${barW}" height="${topPad + plotH - yStab}" fill="${VARIANT_COLOR[p.variant]}">
        <title>${label} 안정도: ${pct(p.x)}</title>
      </rect>
      <text x="${xStab + barW/2}" y="${yStab - 5}" text-anchor="middle" font-size="9.5" font-weight="700" fill="var(--ink-700)">${pct(p.x)}</text>
      <rect x="${xAgree}" y="${yAgree}" width="${barW}" height="${topPad + plotH - yAgree}" fill="${VARIANT_COLOR[p.variant]}" opacity="0.45">
        <title>${label} 모델간합의: ${pct(p.y)}</title>
      </rect>
      <text x="${xAgree + barW/2}" y="${yAgree - 5}" text-anchor="middle" font-size="9.5" font-weight="700" fill="var(--ink-700)">${pct(p.y)}</text>
      <text x="${gx + groupW/2}" y="${topPad + plotH + 16}" text-anchor="middle" font-size="11" font-weight="700" fill="var(--ink-900)">${label}</text>`;
  });

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="min-width:${Math.min(W,760)}px; max-width:${W}px;" xmlns="http://www.w3.org/2000/svg">
    ${gridSvg}
    <line x1="${leftPad}" y1="${topPad + plotH}" x2="${W - rightPad}" y2="${topPad + plotH}" stroke="var(--line-strong)"/>
    <line x1="${leftPad}" y1="${topPad}" x2="${leftPad}" y2="${topPad + plotH}" stroke="var(--line-strong)"/>
    ${barsSvg}
    <g transform="translate(${leftPad}, ${H - 22})">
      <rect x="0" y="-9" width="10" height="10" fill="var(--ink-700)"/>
      <text x="14" y="0" font-size="10.5" fill="var(--ink-700)">진한 막대 = 안정도</text>
      <rect x="128" y="-9" width="10" height="10" fill="var(--ink-700)" opacity="0.45"/>
      <text x="142" y="0" font-size="10.5" fill="var(--ink-700)">연한 막대 = 모델 간 합의</text>
    </g>
  </svg>`;
}

// 최종 요약: "결과를 한눈에" 보기 위한 요청 반영 — 산점도(위치로 보기)와 막대그래프(정확한
// 크기로 비교하기) 두 가지 보기 방식을 토글로 제공한다. 표·히트맵은 이미 충분히 있으니
// 여기서는 숫자를 다시 나열하지 않고 시각적으로만 보여주되, 아래 순위 리스트로 보조한다.
function renderFinalSummary(data) {
  lastData = data;
  renderFinalLegendRecap(data);
  const el = document.getElementById("final-summary-svg");
  const rankEl = document.getElementById("final-summary-ranking");
  const points = collectFinalPoints(data);

  if (!points.length) {
    el.innerHTML = `<p class="cell-note">아직 데이터가 없습니다.</p>`;
    rankEl.innerHTML = "";
    return;
  }

  const view = document.getElementById("final-summary-view-select")?.value || "scatter";
  if (view === "bar") renderBarView(el, points);
  else renderScatterView(el, points);

  // 아래에 순위 리스트도 같이 — 그래픽만으론 정확한 값 비교가 어려우니 보조.
  const colorFor = v => VARIANT_COLOR[v];
  const ranked = points.slice().sort((a, b) => (b.x + b.y) - (a.x + a.y));
  rankEl.innerHTML = `<div class="final-rank-list">` + ranked.map((p, i) => `
    <div class="final-rank-row">
      <span class="final-rank-no">${i + 1}</span>
      <span class="final-rank-dot" style="background:${colorFor(p.variant)}"></span>
      <span class="final-rank-label">${p.variant}(${VARIANT_SHORT[p.variant]}) · T${p.temperature.toFixed(1)}</span>
      <span class="final-rank-vals">안정도 ${pct(p.x)} · 모델간합의 ${pct(p.y)}</span>
    </div>`).join("") + `</div>`;
}

async function refresh() {
  try {
    const res = await fetch(API + "?_=" + Date.now());
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "알 수 없는 오류");

    renderTopBadges(data);
    renderVariantCards(data.variant_info);
    renderArchSvg(data);
    renderMatrixGrid(data);
    renderBars("bars-temperature", data.by_temperature, k => `T = ${k}`);
    renderBars("bars-variant", data.by_variant, k => (data.variant_info?.[k]?.label || k));
    renderBars("bars-temperature-cross", data.by_temperature_cross_model, k => `T = ${k}`);
    renderBars("bars-variant-cross", data.by_variant_cross_model, k => (data.variant_info?.[k]?.label || k));
    renderRationaleReport(data);
    renderClusterComparison(data);
    renderFinalSummary(data);

    document.getElementById("footer-status").textContent =
      `마지막 갱신: ${new Date(data.generated_at).toLocaleString("ko-KR")} · ${REFRESH_MS/1000}초마다 자동 갱신 · 배치 스크립트가 백그라운드에서 실행 중이면 진행률이 계속 올라갑니다.`;
  } catch (e) {
    document.getElementById("footer-status").textContent = "데이터를 불러오지 못했습니다: " + e.message;
  }
}

document.getElementById("cluster-compare-temp-filter")?.addEventListener("change", () => {
  if (lastData) renderClusterComparison(lastData); // 새로 fetch할 필요 없이 캐시된 데이터로 즉시 다시 그림
});
document.getElementById("final-summary-view-select")?.addEventListener("change", () => {
  if (lastData) renderFinalSummary(lastData);
});

refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>
