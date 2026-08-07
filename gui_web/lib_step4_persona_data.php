<?php
/**
 * STEP 4(신규) — 페르소나 체인 기반 군집 레이블링 결과 로더.
 *
 * 이 파이프라인은 PHP가 아니라 별도 파이썬 CLI(`step4/`, `python -m step4`)가
 * 로컬 vLLM으로 직접 실행하고, 그 산출물(outputs/step4_persona_ablation/)을
 * 파일로 남긴다. 이 파일은 그 결과 파일들을 읽어 배열로 반환하기만 하며,
 * 이 파일 자체는 모델을 호출하지 않는다 — 구 STEP4(api_llm_label.php 등)처럼
 * 브라우저 요청마다 LLM을 부르지 않는다.
 */

declare(strict_types=1);

namespace Step4Persona;

const OUTPUT_ROOT = __DIR__ . "/../outputs/step4_persona_ablation";

function output_exists(): bool {
    return is_dir(OUTPUT_ROOT) && is_file(OUTPUT_ROOT . "/flat/labels.csv");
}

/** flat/labels.csv 전체를 연관배열 목록으로 읽는다. 없으면 빈 배열. */
function load_labels(): array {
    $path = OUTPUT_ROOT . "/flat/labels.csv";
    if (!is_file($path)) return [];
    $rows = [];
    $fh = fopen($path, "r");
    if ($fh === false) return [];
    $header = fgetcsv($fh);
    if ($header === false) { fclose($fh); return []; }
    while (($line = fgetcsv($fh)) !== false) {
        if (count($line) !== count($header)) continue;
        $rows[] = array_combine($header, $line);
    }
    fclose($fh);
    return $rows;
}

/** persona_03(최종 레이블) 행만 골라, cluster_id => [condition => row] 형태로 재구성한다. */
function final_labels_by_cluster(): array {
    $out = [];
    foreach (load_labels() as $row) {
        if (($row["stage"] ?? "") !== "persona_03") continue;
        $cluster = $row["doc_id"] ?? "";
        $condition = $row["condition"] ?? "";
        if ($cluster === "" || $condition === "") continue;
        $out[$cluster][$condition] = $row;
    }
    ksort($out);
    return $out;
}

function load_json(string $relativePath): ?array {
    $path = OUTPUT_ROOT . "/" . ltrim($relativePath, "/");
    if (!is_file($path)) return null;
    $raw = file_get_contents($path);
    $decoded = json_decode($raw, true);
    return is_array($decoded) ? $decoded : null;
}

function schema_validity(): array {
    return load_json("metrics/schema_validity.json") ?? [];
}

function ablation_comparison(): array {
    return load_json("metrics/ablation_comparison.json") ?? [];
}

function determinism_check(): ?array {
    return load_json("metrics/determinism_check.json");
}

function run_manifest(): ?array {
    return load_json("artifacts/run_manifest.json");
}

/** KMST 공식 분류체계 세부항목 22종 — persona_model/kmst_official_taxonomy.json 그대로. */
function kmst_taxonomy_flat(): array {
    $path = __DIR__ . "/../persona_model/kmst_official_taxonomy.json";
    if (!is_file($path)) return [];
    $decoded = json_decode(file_get_contents($path), true);
    if (!is_array($decoded)) return [];
    $flat = [];
    foreach (($decoded["categories"] ?? []) as $items) {
        foreach ($items as $item) $flat[] = $item;
    }
    return $flat;
}
