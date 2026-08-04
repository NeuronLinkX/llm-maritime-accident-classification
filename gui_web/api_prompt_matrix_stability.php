<?php
/**
 * STEP 4 — 프롬프트 3종(A/B/C) x temperature 2종(0.0/0.2) = 6칸 실험 매트릭스 집계.
 *
 * run_prompt_temp_matrix.py가 저장한 기록은 config_version_key()의 "_p{A|B|C}_t{온도}"
 * 접미사 덕분에 조합별로 다른 폴더(step_4_process/output/runs/{버전}/multimodel_runs.jsonl)에
 * 쌓이지만, 이 엔드포인트는 폴더명을 파싱하지 않고 각 실행 기록에 저장된 명시적
 * prompt_variant/temperature 필드(api_save_multimodel_run.php 참고)로 6칸에 배정한다 —
 * 더 안전하고, 폴더명 규칙이 나중에 바뀌어도 깨지지 않는다.
 *
 * 기존 api_multimodel_stability.php와 같은 방식(모델·군집 조합마다 최빈 라벨과 그 비율)으로
 * 안정도를 계산하고, GUIDE.md에 이미 쓰인 70%/40% 기준(초록/노랑/빨강)을 그대로 재사용한다.
 *
 * 출력: {"ok": true, "target_per_cell": 30, "excluded_models": [...],
 *        "cells": {"A_0.0": {...}, ...}, "by_variant": {...}, "by_temperature": {...}}
 */

declare(strict_types=1);

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");
// report_template.html(포트 9000, 메인 체크아웃)이 이 엔드포인트(포트 9102, 이 워크트리)를
// 브라우저에서 직접 fetch()로 불러 "BASELINE 통계 요약"을 그린다 — 포트가 다르면 브라우저가
// 별도 origin으로 취급해 CORS를 검사하므로 허용 헤더가 필요하다. 읽기 전용 집계 통계라 공개해도
// 문제없다.
header("Access-Control-Allow-Origin: *");

require_once __DIR__ . "/lib_llm_common.php";

const TARGET_PER_CELL = 5;
const VARIANTS = ["A", "B", "C"];
const TEMPERATURES = [0.0, 0.2];
const EXCLUDED_MODELS = ["Qwen2.5-7B-Instruct"];
const RUNS_BASE = __DIR__ . "/../step_4_process/output/runs";

function cell_key(string $variant, float $temperature): string {
    return $variant . "_" . sprintf("%.1f", $temperature);
}

/** RUNS_BASE 아래 모든 버전 폴더의 multimodel_runs.jsonl을 한 번에 읽어 레코드 배열로 반환. */
function read_all_runs(): array {
    if (!is_dir(RUNS_BASE)) return [];
    $all = [];
    foreach (scandir(RUNS_BASE) as $name) {
        if ($name === "." || $name === "..") continue;
        $path = RUNS_BASE . "/" . $name . "/multimodel_runs.jsonl";
        if (!is_file($path)) continue;
        $fh = fopen($path, "r");
        while (($line = fgets($fh)) !== false) {
            $line = trim($line);
            if ($line === "") continue;
            $d = json_decode($line, true);
            if ($d) $all[] = $d;
        }
        fclose($fh);
    }
    return $all;
}

$allRuns = read_all_runs();

// prompt_variant가 A/B/C 중 하나로 명시적으로 태그된 기록만 이 실험 매트릭스에 포함한다.
// (레거시/다른 실행은 prompt_variant가 null이므로 자동으로 제외된다.)
$byCell = [];
foreach (VARIANTS as $v) {
    foreach (TEMPERATURES as $t) {
        $byCell[cell_key($v, $t)] = [];
    }
}
$inMatrixRuns = []; // 6칸에 실제로 들어간 기록만 모은 평평한 목록(근거 문장 리포트용)
foreach ($allRuns as $run) {
    $variant = $run["prompt_variant"] ?? null;
    $temperature = $run["temperature"] ?? null;
    if (!in_array($variant, VARIANTS, true)) continue;
    if (!is_numeric($temperature)) continue;
    $temperature = (float)$temperature;
    $key = cell_key($variant, $temperature);
    if (!isset($byCell[$key])) continue; // 매트릭스 밖 온도값(예: 실험과 무관한 커스텀 값)은 무시
    $byCell[$key][] = $run;
    $inMatrixRuns[] = $run;
}

/** 한 칸(같은 prompt_variant+temperature) 안의 실행 기록들을 model->cluster->label 빈도로 집계. */
function aggregate_cell(array $runs): array {
    $freq = [];       // model => cluster => label => count
    $modelsSeen = [];
    $clustersSeen = [];
    foreach ($runs as $run) {
        foreach (($run["results"] ?? []) as $r) {
            if (empty($r["ok"]) || empty($r["model"]) || empty($r["clusters"])) continue;
            $model = $r["model"];
            $modelsSeen[$model] = true;
            foreach ($r["clusters"] as $c) {
                if (!isset($c["cluster"]) || !isset($c["proposed_label"])) continue;
                $cluster = (int)$c["cluster"];
                $label = (string)$c["proposed_label"];
                $clustersSeen[$cluster] = true;
                $freq[$model][$cluster][$label] = ($freq[$model][$cluster][$label] ?? 0) + 1;
            }
        }
    }

    $stats = [];
    $ratios = []; // 전체 model x cluster 조합의 mode 비율(칸 전체 안정도 평균용)
    foreach ($freq as $model => $byCluster) {
        foreach ($byCluster as $cluster => $labelCounts) {
            arsort($labelCounts);
            $mode = array_key_first($labelCounts);
            $count = $labelCounts[$mode];
            $total = array_sum($labelCounts);
            $stats[$model][(string)$cluster] = [
                "mode" => $mode, "count" => $count, "total" => $total, "freq" => $labelCounts,
            ];
            if ($total > 0) $ratios[] = $count / $total;
        }
    }

    $models = array_keys($modelsSeen);
    $clusters = array_keys($clustersSeen);
    sort($clusters);
    $cellStabilityAvg = $ratios ? array_sum($ratios) / count($ratios) : null;

    // 모델 간 합의율 — 위 $stats에서 계산한 "모델별 최빈 라벨"들을 군집마다 다시 모아,
    // 그 군집에서 몇 개 모델이 서로 같은 라벨(다수결)에 동의했는지를 본다. 이건 "같은
    // 모델을 반복 실행해도 라벨이 안 흔들리는가"(cell_stability_avg, 모델 내부 재현성)와는
    // 다른 축이다 — 여기서는 모델끼리 서로 다른 답을 낼 수도 있다는 걸 보여준다(예:
    // Qwen2.5-14B만 다른 대분류를 고르는 경우).
    $crossModel = [];
    foreach ($clusters as $cluster) {
        $modes = [];
        foreach ($models as $model) {
            $s = $stats[$model][(string)$cluster] ?? null;
            if ($s) $modes[$model] = $s["mode"];
        }
        if (!$modes) continue;
        $counts = array_count_values($modes);
        arsort($counts);
        $consensus = array_key_first($counts);
        $agree = $counts[$consensus];
        $total = count($modes);
        $crossModel[(string)$cluster] = [
            "consensus" => $consensus, "agree" => $agree, "total" => $total,
            "ratio" => $total > 0 ? $agree / $total : null,
            "by_model" => $modes,
        ];
    }
    $crossRatios = array_values(array_filter(array_column($crossModel, "ratio"), fn($x) => $x !== null));
    $cellCrossModelAvg = $crossRatios ? array_sum($crossRatios) / count($crossRatios) : null;

    return [
        "n_runs" => count($runs),
        "target" => TARGET_PER_CELL,
        "progress" => TARGET_PER_CELL > 0 ? min(1.0, count($runs) / TARGET_PER_CELL) : 0.0,
        "models" => $models,
        "clusters" => $clusters,
        "stats" => $stats,
        "cell_stability_avg" => $cellStabilityAvg,
        "cross_model" => $crossModel,
        "cell_cross_model_avg" => $cellCrossModelAvg,
    ];
}

/** 문장 수를 대략 센다 — 문장 종결부호(. ! ?) 뒤 공백/끝에서 자른다. 종결부호가 아예
 * 없으면(드묾) 그래도 내용이 있으니 1문장으로 친다. "N문장 이내" 규칙 준수 여부를
 * 자동으로 체크하기 위한 근사치이며, 완벽한 문장 분리기는 아니다. */
function count_sentences(string $text): int {
    $text = trim($text);
    if ($text === "") return 0;
    $parts = preg_split('/(?<=[.!?])\s+/u', $text) ?: [];
    $parts = array_filter(array_map("trim", $parts), fn($p) => $p !== "");
    return count($parts) ?: 1;
}

const RATIONALE_MAX_SENTENCES = 2;

/** $runs(어느 범위든)의 rationale 전체를 모델별로 스캔해 "N문장 이내" 규칙 준수율을 낸다. */
function rationale_compliance_by_model(array $runs): array {
    $byModel = []; // model => ["n_total"=>, "n_compliant"=>, "sum_sentences"=>, "max_sentences"=>]
    foreach ($runs as $run) {
        foreach (($run["results"] ?? []) as $r) {
            if (empty($r["ok"]) || empty($r["model"]) || empty($r["clusters"])) continue;
            $model = $r["model"];
            if (!isset($byModel[$model])) {
                $byModel[$model] = ["n_total" => 0, "n_compliant" => 0, "sum_sentences" => 0, "max_sentences" => 0];
            }
            foreach ($r["clusters"] as $c) {
                $rationale = (string)($c["rationale"] ?? "");
                if ($rationale === "") continue;
                $n = count_sentences($rationale);
                $byModel[$model]["n_total"]++;
                if ($n <= RATIONALE_MAX_SENTENCES) $byModel[$model]["n_compliant"]++;
                $byModel[$model]["sum_sentences"] += $n;
                $byModel[$model]["max_sentences"] = max($byModel[$model]["max_sentences"], $n);
            }
        }
    }
    $out = [];
    foreach ($byModel as $model => $agg) {
        $out[$model] = [
            "n_total" => $agg["n_total"],
            "n_compliant" => $agg["n_compliant"],
            "compliance_rate" => $agg["n_total"] > 0 ? $agg["n_compliant"] / $agg["n_total"] : null,
            "avg_sentences" => $agg["n_total"] > 0 ? $agg["sum_sentences"] / $agg["n_total"] : null,
            "max_sentences" => $agg["max_sentences"],
        ];
    }
    return $out;
}

$cells = [];
foreach ($byCell as $key => $runs) {
    $cells[$key] = aggregate_cell($runs);
    [$variant, $tempStr] = explode("_", $key, 2);
    $cells[$key]["prompt_variant"] = $variant;
    $cells[$key]["temperature"] = (float)$tempStr;
}

// 온도별/프롬프트별 평균 — 6칸을 각각 하나의 관측치로 취급해 단순 평균한다(칸마다
// 표본 수가 다를 수 있어 완전한 통계적 엄밀함은 아니며, 다음 실험에서 표본 수를 맞춘 뒤
// 다시 볼 것을 전제로 한 탐색적 비교다). cell_stability_avg(모델 내부 재현성)와
// cell_cross_model_avg(모델 간 합의율) 둘 다 같은 방식으로 낸다.
function avg_by_variant(array $cells, string $metricKey): array {
    $out = [];
    foreach (VARIANTS as $v) {
        $vals = array_values(array_filter(array_map(
            fn($k) => str_starts_with($k, $v . "_") ? ($cells[$k][$metricKey] ?? null) : null,
            array_keys($cells)
        ), fn($x) => $x !== null));
        $out[$v] = $vals ? array_sum($vals) / count($vals) : null;
    }
    return $out;
}
function avg_by_temperature(array $cells, string $metricKey): array {
    $out = [];
    foreach (TEMPERATURES as $t) {
        $tKey = sprintf("%.1f", $t);
        $vals = array_values(array_filter(array_map(
            fn($k) => str_ends_with($k, "_" . $tKey) ? ($cells[$k][$metricKey] ?? null) : null,
            array_keys($cells)
        ), fn($x) => $x !== null));
        $out[$tKey] = $vals ? array_sum($vals) / count($vals) : null;
    }
    return $out;
}
$byVariant = avg_by_variant($cells, "cell_stability_avg");
$byTemperature = avg_by_temperature($cells, "cell_stability_avg");
$byVariantCrossModel = avg_by_variant($cells, "cell_cross_model_avg");
$byTemperatureCrossModel = avg_by_temperature($cells, "cell_cross_model_avg");

$rationaleCompliance = rationale_compliance_by_model($inMatrixRuns);

$variantMeta = \LlmCommon\prompt_variants();
$variantInfo = [];
foreach (VARIANTS as $v) {
    $variantInfo[$v] = [
        "label" => $variantMeta[$v]["label"] ?? $v,
        "persona" => $variantMeta[$v]["persona"] ?? "",
    ];
}

echo json_encode([
    "ok" => true,
    "target_per_cell" => TARGET_PER_CELL,
    "excluded_models" => EXCLUDED_MODELS,
    "rationale_max_sentences" => RATIONALE_MAX_SENTENCES,
    "variant_info" => $variantInfo,
    "cells" => $cells,
    "by_variant" => $byVariant,
    "by_temperature" => $byTemperature,
    "by_variant_cross_model" => $byVariantCrossModel,
    "by_temperature_cross_model" => $byTemperatureCrossModel,
    "rationale_compliance" => $rationaleCompliance,
    "generated_at" => date("c"),
], JSON_UNESCAPED_UNICODE);
