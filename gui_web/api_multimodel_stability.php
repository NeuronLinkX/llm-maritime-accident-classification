<?php
/**
 * STEP 4 — "전체 모델 비교 실행"을 여러 번 돌린 기록을 모아, 모델·군집 조합마다
 * 가장 많이 나온 라벨(최빈값)과 그 비율을 계산한다.
 *
 * 기록은 설정 버전별 폴더(step_4_process/output/runs/{버전}/multimodel_runs.jsonl)에
 * 나뉘어 있다. 이 계산은 반드시 같은 프롬프트(같은 그라운딩 설정)로 만든 기록끼리만
 * 모아야 의미가 있다 — 표본 배분이 다른 두 설정의 실행을 섞어서 최빈값을 내면
 * "재현성이 낮다"는 착시가 생긴다(실제로는 프롬프트 자체가 달랐을 뿐). 그래서
 * 기본값은 현재 코드의 설정 버전 폴더만 읽는다.
 *
 * 입력(GET 쿼리): version=... (생략 시 현재 설정 버전)
 * 출력: {"ok": true, "config_version": "...", "n_runs": N, "models": [...],
 *        "clusters": [...], "stats": {model: {cluster: {mode, count, total, freq: {label: n}}}}}
 */

declare(strict_types=1);

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");

require_once __DIR__ . "/lib_llm_common.php";

$currentVersion = \LlmCommon\config_version_key();
$requestedVersion = trim((string)($_GET["version"] ?? ""));
$version = $requestedVersion !== "" ? $requestedVersion : $currentVersion;
if ($requestedVersion !== "" && !in_array($requestedVersion, \LlmCommon\list_config_versions(), true)) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "알 수 없는 version입니다: {$requestedVersion}"]);
    exit;
}
$RUNS_FILE = __DIR__ . "/../step_4_process/output/runs/" . $version . "/multimodel_runs.jsonl";

if (!is_file($RUNS_FILE)) {
    echo json_encode(["ok" => true, "config_version" => $version, "n_runs" => 0, "models" => [], "clusters" => [], "stats" => []]);
    exit;
}

$runs = [];
$fh = fopen($RUNS_FILE, "r");
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
    "config_version" => $version,
    "n_runs" => count($runs),
    "models" => $models,
    "clusters" => $clusters,
    "stats" => $stats,
], JSON_UNESCAPED_UNICODE);
