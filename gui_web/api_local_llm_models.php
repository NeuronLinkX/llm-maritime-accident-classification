<?php
/**
 * STEP 4 — 분기 2: DGX Spark 로컬 LLM 서버의 모델 카탈로그(GET /v1/models) 프록시.
 *
 * "전체 모델 비교 실행" 기능이 어떤 모델들을 순회해야 하는지 알아내는 데 쓴다 —
 * MODEL_CATALOG를 프런트엔드에 하드코딩하지 않고 local_llm_server.py를 단일
 * 소스로 유지하기 위함.
 *
 * 입력(GET 쿼리): endpoint = chat completions URL (예: http://localhost:8500/v1/chat/completions)
 * 출력: {"ok": true, "data": [{"id","status","device","error"}, ...]} 또는 {"ok": false, "error": "..."}
 */

declare(strict_types=1);

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");

require_once __DIR__ . "/lib_llm_common.php";

const DEFAULT_ENDPOINT = "http://localhost:8500/v1/chat/completions";

$endpoint = trim((string)($_GET["endpoint"] ?? DEFAULT_ENDPOINT));
if ($endpoint === "") $endpoint = DEFAULT_ENDPOINT;

$baseUrl = \LlmCommon\derive_local_base_url($endpoint);
if ($baseUrl === null) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "엔드포인트 URL을 해석할 수 없습니다: " . $endpoint]);
    exit;
}

$ctx = stream_context_create([
    "http" => ["method" => "GET", "timeout" => 5, "ignore_errors" => true],
]);
$resp = @file_get_contents($baseUrl . "/v1/models", false, $ctx);

if ($resp === false) {
    echo json_encode(["ok" => false, "error" => "서버에 연결할 수 없습니다 ({$baseUrl}). DGX Spark에서 local_llm_server.py가 실행 중인지 확인하세요."]);
    exit;
}

$data = json_decode($resp, true);
if (!is_array($data) || !isset($data["data"])) {
    echo json_encode(["ok" => false, "error" => "서버 응답을 해석하지 못했습니다.", "raw" => $resp]);
    exit;
}

echo json_encode(["ok" => true, "data" => $data["data"]], JSON_UNESCAPED_UNICODE);
