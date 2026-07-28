<?php
/**
 * STEP 2(SBERT 유사도 정량화) 데이터 로더 — report.php(통합)와 report_step2.php
 * (구버전 단독 페이지, 리다이렉트용으로만 남음)가 공유한다.
 *
 * 이름 충돌을 피하려고 네임스페이스로 감쌌다 — report.php가 STEP1/STEP3
 * 로더를 같은 파일에서 함께 require하므로, build_data/read_csv 같은
 * 흔한 이름이 겹치면 안 된다.
 */

declare(strict_types=1);

namespace Step2;

const EMB_DIR = __DIR__ . "/../step_2_process/embeddings";
const CHOSEN_MODEL = "ko-sroberta-sts";
const TOP_CATEGORY_COUNT = 7;

function read_csv(string $path): array {
    if (!is_file($path)) return [];
    $rows = [];
    $f = fopen($path, "r");
    $header = fgetcsv($f, null, ",", '"', "");
    while (($line = fgetcsv($f, null, ",", '"', "")) !== false) {
        if (count($line) !== count($header)) continue;
        $rows[] = array_combine($header, $line);
    }
    fclose($f);
    return $rows;
}

function load_benchmark(): array {
    $rows = read_csv(EMB_DIR . "/benchmark_results.csv");
    $models = array_map(fn($r) => [
        "name" => $r["model"], "dim" => (int)$r["dim"], "n_records" => (int)$r["n_records"],
        "intra_n" => (int)$r["intra_n"], "intra_mean" => (float)$r["intra_mean"], "intra_std" => (float)$r["intra_std"],
        "inter_n" => (int)$r["inter_n"], "inter_mean" => (float)$r["inter_mean"], "inter_std" => (float)$r["inter_std"],
        "gap" => (float)$r["gap"],
    ], $rows);
    usort($models, fn($a, $b) => $b["gap"] <=> $a["gap"]);
    return $models;
}

function bucket_categories(array $rows): array {
    $counts = [];
    foreach ($rows as $r) $counts[$r["category"]] = ($counts[$r["category"]] ?? 0) + 1;
    arsort($counts);
    $topCategories = array_slice(array_keys($counts), 0, TOP_CATEGORY_COUNT);
    $topSet = array_flip($topCategories);
    $otherCount = 0;
    foreach ($rows as $r) if (!isset($topSet[$r["category"]])) $otherCount++;
    $legend = $topCategories;
    $legendKeys = $topCategories;
    if ($otherCount > 0) {
        $legend[] = "기타(" . (count($counts) - TOP_CATEGORY_COUNT) . "종)";
        $legendKeys[] = "기타";
    }
    return [$topSet, $legend, $legendKeys];
}

function load_scatter(): array {
    $rows = read_csv(EMB_DIR . "/tsne_2d.csv");
    [$topSet, $legend, $legendKeys] = bucket_categories($rows);
    $points = [];
    foreach ($rows as $r) {
        $cat = isset($topSet[$r["category"]]) ? $r["category"] : "기타";
        $points[] = ["id" => $r["id"], "category" => $cat, "filename" => $r["filename"], "x" => (float)$r["x"], "y" => (float)$r["y"]];
    }
    return ["points" => $points, "legend" => $legend, "legend_keys" => $legendKeys];
}

function load_scatter_3d(): array {
    $rows = read_csv(EMB_DIR . "/tsne_3d.csv");
    if (!$rows) return ["points" => [], "legend" => [], "legend_keys" => []];
    [$topSet, $legend, $legendKeys] = bucket_categories($rows);
    $points = [];
    foreach ($rows as $r) {
        $cat = isset($topSet[$r["category"]]) ? $r["category"] : "기타";
        $points[] = ["id" => $r["id"], "category" => $cat, "filename" => $r["filename"], "x" => (float)$r["x"], "y" => (float)$r["y"], "z" => (float)$r["z"]];
    }
    return ["points" => $points, "legend" => $legend, "legend_keys" => $legendKeys];
}

function load_pairs(string $kind): array {
    $rows = read_csv(EMB_DIR . "/" . CHOSEN_MODEL . "_{$kind}_pairs.csv");
    return array_slice($rows, 0, 10);
}

function load_graph(): array {
    $rows = read_csv(EMB_DIR . "/knn_graph.csv");
    return array_map(fn($r) => ["a" => $r["id_a"], "b" => $r["id_b"], "sim" => (float)$r["similarity"]], $rows);
}

function build_data(): array {
    $models = load_benchmark();
    $chosen = null;
    foreach ($models as $m) if ($m["name"] === CHOSEN_MODEL) { $chosen = $m; break; }
    if ($chosen === null && count($models) > 0) $chosen = $models[0];
    $scatter = load_scatter();

    $catSet = array_unique(array_map(fn($p) => $p["category"], $scatter["points"]));

    $summary = [
        "n_docs" => $chosen["n_records"] ?? 0,
        "n_models" => count($models),
        "n_categories" => count($catSet),
        "chosen_model" => CHOSEN_MODEL,
        "gap" => $chosen["gap"] ?? 0.0,
        "intra_mean" => $chosen["intra_mean"] ?? 0.0,
        "inter_mean" => $chosen["inter_mean"] ?? 0.0,
    ];

    return [
        "summary" => $summary,
        "models" => $models,
        "scatter" => $scatter,
        "scatter3d" => load_scatter_3d(),
        "top_pairs" => load_pairs("top"),
        "bottom_pairs" => load_pairs("bottom"),
        "graph" => load_graph(),
        "generated_at" => date("Y-m-d H:i:s"),
        "source_mode" => "live-php",
    ];
}
