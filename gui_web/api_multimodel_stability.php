<?php
/**
 * STEP 4 — "전체 모델 비교 실행"을 여러 번 돌린 기록(step_4_process/output/
 * multimodel_runs.jsonl)을 모아, 모델·군집 조합마다 가장 많이 나온 라벨
 * (최빈값)과 그 비율을 계산한다.
 *
 * 같은 모델·같은 프롬프트를 여러 번 실행했을 때 라벨이 얼마나 안정적으로
 * 나오는지(재현성)를 정량화하는 용도 — temperature > 0이라 매번 100% 같은
 * 결과가 나오진 않는데, 이 최빈값 비율이 낮을수록 그 모델·군집 조합의
 * 결과를 신뢰하기 전에 더 많이 재확인해야 한다는 뜻이다.
 *
 * 출력: {"ok": true, "n_runs": N, "models": [...], "clusters": [...],
 *        "stats": {model: {cluster: {mode, count, total, freq: {label: n}}}}}
 */

declare(strict_types=1);

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");

const RUNS_FILE = __DIR__ . "/../step_4_process/output/multimodel_runs.jsonl";

if (!is_file(RUNS_FILE)) {
    echo json_encode(["ok" => true, "n_runs" => 0, "models" => [], "clusters" => [], "stats" => []]);
    exit;
}

$runs = [];
$fh = fopen(RUNS_FILE, "r");
while (($line = fgets($fh)) !== false) {
    $line = trim($line);
    if ($line === "") continue;
    $d = json_decode($line, true);
    if ($d) $runs[] = $d;
}
fclose($fh);

// model => cluster => label => count
$freq = [];
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
foreach ($freq as $model => $byCluster) {
    foreach ($byCluster as $cluster => $labelCounts) {
        arsort($labelCounts);
        $mode = array_key_first($labelCounts);
        $count = $labelCounts[$mode];
        $total = array_sum($labelCounts);
        $stats[$model][(string)$cluster] = [
            "mode" => $mode, "count" => $count, "total" => $total, "freq" => $labelCounts,
        ];
    }
}

$models = array_keys($modelsSeen);
$clusters = array_keys($clustersSeen);
sort($clusters);

echo json_encode([
    "ok" => true,
    "n_runs" => count($runs),
    "models" => $models,
    "clusters" => $clusters,
    "stats" => $stats,
], JSON_UNESCAPED_UNICODE);
