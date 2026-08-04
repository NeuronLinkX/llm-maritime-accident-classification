<?php
/**
 * STEP 4 — 프롬프트×temperature 실험 매트릭스 전용 헬퍼.
 *
 * run_prompt_temp_matrix.py가 각 (prompt_variant, temperature) 칸을 몇 건 저장했는지
 * 세려면 그 칸의 config_version(=저장 폴더명)을 미리 알아야 한다. 이 값은 모델 호출 없이도
 * lib_llm_common.php의 config_version_key()로 결정론적으로 계산 가능하므로, 매번 실제
 * 라벨링 요청을 하나 흘려보내 알아내는 대신(GPU를 쓸데없이 쓰지 않도록) 이 가벼운 GET
 * 엔드포인트로 바로 조회한다.
 *
 * 입력(GET 쿼리): prompt_variant=A|B|C (선택), temperature=0|0.2 (선택)
 * 출력: {"ok": true, "config_version": "...", "prompt_variant": "A"|null, "temperature": 0.2}
 */

declare(strict_types=1);

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");

require_once __DIR__ . "/lib_llm_common.php";

$promptVariant = \LlmCommon\sanitize_prompt_variant($_GET["prompt_variant"] ?? null);
$temperature = \LlmCommon\sanitize_temperature($_GET["temperature"] ?? null);

echo json_encode([
    "ok" => true,
    "config_version" => \LlmCommon\config_version_key(null, $promptVariant, $temperature),
    "prompt_variant" => $promptVariant,
    "temperature" => \LlmCommon\default_temperature($temperature),
], JSON_UNESCAPED_UNICODE);
