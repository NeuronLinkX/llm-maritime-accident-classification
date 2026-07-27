<?php
/**
 * STEP 4 — 저장된 "전체 모델 비교 실행" 기록을 조회한다.
 *
 * 기록은 설정 버전별 폴더(step_4_process/output/runs/{버전}/multimodel_runs.jsonl)에
 * 나뉘어 쌓인다(lib_llm_common.php의 config_version_key() 참고). 기본값은 현재
 * 코드의 그라운딩 설정(SAMPLES_PER_CLUSTER 등)에 해당하는 폴더만 읽는다 — 과거
 * 다른 설정으로 만든 기록과 섞이지 않게 하기 위함이다.
 *
 * 입력(GET 쿼리):
 *   (없음)          → 현재 설정 버전의 기록 목록(id, saved_at, 모델별 성공 여부 요약)
 *   version=...     → 지정한 설정 버전 폴더의 기록 목록(과거 기록 조회용)
 *   id=...          → 그 실행의 전체 결과(results, 즉 각 모델의 clusters 전부)를 반환
 *                      (version과 함께 쓰면 그 버전 폴더에서, 아니면 현재 버전 폴더에서 찾음)
 * 출력: {"ok": true, "config_version": "...", "versions": [...], "runs": [...]}
 *      또는 {"ok": true, "run": {...}}
 */

declare(strict_types=1);

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");

require_once __DIR__ . "/lib_llm_common.php";

function read_runs(string $runsFile): array {
    if (!is_file($runsFile)) return [];
    $runs = [];
    $fh = fopen($runsFile, "r");
    while (($line = fgets($fh)) !== false) {
        $line = trim($line);
        if ($line === "") continue;
        $d = json_decode($line, true);
        if ($d) $runs[] = $d;
    }
    fclose($fh);
    return $runs;
}

$currentVersion = \LlmCommon\config_version_key();
$requestedVersion = trim((string)($_GET["version"] ?? ""));
$version = $requestedVersion !== "" ? $requestedVersion : $currentVersion;
// 버전 폴더명은 사용자 입력을 그대로 경로에 쓰므로, 실제 존재하는 폴더 중에서만 허용한다.
$knownVersions = \LlmCommon\list_config_versions();
if ($requestedVersion !== "" && !in_array($requestedVersion, $knownVersions, true)) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "알 수 없는 version입니다: {$requestedVersion}"]);
    exit;
}
$runsFile = __DIR__ . "/../step_4_process/output/runs/" . $version . "/multimodel_runs.jsonl";

$runs = read_runs($runsFile);
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

echo json_encode([
    "ok" => true,
    "config_version" => $version,
    "is_current_config" => $version === $currentVersion,
    "versions" => $knownVersions,
    "runs" => $summaries,
], JSON_UNESCAPED_UNICODE);
