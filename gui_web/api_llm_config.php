<?php
/**
 * STEP 4 — 웹 UI가 "군집별 표본 수" 입력창을 채울 기본값을 조회하는 엔드포인트.
 *
 * lib_llm_common.php의 SAMPLES_PER_CLUSTER 상수와 실제 존재하는 군집 id 목록을
 * 그대로 반환한다. JS 쪽에 기본값을 하드코딩해 상수와 따로 놀지 않게 하려는 목적.
 *
 * 출력: {"ok": true, "clusters": [0,1,2,3,4], "samples_per_cluster": {"0":8,...},
 *        "samples_default": 5, "config_version": "s8-10-5-15-3_a2f99347"}
 */

declare(strict_types=1);

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");

require_once __DIR__ . "/lib_llm_common.php";

$byCluster = \LlmCommon\load_cluster_ids();
$clusters = array_keys($byCluster);
sort($clusters);

$samples = [];
foreach ($clusters as $c) {
    $samples[(string)$c] = \LlmCommon\SAMPLES_PER_CLUSTER[$c] ?? \LlmCommon\SAMPLES_PER_CLUSTER_DEFAULT;
}

echo json_encode([
    "ok" => true,
    "clusters" => $clusters,
    "samples_per_cluster" => $samples,
    "samples_default" => \LlmCommon\SAMPLES_PER_CLUSTER_DEFAULT,
    "config_version" => \LlmCommon\config_version_key(),
], JSON_UNESCAPED_UNICODE);
