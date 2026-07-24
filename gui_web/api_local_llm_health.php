<?php
/**
 * STEP 4 — 분기 2: DGX Spark 로컬 LLM 서버(step_4_process/local_llm_server.py)의
 * "작동 유무 확인" 프록시.
 *
 * 브라우저가 로컬 LLM 서버로 직접 fetch할 수도 있지만(같은 호스트), OpenAI
 * 경로와 아키텍처를 통일하고 나중에 서버가 다른 호스트로 옮겨져도 프런트엔드
 * 코드를 안 바꾸도록 PHP가 한 번 프록시한다.
 *
 * 입력(GET 쿼리):
 *   endpoint = 사용자가 폼에 입력한 chat completions URL
 *              (예: http://localhost:8500/v1/chat/completions)
 *              이 URL에서 path를 "/health"로 바꿔서 확인한다.
 *   model    = (선택) 특정 모델 하나만 확인. 없으면 서버 전체 상태(카탈로그+로드된
 *              모델 목록)를 돌려준다.
 *   load     = (선택) "1"이면 model이 아직 안 받아졌을 때 다운로드/로드를 트리거.
 * 출력: {"ok": true, "status": "ok"|"loading"|"error", "model": "...", "device": "..."}
 *      또는 {"ok": false, "error": "..."}
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

$healthUrl = $baseUrl . "/health";
$model = trim((string)($_GET["model"] ?? ""));
if ($model !== "") {
    $healthUrl .= "?model=" . rawurlencode($model);
    if (($_GET["load"] ?? "") === "1") $healthUrl .= "&load=1";
}

$ctx = stream_context_create([
    "http" => ["method" => "GET", "timeout" => 5, "ignore_errors" => true],
]);
$resp = @file_get_contents($healthUrl, false, $ctx);

if ($resp === false) {
    echo json_encode(["ok" => false, "error" => "서버에 연결할 수 없습니다 ({$healthUrl}). DGX Spark에서 local_llm_server.py가 실행 중인지 확인하세요."]);
    exit;
}

$data = json_decode($resp, true);
if (!is_array($data)) {
    echo json_encode(["ok" => false, "error" => "서버 응답을 해석하지 못했습니다.", "raw" => $resp]);
    exit;
}

echo json_encode([
    "ok" => true,
    "status" => $data["status"] ?? "unknown",
    "model" => $data["model"] ?? null,
    "device" => $data["device"] ?? null,
    "error" => $data["error"] ?? null,
], JSON_UNESCAPED_UNICODE);
