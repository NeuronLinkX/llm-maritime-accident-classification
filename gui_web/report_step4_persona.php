<?php
/**
 * STEP 4(신규) — 페르소나 체인 기반 군집 레이블링 · 정체성 유무 대응비교 뷰어.
 *
 * 구 report_step4_matrix.php(프롬프트 A/B/C x temperature 6칸 실험)와는 완전히
 * 다른 실험이다 — 이번엔 비교축이 "프롬프트 문구·모델 종류"가 아니라
 * "역할 정체성 부여 유무" 하나뿐이고, 그 대신 사실추출→원인검증→레이블확정
 * 3단계 체인을 두 조건 모두에 동일하게 적용한다. 데이터는 이 페이지가 직접
 * 만들지 않고, 별도 파이썬 CLI(`python -m step4`)가 이미 실행을 마치고 남긴
 * 산출물(outputs/step4_persona_ablation/)을 lib_step4_persona_data.php로 읽기만 한다 —
 * 이 페이지를 여는 것만으로 LLM이 호출되지 않는다.
 *
 * 실행: php -S 0.0.0.0:PORT (이 파일이 있는 디렉토리에서)
 * 접속: http://localhost:PORT/report_step4_persona.php
 */

declare(strict_types=1);

header("Content-Type: text/html; charset=utf-8");
header("Cache-Control: no-store");

require_once __DIR__ . "/lib_step4_persona_data.php";
require_once __DIR__ . "/lib_step3_data.php";

use function Step4Persona\output_exists;
use function Step4Persona\final_labels_by_cluster;
use function Step4Persona\full_chain_by_cluster;
use function Step4Persona\schema_validity;
use function Step4Persona\ablation_comparison;
use function Step4Persona\determinism_check;
use function Step4Persona\run_manifest;
use function Step4Persona\kmst_taxonomy_flat;

$hasData = output_exists();
$byCluster = $hasData ? final_labels_by_cluster() : [];
$fullChain = $hasData ? full_chain_by_cluster() : [];
$schemaValidity = $hasData ? schema_validity() : [];
$ablation = $hasData ? ablation_comparison() : [];
$determinism = $hasData ? determinism_check() : null;
$manifest = $hasData ? run_manifest() : null;
$taxonomy = kmst_taxonomy_flat();
$taxonomySet = array_flip($taxonomy);

$p03Ablation = $ablation["persona_03"] ?? null;

// STEP3 군집별 원문서 목록 — STEP4 페이지에서도 "이 군집에 실제로 어떤 문서가 있었는지"를
// 바로 볼 수 있도록 STEP3 데이터 로더를 그대로 재사용한다(CSV 파싱 로직 중복 구현 안 함).
$step3Clusters = \Step3\build_data()["clusters"] ?? [];

function fmt($x, int $nd = 3): string {
    if ($x === null || $x === "") return "N/A";
    if (is_bool($x)) return $x ? "true" : "false";
    if (is_numeric($x)) {
        $f = (float)$x;
        if (is_nan($f)) return "N/A";
        return number_format($f, $nd);
    }
    return (string)$x;
}

function tier_class(?float $rate): string {
    if ($rate === null) return "tier-none";
    if ($rate >= 0.9) return "tier-ok";
    if ($rate >= 0.6) return "tier-warn";
    return "tier-bad";
}

function numOrNull($v): ?float {
    if ($v === null || $v === "" || strtolower((string)$v) === "nan") return null;
    return is_numeric($v) ? (float)$v : null;
}

function isTrue($v): bool {
    $s = (string)($v ?? "");
    return $s === "True" || $s === "1" || $s === "true";
}

/**
 * "왜 이 군집의 on/off 결과가 같거나 다른지"를 사람이 그대로 읽고 말할 수 있는 문장 +
 * 근거 수치로 정리한다. persona_03 최종 레이블만 봐서는 알 수 없고, 앞 단계(사실 추출·
 * 원인 후보 검증)의 개수와 반복루프 재시도 이력까지 봐야 설명 가능하다(2026-08-08
 * cluster_4 분석에서 확인됨 — 재시도로 인해 on/off가 서로 다른 디코딩 파라미터로
 * 생성된 경우가 있어, 그 경우는 순수한 "정체성 효과"라고 단정할 수 없다).
 */
function cluster_diagnostics(array $stages, ?string $primaryOn, ?string $primaryOff): array {
    $stageMeta = [
        "persona_01" => ["label" => "사실·패턴 구조화", "counts" => ["n_facts" => "사실", "n_evidence" => "증거", "n_actors" => "행위자", "n_vessels" => "선박"]],
        "persona_02" => ["label" => "원인 후보 검증", "counts" => ["n_cause_candidates" => "원인후보", "n_unresolved_issues" => "미해결쟁점"]],
        "persona_03" => ["label" => "레이블 확정", "counts" => ["n_causes" => "최종원인수"]],
    ];

    $rows = [];
    $retryDiffStages = [];
    $countDiffStages = [];
    $anyInvalid = false;

    foreach ($stageMeta as $stage => $meta) {
        $on = $stages[$stage]["identity_on"] ?? null;
        $off = $stages[$stage]["identity_off"] ?? null;

        $countsOn = [];
        $countsOff = [];
        $countDiffers = false;
        foreach ($meta["counts"] as $col => $label) {
            $vOn = $on ? numOrNull($on[$col] ?? null) : null;
            $vOff = $off ? numOrNull($off[$col] ?? null) : null;
            $countsOn[] = "$label " . ($vOn === null ? "?" : (string)(int)$vOn);
            $countsOff[] = "$label " . ($vOff === null ? "?" : (string)(int)$vOff);
            if ($vOn !== null && $vOff !== null && $vOn !== $vOff) $countDiffers = true;
        }
        if ($countDiffers) $countDiffStages[] = $meta["label"];

        $retryOn = $on ? isTrue($on["repetition_retry_attempted"] ?? null) : false;
        $retryOff = $off ? isTrue($off["repetition_retry_attempted"] ?? null) : false;
        if ($retryOn !== $retryOff) $retryDiffStages[] = $meta["label"];

        $retryDesc = function (?array $row) use (&$retryDesc) {
            if (!$row || !isTrue($row["repetition_retry_attempted"] ?? null)) return "재시도 없음(1차 시도로 완료)";
            $ok = isTrue($row["repetition_retry_succeeded"] ?? null) ? "성공" : "실패";
            $ov = $row["repetition_retry_overrides"] ?? "";
            return "재시도 $ok" . ($ov !== "" ? "({$ov})" : "");
        };

        // CSV에서 빈 값(예: schema_valid=False라 taxonomy_valid를 아예 안 매긴 행)은 빈 문자열로
        // 들어오는데, PHP는 "" !== null이라 그대로 두면 "미준수"로 잘못 판정된다 — null로 정규화.
        $normTax = fn($v) => ($v === null || $v === "") ? null : $v;
        $taxOn = $on ? $normTax($on["taxonomy_valid"] ?? null) : null;
        $taxOff = $off ? $normTax($off["taxonomy_valid"] ?? null) : null;
        if ($stage === "persona_03") {
            if ($taxOn !== null && !isTrue($taxOn)) $anyInvalid = true;
            if ($taxOff !== null && !isTrue($taxOff)) $anyInvalid = true;
        }

        $rows[] = [
            "stage" => $stage, "label" => $meta["label"],
            "counts_on" => implode(" · ", $countsOn), "counts_off" => implode(" · ", $countsOff),
            "tokens_on" => $on ? ($on["completion_tokens"] ?? "-") : "-",
            "tokens_off" => $off ? ($off["completion_tokens"] ?? "-") : "-",
            "retry_on" => $retryDesc($on), "retry_off" => $retryDesc($off),
            "valid_on" => $on ? isTrue($on["schema_valid"] ?? null) : null,
            "valid_off" => $off ? isTrue($off["schema_valid"] ?? null) : null,
            "taxonomy_on" => $stage === "persona_03" ? $taxOn : null,
            "taxonomy_off" => $stage === "persona_03" ? $taxOff : null,
        ];
    }

    $labelChanged = ($primaryOn !== null && $primaryOff !== null && $primaryOn !== $primaryOff);

    $sentence = [];
    if (!$labelChanged) {
        $sentence[] = "최종 주원인이 두 조건에서 동일(\"" . ($primaryOn ?: "-") . "\")합니다.";
    } else {
        $sentence[] = "최종 주원인이 identity_on(\"" . ($primaryOn ?: "-") . "\")과 identity_off(\"" . ($primaryOff ?: "-") . "\") 사이에 다릅니다.";
    }
    if ($retryDiffStages) {
        $sentence[] = "그런데 " . implode(", ", $retryDiffStages) . " 단계에서 두 조건 중 한쪽만 반복루프 재시도(다른 frequency_penalty/repetition_penalty로 재생성)를 거쳤습니다 — 이 군집의 on/off 차이는 순수한 정체성 프레이밍 효과만으로 설명할 수 없고, 재시도로 인한 디코딩 파라미터 차이가 섞여 있습니다.";
    } elseif ($countDiffStages) {
        $sentence[] = implode(", ", $countDiffStages) . " 단계에서 두 조건이 뽑아낸 개수 자체가 달라, 그 차이가 이후 단계까지 이어진 것으로 보입니다(재시도는 없었으므로 순수 정체성 프레이밍 차이로 해석 가능).";
    } elseif ($labelChanged) {
        $sentence[] = "앞 단계 개수·재시도 이력은 두 조건이 동일한데도 최종 레이블이 달라졌습니다 — 이 경우는 정체성 문구 자체가 레이블 확정(persona_03) 단계의 판단에 직접 영향을 준 사례로 볼 수 있습니다.";
    } else {
        $sentence[] = "앞 단계 개수·재시도 이력도 전부 동일합니다 — 증거가 뚜렷해 정체성 유무와 무관하게 같은 결론으로 수렴한 것으로 해석할 수 있습니다.";
    }
    if ($anyInvalid) {
        $sentence[] = "또한 한쪽 조건의 최종 레이블에 KMST 공식 22개 세부항목에 없는 값(분류체계 미준수)이 포함돼 있어 해석에 주의가 필요합니다.";
    }

    return ["rows" => $rows, "sentence" => implode(" ", $sentence), "label_changed" => $labelChanged];
}
?>
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>STEP4 페르소나 identity ablation 리포트</title>
<style>
  :root {
    --ink-900: #1b2431; --ink-700: #3d4657; --ink-500: #69728a;
    --sea-700: #22415a; --sea-600: #305877; --sea-500: #4c7592; --sea-100: #e7dfca;
    --paper-50: #f5f1e6; --panel: #fffdf8; --line: #ddd4c0; --line-strong: #c2b89d;
    --ok-fg: #2f6b4f; --ok-bg: #e4eee0;
    --warn-fg: #8a5a1c; --warn-bg: #f3e7d0;
    --bad-fg: #9c3a2e; --bad-bg: #f5e1da;
    --shadow: 0 1px 0 rgba(27, 36, 49, 0.08);
    --on-fg: #4338CA; --off-fg: #B91C1C;
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
      --on-fg: #a5b4fc; --off-fg: #fca5a5;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--paper-50); color: var(--ink-900);
    font-family: -apple-system, "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", BlinkMacSystemFont, sans-serif;
    font-size: 15px; line-height: 1.55; -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1600px; margin: 0 auto; padding: 26px 28px 64px; }
  header.page-head { margin-bottom: 22px; }
  .eyebrow { font-size: 11.5px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--sea-600); }
  h1 { font-size: 25px; margin: 4px 0 6px; }
  .lede { color: var(--ink-700); max-width: 1200px; }
  .badge-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .badge { font-size: 11.5px; font-weight: 700; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--line-strong); color: var(--ink-700); background: var(--panel); }
  .badge.warn { color: var(--warn-fg); background: var(--warn-bg); border-color: transparent; }
  .badge.on { color: var(--on-fg); border-color: var(--on-fg); }
  .badge.off { color: var(--off-fg); border-color: var(--off-fg); }
  .model-provenance { font-size: 12px; color: var(--ink-500); margin-top: 10px; padding: 8px 12px; border: 1px dashed var(--line-strong); border-radius: 8px; max-width: 1200px; line-height: 1.5; }
  .model-provenance strong { color: var(--ink-700); }

  section.card { background: var(--panel); border: 1px solid var(--line-strong); border-radius: 10px; box-shadow: var(--shadow); padding: 20px 22px; margin-bottom: 20px; }
  section.card > h2 { font-size: 16px; margin: 0 0 4px; }
  section.card > .sub { font-size: 12.5px; color: var(--ink-500); margin: 0 0 16px; }

  .empty-notice { font-size: 13px; color: var(--ink-500); padding: 28px; text-align: center; border: 1px dashed var(--line-strong); border-radius: 8px; }

  /* 군집 비교 카드: 군집당 1행, identity_on/off 2열 */
  .cluster-compare { display: grid; grid-template-columns: 120px 1fr 1fr; gap: 10px; margin-bottom: 12px; align-items: stretch; }
  .cluster-compare .cluster-head { display: flex; flex-direction: column; justify-content: center; font-weight: 700; color: var(--sea-700); font-size: 13px; }
  .cluster-compare .cluster-head .n-docs { font-size: 11px; color: var(--ink-500); font-weight: 500; margin-top: 2px; }
  .cond-cell { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; background: var(--paper-50); }
  .cond-cell.on { border-left: 4px solid var(--on-fg); }
  .cond-cell.off { border-left: 4px solid var(--off-fg); }
  .cond-cell .cond-label { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 6px; }
  .cond-cell.on .cond-label { color: var(--on-fg); }
  .cond-cell.off .cond-label { color: var(--off-fg); }
  .cond-cell .primary-label { font-size: 15px; font-weight: 800; margin-bottom: 4px; }
  .cond-cell .cell-meta { font-size: 11px; color: var(--ink-500); display: flex; gap: 10px; flex-wrap: wrap; }
  .tag-invalid { color: var(--bad-fg); font-weight: 700; }
  .tag-off-taxonomy { color: var(--warn-fg); font-weight: 700; }
  .diff-flag { display: inline-block; margin-left: 6px; font-size: 10.5px; font-weight: 700; padding: 1px 7px; border-radius: 999px; }
  .diff-flag.changed { background: var(--warn-bg); color: var(--warn-fg); }
  .diff-flag.same { background: var(--ok-bg); color: var(--ok-fg); }

  /* ---- 근거 자료(step4-evidence) — 군집별 on/off 체인 단계 수치 펼침 목록 ---- */
  .evidence-cluster { border: 1px solid var(--line); border-radius: 8px; margin-bottom: 10px; background: var(--paper-50); overflow: hidden; }
  .evidence-cluster summary {
    cursor: pointer; padding: 12px 14px; display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
    list-style: none; background: var(--panel);
  }
  .evidence-cluster summary::-webkit-details-marker { display: none; }
  .evidence-cluster summary::before { content: "▸ "; color: var(--sea-600); }
  .evidence-cluster[open] summary::before { content: "▾ "; }
  .evidence-cluster .ec-cluster { font-weight: 800; color: var(--sea-700); font-size: 13.5px; }
  .evidence-cluster .ec-sentence { font-size: 12.5px; color: var(--ink-700); flex: 1 1 300px; }
  .evidence-wrap { padding: 4px 14px 14px; overflow-x: auto; }
  table.evidence-tbl { border-collapse: collapse; width: 100%; font-size: 12px; min-width: 720px; }
  table.evidence-tbl th, table.evidence-tbl td { padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--line); }
  table.evidence-tbl thead th { background: var(--paper-50); color: var(--ink-700); font-weight: 700; white-space: nowrap; }
  table.evidence-tbl .ec-stage { font-weight: 700; white-space: nowrap; }
  table.evidence-tbl .ec-stage-id { font-weight: 400; color: var(--ink-500); font-size: 10.5px; }

  /* 유효성 표 */
  .valid-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; }
  table.valid { border-collapse: collapse; width: 100%; font-size: 12.5px; }
  table.valid th, table.valid td { padding: 8px 12px; text-align: center; border-bottom: 1px solid var(--line); white-space: nowrap; }
  table.valid thead th { background: var(--paper-50); color: var(--ink-700); font-weight: 700; }
  table.valid td:first-child, table.valid td:nth-child(2) { text-align: left; font-weight: 600; }
  .rate-ok { color: var(--ok-fg); font-weight: 700; }
  .rate-warn { color: var(--warn-fg); font-weight: 700; }
  .rate-bad { color: var(--bad-fg); font-weight: 700; }

  /* 통계 카드 */
  .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
  .stat-box { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; background: var(--paper-50); }
  .stat-box .stat-label { font-size: 11px; color: var(--ink-500); margin-bottom: 4px; }
  .stat-box .stat-value { font-size: 19px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .stat-box .stat-note { font-size: 10.5px; color: var(--ink-500); margin-top: 4px; }

  /* 전이표 */
  .transition-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; }
  table.transition { border-collapse: collapse; width: 100%; font-size: 12px; }
  table.transition th, table.transition td { padding: 8px 10px; text-align: center; border-bottom: 1px solid var(--line); border-right: 1px solid var(--line); }
  table.transition thead th { background: var(--paper-50); font-weight: 700; }
  table.transition td.diag { background: var(--ok-bg); color: var(--ok-fg); font-weight: 700; }
  table.transition td.off-diag.nonzero { background: var(--warn-bg); color: var(--warn-fg); font-weight: 700; }
  table.transition th.rowhead { text-align: left; background: var(--paper-50); font-weight: 700; }

  footer.page-foot { color: var(--ink-500); font-size: 12px; margin-top: 30px; }
  a { color: var(--sea-600); }

  /* ---- 군집별 원문서 목록(step3-file-browser) — report_template.html의 STEP1/3 파일
     표와 같은 필터·검색·페이지네이션 UI를 이 독립 페이지에서도 쓰기 위해 필요한 CSS만
     옮겨왔다. */
  .controls { display: flex; flex-direction: column; gap: 10px; padding: 4px 0 10px; }
  .search { position: relative; }
  .search input {
    width: 100%; padding: 9px 12px 9px 32px; border-radius: 5px;
    border: 1px solid var(--line-strong); background: var(--panel); color: var(--ink-900);
    font-size: 14px; outline: none; box-sizing: border-box;
  }
  .search input:focus { border-color: var(--sea-500); box-shadow: 0 0 0 3px var(--sea-100); }
  .search::before {
    content: "⌕"; position: absolute; left: 11px; top: 50%; transform: translateY(-50%);
    color: var(--ink-500); font-size: 15px;
  }
  .filter-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .filter-row label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--ink-500); margin-right: -4px; }
  .pagesize-group { display: inline-flex; align-items: center; gap: 8px; margin-left: auto; }
  .pagesize-group label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--ink-500); margin-right: -4px; }
  select.fsel {
    border: 1px solid var(--line-strong); background: var(--panel); color: var(--ink-900);
    border-radius: 5px; padding: 7px 26px 7px 10px; font-size: 12.5px; font-weight: 600;
    cursor: pointer; outline: none; appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%235c6b70'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: right 9px center;
  }
  select.fsel:focus { border-color: var(--sea-500); box-shadow: 0 0 0 3px var(--sea-100); }
  .reset-btn {
    border: 1px solid transparent; background: transparent; color: var(--sea-600);
    font-size: 12.5px; font-weight: 600; cursor: pointer; padding: 7px 6px;
  }
  .reset-btn:hover { text-decoration: underline; }
  .count-line { font-size: 12.5px; color: var(--ink-500); font-variant-numeric: tabular-nums; padding-top: 2px; }

  .tablewrap { border: 1px solid var(--line); border-radius: 6px; overflow: auto; background: var(--panel); box-shadow: var(--shadow); margin-top: 12px; }
  .tablewrap table { border-collapse: collapse; width: 100%; table-layout: fixed; }
  .tablewrap thead th {
    position: sticky; top: 0; background: var(--sea-700); color: #fff; text-align: left;
    font-size: 11.5px; letter-spacing: 0.04em; text-transform: uppercase;
    padding: 10px 12px; font-weight: 600; white-space: nowrap;
  }
  .tablewrap tbody tr { border-top: 1px solid var(--line); }
  .tablewrap tbody tr:hover { background: var(--sea-100); }
  .tablewrap tbody td { padding: 9px 12px; vertical-align: top; font-size: 13.2px; color: var(--ink-700); overflow: hidden; }
  .tablewrap td.fname { color: var(--ink-900); font-weight: 500; }
  .tablewrap td.fname .fn { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tablewrap td.num { font-family: ui-monospace, "SF Mono", Consolas, monospace; font-variant-numeric: tabular-nums; text-align: center; }
  .tablewrap td.center { text-align: center; }
  .col-num { width: 40px; }
  .col-cat { width: 130px; }

  .empty-state { text-align: center; padding: 48px 20px; color: var(--ink-500); font-size: 14px; }

  .pager { display: flex; align-items: center; justify-content: center; gap: 4px; margin-top: 14px; flex-wrap: wrap; }
  .pager button {
    border: 1px solid var(--line-strong); background: var(--panel); color: var(--ink-700);
    border-radius: 5px; padding: 6px 11px; font-size: 12.5px; cursor: pointer; font-weight: 600;
    font-variant-numeric: tabular-nums; min-width: 32px;
  }
  .pager button:hover:not(:disabled) { border-color: var(--sea-500); color: var(--sea-700); }
  .pager button[aria-current="true"] { background: var(--sea-700); border-color: var(--sea-700); color: #fff; }
  .pager button:disabled { opacity: 0.35; cursor: default; }
  .pager .gap { color: var(--ink-500); padding: 0 2px; font-size: 12.5px; }
</style>
</head>
<body>
<div class="wrap">
  <header class="page-head">
    <div class="eyebrow">STEP4 · PERSONA IDENTITY ABLATION</div>
    <h1>페르소나 정체성 유무 대응비교 — 군집별 KMST 레이블링 결과</h1>
    <p class="lede">
    </p>
    <?php if ($manifest): ?>
    <div class="badge-row">
      <span class="badge">군집 수: <?= (int)($manifest["doc_count"] ?? 0) ?></span>
      <span class="badge">실패 건수: <?= (int)($manifest["failure_count"] ?? 0) ?></span>
      <span class="badge">config sha256: <?= htmlspecialchars(substr((string)($manifest["config_sha256"] ?? ""), 0, 12)) ?>…</span>
      <?php if (!empty($manifest["finished_at"])): ?><span class="badge">완료: <?= htmlspecialchars((string)$manifest["finished_at"]) ?></span><?php endif; ?>
    </div>
    <?php endif; ?>
    <p class="model-provenance">
      페르소나(persona_01/02/03) 문서 학습·생성 모델: <strong>Qwen3-14B</strong> ·
      이 페이지의 레이블링 결과 도출(실제 실행) 모델: <strong>Qwen2.5-14B-Instruct</strong>
      — 두 모델이 다르므로 아래 "단계·조건별 출력 유효성"의 think_leak_rate(Qwen3 전용 사고형 태그 누출률)는
      이 실행(Qwen2.5)에서는 해당 없음(항상 0)으로 표시됩니다.
    </p>
  </header>

  <?php if (!$hasData): ?>
  <section class="card">
    <div class="empty-notice">
      아직 실행 결과가 없습니다. 저장소 루트에서 다음을 실행하세요:<br><br>
      <code>./step_4_process/.venv/bin/python -m step4 --config config/config.json</code>
    </div>
  </section>
  <?php else: ?>

  <section class="card" id="step3-file-browser">
    <h2>군집별 원문서 목록</h2>
    <p class="sub">STEP3이 군집을 배정한 <?= count($step3Clusters) ? array_sum(array_column($step3Clusters, "n_docs")) : 0 ?>건 원문서 전체입니다. 군집·카테고리로 필터링하거나 파일명/id로 검색할 수 있습니다.</p>

    <div id="step3-browser-table">
      <div class="controls">
        <div class="search">
          <input id="s3-q" type="text" placeholder="파일명 또는 id로 검색…" autocomplete="off" />
        </div>
        <div class="filter-row">
          <label for="s3-f-cluster">군집</label>
          <select id="s3-f-cluster" class="fsel"></select>
          <label for="s3-f-cat">카테고리</label>
          <select id="s3-f-cat" class="fsel"></select>
          <button class="reset-btn" id="s3-reset-filters">필터 초기화</button>
          <span class="pagesize-group">
            <label for="s3-f-pagesize">페이지당</label>
            <select id="s3-f-pagesize" class="fsel"></select>
          </span>
        </div>
        <div class="count-line" id="s3-count-line"></div>
      </div>

      <div class="tablewrap">
        <table id="s3-tbl">
          <thead>
            <tr>
              <th class="col-num">#</th>
              <th class="col-cat">군집</th>
              <th>id</th>
              <th class="col-cat">카테고리</th>
              <th>파일명</th>
            </tr>
          </thead>
          <tbody id="s3-tbody"></tbody>
        </table>
        <div class="empty-state" id="s3-empty-state" style="display:none">일치하는 결과가 없습니다.</div>
      </div>

      <div class="pager" id="s3-pager"></div>
    </div>
  </section>

  <section class="card">
    <h2>군집별 최종 레이블 비교</h2>
    <p class="sub">기여도 점수가 가장 높은 원인이 주(主) 레이블입니다. 두 조건의 주 레이블이 다르면 "변경" 표시가 붙습니다.</p>
    <p class="sub" style="background:var(--paper-50);border:1px solid var(--line);border-radius:6px;padding:10px 12px;">
      <strong>왜 "off와 동일"이 나오는가</strong> — identity_on/off는 <code>persona_0N.md</code>
      한 문서에서 정체성 선언 문장("당신은 KMST-P0N이다") 한 줄만 넣고 뺀 것이고, 그 외
      법령 검색 컨텍스트·군집 대표문장·JSON 스키마·디코딩 파라미터는 전부 100% 동일합니다
      (매 실행마다 <code>step4/ablation.py</code>가 "정체성 블록 외 변경 없음"을 자동 검증).
      즉 두 조건이 받는 <em>증거 자체</em>는 완전히 같으므로, 증거가 하나의 원인을 뚜렷하게
      가리키는 군집은 정체성 문구 유무와 무관하게 같은 레이블로 수렴하는 것이 greedy(temperature=0)
      디코딩에서는 오히려 자연스러운 결과입니다. 반대로 "off와 다름"이 나온 군집(예: cluster_0,
      cluster_4)은 정체성 프레이밍이 실제로 레이블 선택에 영향을 준 사례로 해석할 수 있습니다 —
      "동일 vs 다름"의 비율 자체가 이 비교실험이 측정하려는 핵심 결과입니다.
    </p>
    <?php foreach ($byCluster as $clusterId => $conditions):
        $on = $conditions["identity_on"] ?? null;
        $off = $conditions["identity_off"] ?? null;
        $onPrimary = $on["root_cause_primary"] ?? null;
        $offPrimary = $off["root_cause_primary"] ?? null;
        $changed = ($onPrimary !== null && $offPrimary !== null && $onPrimary !== $offPrimary);
    ?>
    <div class="cluster-compare">
      <div class="cluster-head">
        <?= htmlspecialchars($clusterId) ?>
        <span class="n-docs"><?= (int)($on["n_docs"] ?? $off["n_docs"] ?? 0) ?>건 원문서</span>
      </div>
      <?php foreach ([["on", $on, "identity_on · 정체성 포함"], ["off", $off, "identity_off · 정체성 제거"]] as [$cls, $row, $label]): ?>
        <div class="cond-cell <?= $cls ?>">
          <div class="cond-label"><?= $label ?></div>
          <?php if ($row === null): ?>
            <div class="primary-label tag-invalid">결과 없음</div>
          <?php elseif (($row["schema_valid"] ?? "") !== "True" && ($row["schema_valid"] ?? "") !== "1"): ?>
            <div class="primary-label tag-invalid">스키마 검증 실패</div>
            <div class="cell-meta"><span><?= htmlspecialchars((string)($row["parse_error"] ?? "")) ?></span></div>
          <?php else:
            $primary = $row["root_cause_primary"] ?? "";
            $isKnown = isset($taxonomySet[$primary]);
            $secondary = json_decode($row["root_cause_secondary"] ?? "[]", true) ?: [];
          ?>
            <div class="primary-label <?= $isKnown ? "" : "tag-off-taxonomy" ?>">
              <?= htmlspecialchars($primary ?: "(비어있음)") ?>
              <?php if (!$isKnown && $primary !== "") : ?><span title="KMST 공식 목록에 없는 값">⚠</span><?php endif; ?>
              <?php if ($cls === "on" && $changed): ?><span class="diff-flag changed" title="정체성 문구 외 입력(증거·스키마)은 동일한데 주 레이블이 달라짐 — 정체성 프레이밍이 이 군집의 판단에 영향을 준 사례로 해석 가능">off와 다름</span><?php endif; ?>
              <?php if ($cls === "on" && !$changed && $offPrimary !== null): ?><span class="diff-flag same" title="정체성 문구만 다르고 나머지 입력(증거·스키마)은 동일 — 증거가 뚜렷해 정체성 유무와 무관하게 같은 레이블로 수렴">off와 동일</span><?php endif; ?>
            </div>
            <div class="cell-meta">
              <span>원인 개수: <?= htmlspecialchars((string)($row["n_causes"] ?? "N/A")) ?></span>
              <span>출력 토큰: <?= htmlspecialchars((string)($row["completion_tokens"] ?? "N/A")) ?></span>
            </div>
            <?php // 부원인(root_cause_secondary) 표시는 뺐음 — 2026-08-08 cluster_4 분석에서
                  // 확인됐듯 부원인 자리가 실제 레이블이 아니라 NOT_SCORABLE 플레이스홀더(_UNKNOWN_
                  // 등)로 채워지는 경우가 있어, 이 카드에 그대로 보여주면 오해 소지가 있다. 부원인이
                  // 왜/어떻게 갈렸는지는 아래 "근거 자료" 절에서 수치로 설명한다. ?>
          <?php endif; ?>
        </div>
      <?php endforeach; ?>
    </div>
    <?php endforeach; ?>
  </section>

  <section class="card" id="step4-evidence">
    <h2>근거 자료 — 군집별 on/off 결과가 왜 같거나 다른지</h2>
    <p class="sub">
      "군집별 최종 레이블 비교"는 결과(무엇이 나왔는지)만 보여줍니다. 이 절은 <b>왜</b> 그 결과가
      나왔는지를 체인 앞 단계(persona_01 사실추출 → persona_02 원인검증 → persona_03 레이블확정)의
      실제 개수와 반복루프 재시도 이력으로 추적한 것입니다. 군집을 눌러 펼치면 단계별 on/off 수치를
      볼 수 있습니다.
    </p>
    <?php foreach ($fullChain as $clusterId => $stages):
        $finalOn = $byCluster[$clusterId]["identity_on"] ?? null;
        $finalOff = $byCluster[$clusterId]["identity_off"] ?? null;
        $diag = cluster_diagnostics($stages, $finalOn["root_cause_primary"] ?? null, $finalOff["root_cause_primary"] ?? null);
    ?>
    <details class="evidence-cluster" <?= $diag["label_changed"] ? "open" : "" ?>>
      <summary>
        <span class="ec-cluster"><?= htmlspecialchars($clusterId) ?></span>
        <span class="diff-flag <?= $diag["label_changed"] ? "changed" : "same" ?>"><?= $diag["label_changed"] ? "레이블 변경" : "레이블 동일" ?></span>
        <span class="ec-sentence"><?= htmlspecialchars($diag["sentence"]) ?></span>
      </summary>
      <div class="evidence-wrap">
        <table class="evidence-tbl">
          <thead>
            <tr>
              <th>단계</th>
              <th>identity_on — 개수</th>
              <th>identity_off — 개수</th>
              <th>identity_on — 재시도</th>
              <th>identity_off — 재시도</th>
              <th>출력 토큰 on/off</th>
            </tr>
          </thead>
          <tbody>
            <?php foreach ($diag["rows"] as $r): ?>
            <tr>
              <td class="ec-stage"><?= htmlspecialchars($r["label"]) ?> <span class="ec-stage-id">(<?= htmlspecialchars($r["stage"]) ?>)</span></td>
              <td><?= htmlspecialchars($r["counts_on"]) ?><?php if ($r["stage"] === "persona_03"): ?> · 분류체계<?= $r["taxonomy_on"] === null ? " N/A" : (isTrue($r["taxonomy_on"]) ? " 준수" : " 미준수⚠") ?><?php endif; ?></td>
              <td><?= htmlspecialchars($r["counts_off"]) ?><?php if ($r["stage"] === "persona_03"): ?> · 분류체계<?= $r["taxonomy_off"] === null ? " N/A" : (isTrue($r["taxonomy_off"]) ? " 준수" : " 미준수⚠") ?><?php endif; ?></td>
              <td><?= htmlspecialchars($r["retry_on"]) ?></td>
              <td><?= htmlspecialchars($r["retry_off"]) ?></td>
              <td><?= htmlspecialchars((string)$r["tokens_on"]) ?> / <?= htmlspecialchars((string)$r["tokens_off"]) ?></td>
            </tr>
            <?php endforeach; ?>
          </tbody>
        </table>
      </div>
    </details>
    <?php endforeach; ?>
  </section>

  <section class="card">
    <h2>단계·조건별 출력 유효성</h2>
    <p class="sub">스키마 검증 통과율 — 90% 이상 초록, 60~90% 노랑, 60% 미만 빨강.</p>
    <div class="valid-wrap">
      <table class="valid">
        <thead><tr><th>단계</th><th>조건</th><th>n</th><th>스키마 유효율</th><th>파싱 실패율</th><th>think 누출율</th><th>평균 출력 토큰</th></tr></thead>
        <tbody>
        <?php foreach ($schemaValidity as $key => $m):
            [$stage, $condition] = array_pad(explode("__", $key, 2), 2, "");
            $rate = isset($m["schema_validity_rate"]) ? (float)$m["schema_validity_rate"] : null;
            $rateClass = $rate === null ? "" : ($rate >= 0.9 ? "rate-ok" : ($rate >= 0.6 ? "rate-warn" : "rate-bad"));
        ?>
          <tr>
            <td><?= htmlspecialchars($stage) ?></td>
            <td><?= htmlspecialchars($condition) ?></td>
            <td><?= (int)($m["n"] ?? 0) ?></td>
            <td class="<?= $rateClass ?>"><?= fmt($rate) ?></td>
            <td><?= fmt($m["parse_failure_rate"] ?? null) ?></td>
            <td><?= fmt($m["think_leak_rate"] ?? null) ?></td>
            <td><?= fmt($m["mean_completion_tokens"] ?? null, 1) ?></td>
          </tr>
        <?php endforeach; ?>
        </tbody>
      </table>
    </div>
  </section>

  <?php if ($p03Ablation): ?>
  <section class="card">
    <h2>정체성 유무 통계 비교 (최종 레이블 단계)</h2>
    <p class="sub">문서(군집) 단위 대응비교. 표본이 5개뿐이라 p-value보다 효과크기·신뢰구간을 함께 보는 것을 권장합니다.</p>
    <div class="stat-grid">
      <div class="stat-box">
        <div class="stat-label">레이블 변경률</div>
        <div class="stat-value"><?= fmt($p03Ablation["label_change_rate"] ?? null) ?></div>
        <div class="stat-note">95% CI: <?= isset($p03Ablation["label_change_rate_bootstrap_ci95"]) ? "(" . fmt($p03Ablation["label_change_rate_bootstrap_ci95"][0] ?? null) . ", " . fmt($p03Ablation["label_change_rate_bootstrap_ci95"][1] ?? null) . ")" : "N/A" ?></div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Cohen's kappa (on vs off)</div>
        <div class="stat-value"><?= fmt($p03Ablation["root_cause_primary_cohens_kappa_on_vs_off"] ?? null) ?></div>
        <div class="stat-note">1에 가까울수록 두 조건이 같은 레이블에 수렴</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">JS divergence</div>
        <div class="stat-value"><?= fmt($p03Ablation["root_cause_primary_js_divergence"] ?? null) ?></div>
        <div class="stat-note">0에 가까울수록 레이블 분포가 유사</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">스키마 유효성 (McNemar)</div>
        <div class="stat-value">p=<?= fmt($p03Ablation["schema_validity_mcnemar"]["p_value"] ?? null) ?></div>
        <div class="stat-note">on=<?= fmt($p03Ablation["schema_validity_rate_on"] ?? null) ?> / off=<?= fmt($p03Ablation["schema_validity_rate_off"] ?? null) ?></div>
      </div>
      <div class="stat-box">
        <div class="stat-label">출력 길이 (Wilcoxon)</div>
        <div class="stat-value">p=<?= fmt($p03Ablation["completion_tokens_wilcoxon"]["p_value"] ?? null) ?></div>
        <div class="stat-note">on 평균 <?= fmt($p03Ablation["completion_tokens_mean_on"] ?? null, 1) ?> / off 평균 <?= fmt($p03Ablation["completion_tokens_mean_off"] ?? null, 1) ?></div>
      </div>
      <div class="stat-box">
        <div class="stat-label">대응 표본 수</div>
        <div class="stat-value"><?= (int)($p03Ablation["n_paired_docs"] ?? 0) ?></div>
        <div class="stat-note">identity_on/off 둘 다 유효한 군집 수</div>
      </div>
    </div>
  </section>

  <?php $transition = $p03Ablation["root_cause_primary_transition_on_to_off"] ?? null; if ($transition): ?>
  <section class="card">
    <h2>레이블 전이 (identity_on → identity_off)</h2>
    <p class="sub">대각선(초록)은 두 조건이 같은 레이블을 낸 경우, 그 외(노랑)는 정체성 유무에 따라 레이블이 바뀐 경우입니다.</p>
    <?php
      $onLabels = array_keys($transition);
      $offLabels = [];
      foreach ($transition as $row) foreach (array_keys($row) as $k) $offLabels[$k] = true;
      $offLabels = array_keys($offLabels);
      sort($onLabels); sort($offLabels);
    ?>
    <div class="transition-wrap">
      <table class="transition">
        <thead><tr><th class="rowhead">on \ off</th><?php foreach ($offLabels as $ol): ?><th><?= htmlspecialchars($ol) ?></th><?php endforeach; ?></tr></thead>
        <tbody>
        <?php foreach ($onLabels as $ol_on): ?>
          <tr>
            <th class="rowhead"><?= htmlspecialchars($ol_on) ?></th>
            <?php foreach ($offLabels as $ol_off):
                $count = (int)($transition[$ol_on][$ol_off] ?? 0);
                $isDiag = ($ol_on === $ol_off);
            ?>
              <td class="<?= $isDiag ? "diag" : "off-diag" . ($count > 0 ? " nonzero" : "") ?>"><?= $count ?></td>
            <?php endforeach; ?>
          </tr>
        <?php endforeach; ?>
        </tbody>
      </table>
    </div>
  </section>
  <?php endif; endif; ?>

  <?php if ($determinism): ?>
  <section class="card">
    <h2>결정성(재현성) 확인</h2>
    <p class="sub">동일 입력을 2회 실행해 greedy 디코딩이 실제로 같은 결과를 내는지 확인합니다(<?= htmlspecialchars((string)($determinism["stage_tested"] ?? "")) ?> 단계, <?= htmlspecialchars((string)($determinism["condition_tested"] ?? "")) ?> 조건 기준).</p>
    <div class="stat-grid">
      <div class="stat-box">
        <div class="stat-label">바이트 단위 일치율</div>
        <div class="stat-value"><?= fmt($determinism["byte_identical_rate"] ?? null) ?></div>
        <div class="stat-note">표본 <?= (int)($determinism["sample_size_used"] ?? 0) ?>건</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">레이블 일치율</div>
        <div class="stat-value"><?= fmt($determinism["label_identical_rate"] ?? null) ?></div>
      </div>
      <?php if (!empty($determinism["mismatch_cases"])): ?>
      <div class="stat-box">
        <div class="stat-label">불일치 군집</div>
        <div class="stat-value" style="font-size:13px;"><?= htmlspecialchars(implode(", ", $determinism["mismatch_cases"])) ?></div>
      </div>
      <?php endif; ?>
    </div>
    <?php if (($determinism["byte_identical_rate"] ?? 1) < 1.0): ?>
    <p class="sub" style="margin-top:12px;color:var(--warn-fg);">100% 미만입니다 — temperature=0(greedy)인데도 재현이 안 됐다면, 로컬 추론 엔진의 배치 처리·캐시 관련 비결정성일 가능성이 있습니다. 결과를 해석할 때 이 점을 감안하세요.</p>
    <?php endif; ?>
  </section>
  <?php endif; ?>

  <section class="card">
    <h2>원본 데이터</h2>
    <p class="sub">
      전체 표: <code>outputs/step4_persona_ablation/flat/labels.csv</code> ·
      상세 리포트: <code>outputs/step4_persona_ablation/report/benchmark_report.md</code> ·
      컬럼 설명: <code>outputs/labels_csv_schema_guide.md</code>
    </p>
  </section>

  <?php endif; ?>

  <footer class="page-foot">
    STEP4 · Persona Identity Ablation Viewer — 이 페이지는 <code>python -m step4</code> 실행 결과 파일을 읽기만 합니다.
  </footer>
</div>
<script>
  const STEP3_CLUSTERS = <?= json_encode($step3Clusters, JSON_UNESCAPED_UNICODE) ?: "[]" ?>;
</script>
<script>
(function () {
  const tbl = document.getElementById("s3-tbl");
  if (!tbl) return; // 이 페이지에 파일 목록 카드가 없으면(=STEP4 결과가 아직 없으면) 아무것도 안 함

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  const PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 200];
  const state = { q: "", cluster: "", category: "", page: 1, pageSize: 10 };

  const docs = [];
  (STEP3_CLUSTERS || []).forEach((c) => {
    (c.docs || []).forEach((d) => docs.push({ cluster: c.cluster, id: d.id, category: d.category, filename: d.filename }));
  });
  const categories = [...new Set(docs.map((d) => d.category))].sort();

  function fillOptions(sel, values, allLabel) {
    sel.innerHTML = `<option value="">${esc(allLabel)}</option>` +
      values.map((v) => `<option value="${esc(String(v.value))}">${esc(v.label)}</option>`).join("");
  }

  function matches(d) {
    if (state.q && !d.filename.toLowerCase().includes(state.q) && !String(d.id).toLowerCase().includes(state.q)) return false;
    if (state.cluster !== "" && String(d.cluster) !== state.cluster) return false;
    if (state.category && d.category !== state.category) return false;
    return true;
  }

  function rowHtml(d, pos) {
    return `<tr>
      <td class="num">${pos}</td>
      <td class="center">군집${d.cluster}</td>
      <td>${esc(d.id)}</td>
      <td>${esc(d.category || "-")}</td>
      <td class="fname"><span class="fn" title="${esc(d.filename)}">${esc(d.filename)}</span></td>
    </tr>`;
  }

  function renderPager(count, totalPages) {
    const pager = document.getElementById("s3-pager");
    if (count <= state.pageSize) { pager.innerHTML = ""; return; }
    const cur = state.page;
    const btn = (label, page, opts = {}) =>
      `<button ${opts.disabled ? "disabled" : ""} ${page === cur ? 'aria-current="true"' : ""} data-page="${page}">${label}</button>`;
    let html = "";
    html += btn("이전", cur - 1, { disabled: cur === 1 });
    const pages = new Set([1, totalPages, cur, cur - 1, cur + 1]);
    let last = 0;
    [...pages].filter((p) => p >= 1 && p <= totalPages).sort((a, b) => a - b).forEach((p) => {
      if (last && p - last > 1) html += `<span class="gap">…</span>`;
      html += btn(String(p), p);
      last = p;
    });
    html += btn("다음", cur + 1, { disabled: cur === totalPages });
    pager.innerHTML = html;
    pager.querySelectorAll("button[data-page]").forEach((b) => {
      b.addEventListener("click", () => {
        state.page = Number(b.getAttribute("data-page"));
        render();
        document.getElementById("step3-browser-table").scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
  }

  function render() {
    const filtered = docs.filter(matches);
    const totalPages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
    if (state.page > totalPages) state.page = totalPages;
    if (state.page < 1) state.page = 1;

    document.getElementById("s3-count-line").textContent =
      `${filtered.length.toLocaleString()}건 표시 중 (전체 ${docs.length.toLocaleString()}건 중) · ${state.page}/${totalPages}페이지`;

    const tbody = document.getElementById("s3-tbody");
    const empty = document.getElementById("s3-empty-state");
    if (filtered.length === 0) {
      tbody.innerHTML = "";
      empty.style.display = "block";
      renderPager(0, 1);
      return;
    }
    empty.style.display = "none";
    const start = (state.page - 1) * state.pageSize;
    const pageRows = filtered.slice(start, start + state.pageSize);
    tbody.innerHTML = pageRows.map((d, i) => rowHtml(d, start + i + 1)).join("");
    renderPager(filtered.length, totalPages);
  }

  document.getElementById("s3-q").addEventListener("input", (e) => {
    state.q = e.target.value.trim().toLowerCase();
    state.page = 1;
    render();
  });

  const clusterOptions = (STEP3_CLUSTERS || []).map((c) => ({ value: String(c.cluster), label: `군집${c.cluster} (${c.n_docs}건)` }));
  fillOptions(document.getElementById("s3-f-cluster"), clusterOptions, "전체");

  const catOptions = categories.map((c) => ({ value: c, label: c }));
  fillOptions(document.getElementById("s3-f-cat"), catOptions, "전체");

  const pageSizeSel = document.getElementById("s3-f-pagesize");
  pageSizeSel.innerHTML = PAGE_SIZE_OPTIONS.map((n) => `<option value="${n}">${n}개씩</option>`).join("");
  pageSizeSel.value = String(state.pageSize);
  pageSizeSel.addEventListener("change", (e) => {
    state.pageSize = Number(e.target.value);
    state.page = 1;
    render();
  });

  [["s3-f-cluster", "cluster"], ["s3-f-cat", "category"]].forEach(([id, key]) => {
    document.getElementById(id).addEventListener("change", (e) => {
      state[key] = e.target.value;
      state.page = 1;
      render();
    });
  });

  document.getElementById("s3-reset-filters").addEventListener("click", () => {
    state.q = ""; state.cluster = ""; state.category = ""; state.page = 1;
    document.getElementById("s3-q").value = "";
    ["s3-f-cluster", "s3-f-cat"].forEach((id) => document.getElementById(id).value = "");
    render();
  });

  render();
})();
</script>
</body>
</html>
