<?php
/**
 * STEP 4 — "전체 모델 비교 실행" 결과를 디스크에 남겨서, 매번 다시 돌리지
 * 않아도 지난 기록을 다시 볼 수 있게 한다.
 *
 * 한 줄에 한 실행(JSON Lines) — step_4_process/output/runs/{설정버전}/
 * multimodel_runs.jsonl에 append만 한다(읽기-수정-쓰기 경쟁 상태를 피하려고).
 * 설정버전 폴더는 lib_llm_common.php의 config_version_key()로 결정된다 —
 * SAMPLES_PER_CLUSTER 등 그라운딩 설정이 바뀌면 자동으로 다른 폴더에 쌓여서,
 * 서로 다른 프롬프트로 만든 기록이 섞이지 않는다.
 *
 * 입력(JSON 바디): {"results": [{"model":..., "ok":bool, "clusters":[...], "error":...}, ...],
 *                  "samples_per_cluster": {"0": 8, "1": 10, ...}}
 *                 samples_per_cluster는 선택 — 그 실행에 실제로 쓰인 군집별 표본 수를 그대로
 *                 넘겨야 config_version_key()가 label 호출 때와 같은 폴더로 계산된다(웹 UI에서
 *                 직접 지정한 경우 label 요청 때 쓴 값을 그대로 여기에도 실어 보내야 함).
 * 출력: {"ok": true, "id": "20260125-091500", "config_version": "s8-10-5-15-3_a2f99347"}
 */

declare(strict_types=1);

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");

if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    http_response_code(405);
    echo json_encode(["ok" => false, "error" => "POST로만 호출할 수 있습니다."]);
    exit;
}

require_once __DIR__ . "/lib_llm_common.php";

$body = json_decode((string)file_get_contents("php://input"), true) ?: [];
$results = $body["results"] ?? null;
if (!is_array($results) || !$results) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "results 배열이 필요합니다."]);
    exit;
}

$samplesOverride = \LlmCommon\sanitize_samples_override($body["samples_per_cluster"] ?? null);
$configVersion = \LlmCommon\config_version_key($samplesOverride);
$runsDir = \LlmCommon\runs_dir_for_config($samplesOverride); // 폴더 없으면 여기서 생성
$RUNS_FILE = $runsDir . "/multimodel_runs.jsonl";

$id = date("Ymd-His");
$record = [
    "id" => $id,
    "saved_at" => date("c"),
    "config_version" => $configVersion,
    "samples_per_cluster" => $samplesOverride ?? \LlmCommon\SAMPLES_PER_CLUSTER,
    "results" => $results,
];

$line = json_encode($record, JSON_UNESCAPED_UNICODE) . "\n";
$fh = fopen($RUNS_FILE, "a");
if ($fh === false) {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "기록 파일을 열 수 없습니다."]);
    exit;
}
flock($fh, LOCK_EX);
fwrite($fh, $line);
flock($fh, LOCK_UN);
fclose($fh);

echo json_encode(["ok" => true, "id" => $id, "config_version" => $configVersion], JSON_UNESCAPED_UNICODE);
