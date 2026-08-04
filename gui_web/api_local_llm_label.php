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

// php.ini의 max_execution_time(기본 30초)은 아래 stream_context의 timeout=>400과
// 별개로 PHP 스크립트 자체를 강제 종료시킨다 — file_get_contents()가 30초를 넘기면
// "Maximum execution time exceeded" 치명적 오류로 php -S 프로세스 자체가 죽고,
// 그 순간 연결돼 있던 다른 모든 요청(브라우저 포함)도 "Failed to fetch"로 끊긴다.
// 아래 stream timeout(400초)보다 여유 있게 스크립트 실행시간 한도를 늘려둔다.
set_time_limit(420);

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
$promptVariant = \LlmCommon\sanitize_prompt_variant($body["prompt_variant"] ?? null);
$temperatureOverride = \LlmCommon\sanitize_temperature($body["temperature"] ?? null);

$clusterBlocks = \LlmCommon\build_cluster_blocks($samplesOverride);
if ($clusterBlocks === null) {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "STEP3 산출물(clusters.csv)을 찾을 수 없습니다. kmeans를 먼저 실행하세요."]);
    exit;
}
[$systemPrompt, $userPrompt] = \LlmCommon\build_prompt($clusterBlocks, $promptVariant);

$payload = [
    "messages" => [
        ["role" => "system", "content" => $systemPrompt],
        ["role" => "user", "content" => $userPrompt],
    ],
    // 표본(array_slice로 항상 동일)에 더해 temperature까지 0으로 고정하면 100회
    // 반복이 매번 같은 답을 내는 동어반복이 되어 재현성 측정 자체가 무의미해진다.
    // 라벨이 흔들리는 정도(A/G, 군집3처럼 낮게 나오는 것 포함)는 그 자체로
    // "이 군집 라벨을 얼마나 신뢰할 수 있는가"를 보여주는 진단 신호이므로 그대로 둔다.
    // "정리되어 보임"은 온도가 아니라 build_prompt()에서 후보 어휘를 고정한 것으로 확보한다.
    // 값 자체는 config/config.json의 generation.default_temperature — local_llm_server.py와
    // 공유하는 같은 파일이라 두 경로의 기본값이 어긋나지 않는다.
    "temperature" => \LlmCommon\default_temperature($temperatureOverride),
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
// PHP 8.4+의 $http_response_header 매직 변수는 PHP 8.5부터 deprecated다 —
// 직접 참조하면 경고가 응답 바디 맨 앞에 HTML로 섞여 들어가 JSON.parse()가
// 깨진다(브라우저에서 "Unexpected token '<'"로 나타남). http_get_last_response_headers()로
// 대체하되, 이 함수 자체가 PHP 8.4부터 생겨서 그보다 낮은 버전(이 서버는 8.3)에서는
// "Call to undefined function"으로 즉시 fatal error가 나 응답 바디가 통째로 비어버린다
// (브라우저에서 "Unexpected end of JSON input") — 있으면 새 함수, 없으면 매직 변수로 폴백.
$responseHeaders = function_exists("http_get_last_response_headers")
    ? (http_get_last_response_headers() ?? [])
    : ($http_response_header ?? []);
if (isset($responseHeaders[0]) && preg_match('/\s(\d{3})\s/', $responseHeaders[0], $m)) {
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
    "config_version" => \LlmCommon\config_version_key($samplesOverride, $promptVariant, $temperatureOverride),
    "prompt_variant" => $promptVariant,
    "temperature" => \LlmCommon\default_temperature($temperatureOverride),
], JSON_UNESCAPED_UNICODE);
