<?php
/**
 * STEP 4 — 저장된 "전체 모델 비교 실행" 기록을 조회한다.
 *
 * 입력(GET 쿼리):
 *   (없음)   → 기록 목록(id, saved_at, 모델별 성공 여부 요약)만 가볍게 반환
 *   id=...   → 그 실행의 전체 결과(results, 즉 각 모델의 clusters 전부)를 반환
 * 출력: {"ok": true, "runs": [...]} 또는 {"ok": true, "run": {...}}
 */

declare(strict_types=1);

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");

const RUNS_FILE = __DIR__ . "/../step_4_process/output/multimodel_runs.jsonl";

function read_runs(): array {
    if (!is_file(RUNS_FILE)) return [];
    $runs = [];
    $fh = fopen(RUNS_FILE, "r");
    while (($line = fgets($fh)) !== false) {
        $line = trim($line);
        if ($line === "") continue;
        $d = json_decode($line, true);
        if ($d) $runs[] = $d;
    }
    fclose($fh);
    return $runs;
}

$runs = read_runs();
$id = trim((string)($_GET["id"] ?? ""));

if ($id !== "") {
    $found = null;
    foreach ($runs as $r) if (($r["id"] ?? "") === $id) { $found = $r; break; }
    if (!$found) {
        http_response_code(404);
        echo json_encode(["ok" => false, "error" => "기록을 찾을 수 없습니다: {$id}"]);
        exit;
    }
    echo json_encode(["ok" => true, "run" => $found], JSON_UNESCAPED_UNICODE);
    exit;
}

$summaries = array_map(function ($r) {
    $models = array_map(fn($m) => ["model" => $m["model"] ?? "?", "ok" => (bool)($m["ok"] ?? false)], $r["results"] ?? []);
    return ["id" => $r["id"] ?? "", "saved_at" => $r["saved_at"] ?? "", "models" => $models];
}, $runs);
usort($summaries, fn($a, $b) => strcmp($b["id"], $a["id"])); // 최신 먼저

echo json_encode(["ok" => true, "runs" => $summaries], JSON_UNESCAPED_UNICODE);
