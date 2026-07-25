<?php
/**
 * STEP 3(K-Means 군집화) 데이터 로더 — report.php(통합)와 report_step3.php
 * (구버전 단독 페이지, 리다이렉트용으로만 남음)가 공유한다.
 */

declare(strict_types=1);

namespace Step3;

const OUT_DIR = __DIR__ . "/../step_3_process/output";
const TOP_KEYWORDS = 10;

function read_csv(string $path): array {
    if (!is_file($path)) return [];
    $rows = [];
    $f = fopen($path, "r");
    $header = fgetcsv($f);
    while (($line = fgetcsv($f)) !== false) {
        if (count($line) !== count($header)) continue;
        $rows[] = array_combine($header, $line);
    }
    fclose($f);
    return $rows;
}

function load_k_selection(): array {
    return array_map(fn($r) => [
        "k" => (int)$r["k"], "wcss" => (float)$r["wcss"], "silhouette" => (float)$r["avg_silhouette"],
        "is_elbow" => $r["is_elbow"] === "1", "is_silhouette_best" => $r["is_silhouette_best"] === "1",
        "is_chosen" => $r["is_chosen"] === "1",
    ], read_csv(OUT_DIR . "/k_selection.csv"));
}

function load_scatter(): array {
    $rows = read_csv(OUT_DIR . "/tsne_clusters.csv");
    return array_map(fn($r) => [
        "id" => $r["id"], "category" => $r["category"], "filename" => $r["filename"],
        "x" => (float)$r["x"], "y" => (float)$r["y"], "cluster" => (int)$r["cluster"],
    ], $rows);
}

/**
 * similarity_sample.py가 만든, 군집별 대표 문서 간 실제 코사인 유사도 행렬을 읽는다.
 * CSV 형태: id,category,filename,cluster,<문서id1>,<문서id2>,...  (뒤쪽 컬럼이 곧 행렬)
 */
function load_similarity_sample(): ?array {
    $path = OUT_DIR . "/similarity_sample.csv";
    if (!is_file($path)) return null;
    $f = fopen($path, "r");
    $header = fgetcsv($f);
    if (!$header) { fclose($f); return null; }
    $metaCols = ["id", "category", "filename", "cluster"];
    $docIds = array_slice($header, count($metaCols));

    $items = [];
    $matrix = [];
    while (($line = fgetcsv($f)) !== false) {
        if (count($line) !== count($header)) continue;
        $row = array_combine($header, $line);
        $items[] = ["id" => $row["id"], "category" => $row["category"], "cluster" => (int)$row["cluster"]];
        $matrix[] = array_map(fn($id) => (float)$row[$id], $docIds);
    }
    fclose($f);
    if (!$items) return null;

    return ["items" => $items, "matrix" => $matrix];
}

/** similarity_histogram.py가 만든 전체 문서쌍(818건 전수) 유사도 분포(intra/inter 군집)를 읽는다. */
function load_similarity_histogram(): ?array {
    $rows = read_csv(OUT_DIR . "/similarity_histogram.csv");
    if (!$rows) return null;
    return array_map(fn($r) => [
        "bin_start" => (float)$r["bin_start"], "bin_end" => (float)$r["bin_end"],
        "intra_count" => (int)$r["intra_count"], "inter_count" => (int)$r["inter_count"],
    ], $rows);
}

function build_insights(array $clustersOut, array $summary): array {
    $totalDocs = array_sum(array_map(fn($c) => $c["n_docs"], $clustersOut)) ?: 1;
    $scored = array_map(function ($c) use ($totalDocs) {
        $topKw = $c["keywords"][0] ?? null;
        $top3 = array_map(fn($k) => $k["keyword"], array_slice($c["keywords"], 0, 3));
        return [
            "cluster" => $c["cluster"], "n_docs" => $c["n_docs"],
            "top_keyword" => $top3 ? implode(", ", $top3) : "-",
            "top_score" => $topKw["score"] ?? 0.0,
            "pct" => round($c["n_docs"] / $totalDocs * 100, 1),
        ];
    }, $clustersOut);

    $byScoreDesc = $scored;
    usort($byScoreDesc, fn($a, $b) => $b["top_score"] <=> $a["top_score"]);
    $strong = array_slice($byScoreDesc, 0, 2);

    $byScoreAsc = $scored;
    usort($byScoreAsc, fn($a, $b) => $a["top_score"] <=> $b["top_score"] ?: $b["n_docs"] <=> $a["n_docs"]);
    $weak = $byScoreAsc[0] ?? null;

    return [
        "clusters" => $scored,
        "strong" => $strong,
        "weak" => $weak,
        "chosen_silhouette" => isset($summary["chosen_silhouette"]) ? round($summary["chosen_silhouette"], 4) : null,
    ];
}

function build_data(): array {
    $kSel = load_k_selection();
    $clustersRaw = read_csv(OUT_DIR . "/clusters.csv");
    $keywordsRaw = read_csv(OUT_DIR . "/cluster_keywords.csv");
    $scatterPoints = load_scatter();

    $chosen = null; $elbow = null; $silBest = null;
    foreach ($kSel as $r) {
        if ($r["is_chosen"]) $chosen = $r;
        if ($r["is_elbow"]) $elbow = $r;
        if ($r["is_silhouette_best"]) $silBest = $r;
    }

    $byClusterDocs = [];
    foreach ($clustersRaw as $r) {
        $c = (int)$r["cluster"];
        $byClusterDocs[$c][] = $r;
    }

    $keywordsByCluster = [];
    foreach ($keywordsRaw as $r) {
        if ($r["kind"] !== "distinctive") continue;
        $c = (int)$r["cluster"];
        if (!isset($keywordsByCluster[$c])) $keywordsByCluster[$c] = [];
        if (count($keywordsByCluster[$c]) >= TOP_KEYWORDS) continue;
        $keywordsByCluster[$c][] = ["keyword" => $r["keyword"], "freq" => (int)$r["freq"], "score" => (float)$r["score"]];
    }

    ksort($byClusterDocs);
    $clustersOut = [];
    foreach ($byClusterDocs as $c => $docs) {
        $catCounts = [];
        foreach ($docs as $d) $catCounts[$d["category"]] = ($catCounts[$d["category"]] ?? 0) + 1;
        arsort($catCounts);
        $topCats = array_slice($catCounts, 0, 5, true);
        $topCatsOut = [];
        foreach ($topCats as $cat => $cnt) $topCatsOut[] = ["category" => $cat, "count" => $cnt];

        $clustersOut[] = [
            "cluster" => $c, "n_docs" => count($docs), "top_categories" => $topCatsOut,
            "keywords" => $keywordsByCluster[$c] ?? [],
            "wordcloud" => "assets/wordclouds/wordcloud_cluster_{$c}.png",
        ];
    }

    $summary = [
        "n_docs" => count($clustersRaw),
        "chosen_k" => $chosen["k"] ?? null,
        "chosen_silhouette" => $chosen["silhouette"] ?? null,
        "elbow_k" => $elbow["k"] ?? null,
        "silhouette_k" => $silBest["k"] ?? null,
        "n_clusters" => count($byClusterDocs),
    ];

    return [
        "summary" => $summary,
        "k_selection" => $kSel,
        "clusters" => $clustersOut,
        "scatter" => ["points" => $scatterPoints, "n_clusters" => count($byClusterDocs)],
        "insights" => build_insights($clustersOut, $summary),
        "similarity_sample" => load_similarity_sample(),
        "similarity_histogram" => load_similarity_histogram(),
        "generated_at" => date("Y-m-d H:i:s"),
        "source_mode" => "live-php",
    ];
}
