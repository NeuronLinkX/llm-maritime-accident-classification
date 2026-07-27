<?php
/**
 * STEP 4 — 분기 2: DGX Spark 로컬 LLM 서버로 STEP3 군집을 레이블링하는 엔드포인트.
 *
 * api_llm_label.php(OpenAI 경로)와 군집 데이터 수집·프롬프트는
 * lib_llm_common.php를 그대로 공유하고, 호출 대상만 로컬 서버로 바뀐다.
 * 로컬 서버는 API 키가 필요 없다 — 요청 바디의 endpoint로 어디를 부를지만 받는다.
 *
 * 입력(JSON 바디): {"endpoint": "http://localhost:8500/v1/chat/completions", "model": "Qwen/Qwen2.5-3B-Instruct",
 *                  "samples_per_cluster": {"0": 8, "1": 10, ...}}
 *                 model은 선택 — 생략하면 로컬 서버의 카탈로그 첫 번째 모델이 쓰인다.
 *                 samples_per_cluster도 선택 — 웹 UI에서 군집별 표본 수를 직접 지정할 때 쓰고,
 *                 생략하면 lib_llm_common.php의 기본 SAMPLES_PER_CLUSTER를 쓴다.
 *                 "전체 모델 비교 실행"은 이 엔드포인트를 모델별로 반복 호출한다.
 * 출력: api_llm_label.php와 동일한 형식(+ 실제 사용된 모델명이 "model" 필드에 반영됨)
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

const DEFAULT_ENDPOINT = "http://localhost:8500/v1/chat/completions";

$body = json_decode((string)file_get_contents("php://input"), true) ?: [];
$endpoint = trim((string)($body["endpoint"] ?? DEFAULT_ENDPOINT));
if ($endpoint === "") $endpoint = DEFAULT_ENDPOINT;
if (!parse_url($endpoint) || !preg_match('#^https?://#', $endpoint)) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "엔드포인트 URL이 올바르지 않습니다: " . $endpoint]);
    exit;
}
$model = trim((string)($body["model"] ?? ""));
$samplesOverride = \LlmCommon\sanitize_samples_override($body["samples_per_cluster"] ?? null);

$clusterBlocks = \LlmCommon\build_cluster_blocks($samplesOverride);
if ($clusterBlocks === null) {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "STEP3 산출물(clusters.csv)을 찾을 수 없습니다. kmeans를 먼저 실행하세요."]);
    exit;
}
[$systemPrompt, $userPrompt] = \LlmCommon\build_prompt($clusterBlocks);

$payload = [
    "messages" => [
        ["role" => "system", "content" => $systemPrompt],
        ["role" => "user", "content" => $userPrompt],
    ],
    "temperature" => 0.2,
    "max_tokens" => 2000,
    "response_format" => ["type" => "json_object"],
];
if ($model !== "") $payload["model"] = $model;

// 여기서 "모델이 로드될 때까지 재시도"를 하지 않는다 — PHP 내장 서버(php -S)는
// 요청을 한 번에 하나만 처리하는 단일 스레드라, sleep()으로 재시도 루프를 돌리면
// 그동안 다른 모든 요청(다른 모델 호출, 상태 확인 등)이 전부 막힌다. 대신
// "모델 로드 대기"는 프런트엔드가 api_local_llm_health.php를 짧은 간격으로
// 폴링해서 처리하고(report_template.html의 waitForLocalModelReady), 이 엔드포인트는
// "이미 로드가 끝났다고 판단한 뒤"에만 호출된다는 전제로 생성 타임아웃만 넉넉히 둔다.
//
// 180초였을 때 Qwen2.5-7B-Instruct만 유독 자주 502로 실패했다 — 크기는 14B보다
// 작은데도(14B는 실패한 적 없음) rationale을 "2문장 이내"로 요청해도 유독 길게
// 쓰는 경향이 있어(실측 응답 길이가 3B/14B보다 김), temperature=0.2의 확률적
// 변동과 겹치면 종종 180초를 넘겼다. 실측 소요시간 분포(33~102초)에 4배 이상
// 여유를 두고 400초로 올린다.
$streamContext = stream_context_create([
    "http" => [
        "method" => "POST",
        "header" => "Content-Type: application/json\r\n",
        "content" => json_encode($payload, JSON_UNESCAPED_UNICODE),
        "timeout" => 400,
        "ignore_errors" => true,
    ],
]);
$resp = @file_get_contents($endpoint, false, $streamContext);

$httpCode = 0;
if (isset($http_response_header[0]) && preg_match('/\s(\d{3})\s/', $http_response_header[0], $m)) {
    $httpCode = (int)$m[1];
}
$respData = $resp === false ? null : json_decode($resp, true);

if ($resp === false) {
    $err = error_get_last();
    http_response_code(502);
    echo json_encode(["ok" => false, "error" => "로컬 LLM 호출 실패: " . ($err["message"] ?? "알 수 없는 오류") . " ({$endpoint})"]);
    exit;
}

if ($httpCode >= 400) {
    $msg = $respData["detail"] ?? $respData["error"]["message"] ?? ("HTTP " . $httpCode);
    http_response_code(502);
    echo json_encode(["ok" => false, "error" => "로컬 LLM 오류: " . (is_string($msg) ? $msg : json_encode($msg))]);
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
    "model" => $respData["model"] ?? "local",
    "clusters" => $clusters,
    "config_version" => \LlmCommon\config_version_key($samplesOverride),
], JSON_UNESCAPED_UNICODE);
