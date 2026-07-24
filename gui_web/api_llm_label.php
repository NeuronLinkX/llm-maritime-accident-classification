<?php
/**
 * STEP 4 — 분기 1: OpenAI API로 STEP3 군집을 사전 정의된 사고원인 대분류로
 * 빠르게 레이블링해 보는 실험적 엔드포인트.
 *
 * API 키는 이 파일(서버 사이드)에서만 쓰고 브라우저로는 절대 내려보내지
 * 않는다 — 프런트엔드는 이 엔드포인트를 POST로 호출해 결과 JSON만 받는다.
 * 군집 데이터 수집·프롬프트 구성은 lib_llm_common.php(로컬 LLM 경로와 공유).
 *
 * 출력: {"ok": true, "model": "...", "clusters": [{cluster, n_docs, proposed_label, rationale, confidence}, ...]}
 *      또는 {"ok": false, "error": "..."}
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

const CONFIG_PATH = __DIR__ . "/../config/config.php";

if (!is_file(CONFIG_PATH)) {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "config/config.php를 찾을 수 없습니다."]);
    exit;
}
require_once CONFIG_PATH;

if (!defined("OPENAI_API_KEY") || trim(OPENAI_API_KEY) === "") {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "config.php에 OPENAI_API_KEY가 비어 있습니다."]);
    exit;
}

$clusterBlocks = \LlmCommon\build_cluster_blocks();
if ($clusterBlocks === null) {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "STEP3 산출물(clusters.csv)을 찾을 수 없습니다. kmeans를 먼저 실행하세요."]);
    exit;
}
[$systemPrompt, $userPrompt] = \LlmCommon\build_prompt($clusterBlocks);

$payload = [
    "model" => defined("OPENAI_CHAT_MODEL") ? OPENAI_CHAT_MODEL : "gpt-4o-mini",
    "messages" => [
        ["role" => "system", "content" => $systemPrompt],
        ["role" => "user", "content" => $userPrompt],
    ],
    "temperature" => 0.2,
    "max_tokens" => 2000,
    "response_format" => ["type" => "json_object"],
];

// 이 환경엔 curl 확장이 없어서(php -m에 없음) file_get_contents +
// 스트림 컨텍스트로 HTTPS POST를 보낸다 — openssl 확장은 있어서 TLS는 된다.
$streamContext = stream_context_create([
    "http" => [
        "method" => "POST",
        "header" => "Content-Type: application/json\r\nAuthorization: Bearer " . OPENAI_API_KEY . "\r\n",
        "content" => json_encode($payload, JSON_UNESCAPED_UNICODE),
        "timeout" => 60,
        "ignore_errors" => true, // 4xx/5xx여도 응답 바디를 받아서 에러 메시지를 읽기 위함
    ],
]);
$resp = @file_get_contents("https://api.openai.com/v1/chat/completions", false, $streamContext);

$httpCode = 0;
if (isset($http_response_header[0]) && preg_match('/\s(\d{3})\s/', $http_response_header[0], $m)) {
    $httpCode = (int)$m[1];
}

if ($resp === false) {
    $err = error_get_last();
    http_response_code(502);
    echo json_encode(["ok" => false, "error" => "OpenAI 호출 실패: " . ($err["message"] ?? "알 수 없는 오류")]);
    exit;
}

$respData = json_decode($resp, true);
if ($httpCode >= 400) {
    $msg = $respData["error"]["message"] ?? ("HTTP " . $httpCode);
    http_response_code(502);
    echo json_encode(["ok" => false, "error" => "OpenAI 오류: " . $msg]);
    exit;
}

$content = $respData["choices"][0]["message"]["content"] ?? "";
$clusters = \LlmCommon\parse_clusters_response($content);
if ($clusters === null) {
    http_response_code(502);
    echo json_encode(["ok" => false, "error" => "모델 응답을 JSON으로 해석하지 못했습니다.", "raw" => $content]);
    exit;
}

echo json_encode([
    "ok" => true,
    "model" => $payload["model"],
    "clusters" => $clusters,
], JSON_UNESCAPED_UNICODE);
