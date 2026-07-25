<?php
/**
 * STEP 4 — "전체 모델 비교 실행" 결과를 디스크에 남겨서, 매번 다시 돌리지
 * 않아도 지난 기록을 다시 볼 수 있게 한다.
 *
 * 한 줄에 한 실행(JSON Lines) — step_4_process/output/multimodel_runs.jsonl에
 * append만 한다(읽기-수정-쓰기 경쟁 상태를 피하려고). 파일이 커지면 오래된
 * 줄부터 정리할 수 있지만, 지금 규모(실행당 수십 KB)에선 문제가 안 된다.
 *
 * 입력(JSON 바디): {"results": [{"model":..., "ok":bool, "clusters":[...], "error":...}, ...]}
 * 출력: {"ok": true, "id": "20260125-091500"}
 */

declare(strict_types=1);

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");

if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    http_response_code(405);
    echo json_encode(["ok" => false, "error" => "POST로만 호출할 수 있습니다."]);
    exit;
}

const OUT_DIR = __DIR__ . "/../step_4_process/output";
const RUNS_FILE = OUT_DIR . "/multimodel_runs.jsonl";

$body = json_decode((string)file_get_contents("php://input"), true) ?: [];
$results = $body["results"] ?? null;
if (!is_array($results) || !$results) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "results 배열이 필요합니다."]);
    exit;
}

if (!is_dir(OUT_DIR)) mkdir(OUT_DIR, 0775, true);

$id = date("Ymd-His");
$record = ["id" => $id, "saved_at" => date("c"), "results" => $results];

$line = json_encode($record, JSON_UNESCAPED_UNICODE) . "\n";
$fh = fopen(RUNS_FILE, "a");
if ($fh === false) {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "기록 파일을 열 수 없습니다."]);
    exit;
}
flock($fh, LOCK_EX);
fwrite($fh, $line);
flock($fh, LOCK_UN);
fclose($fh);

echo json_encode(["ok" => true, "id" => $id], JSON_UNESCAPED_UNICODE);
