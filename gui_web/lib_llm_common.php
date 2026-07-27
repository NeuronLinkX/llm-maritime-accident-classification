<?php
/**
 * STEP 4 공용 — 군집 데이터 수집 + 프롬프트 구성.
 *
 * OpenAI 경로(api_llm_label.php)와 로컬 LLM 경로(api_local_llm_label.php)가
 * "STEP3 산출물을 읽어 군집 블록을 만들고, 같은 프롬프트로 라벨을 요청한다"는
 * 로직을 완전히 공유한다 — 모델 백엔드만 다르므로, 그 차이만 각 엔드포인트에
 * 남기고 나머지는 여기 한 곳에 둔다.
 */

declare(strict_types=1);

namespace LlmCommon;

const STEP3_OUT = __DIR__ . "/../step_3_process/output";
const STEP1_JSONL = __DIR__ . "/../step_2_process/from_step1/step1_dataset.jsonl";
// Neyman 최적 배분(Cochran, 1977, Sampling Techniques §5.5)으로 계산한 군집별 표본 수.
// n_h = n * (N_h*S_h) / sum(N_h*S_h), S_h는 STEP2 ko-sroberta-sts 임베딩에서 군집 중심까지의
// 코사인 거리 표준편차(군집 내부 이질성). 재군집화(K 변경) 시 이 값도 다시 계산해야 한다.
const SAMPLES_PER_CLUSTER = [0 => 8, 1 => 10, 2 => 5, 3 => 15, 4 => 3];
const SAMPLES_PER_CLUSTER_DEFAULT = 5; // 카탈로그에 없는 군집 id 대비 폴백
const SAMPLE_TEXT_MAXLEN = 300; // 문자 단위 자르기(토큰 비용/생성 시간 절약)
const KEYWORDS_PER_CLUSTER = 8;
const CANDIDATE_LABELS = "경계소홀, 정비불량/기기결함, 화재/폭발, 인적과실(조선부주의), 기상/환경요인, "
    . "하역/작업안전(인명사상), 항법위반, 조종미숙, 계류/정박사고, 해양오염, 기타";

function read_csv(string $path): array {
    if (!is_file($path)) return [];
    $rows = [];
    $f = fopen($path, "r");
    $header = fgetcsv($f);
    while (($line = fgetcsv($f)) !== false) {
        if (count($line) !== count($header)) continue;
        $rows[] = array_combine($header, $line);
    }
    fclose($f);
    return $rows;
}

function load_cluster_ids(): array {
    $rows = read_csv(STEP3_OUT . "/clusters.csv");
    $byCluster = [];
    foreach ($rows as $r) {
        $byCluster[(int)$r["cluster"]][] = $r["id"];
    }
    ksort($byCluster);
    return $byCluster;
}

function load_keywords(): array {
    $rows = read_csv(STEP3_OUT . "/cluster_keywords.csv");
    $byCluster = [];
    foreach ($rows as $r) {
        if ($r["kind"] !== "distinctive") continue;
        $c = (int)$r["cluster"];
        if (!isset($byCluster[$c])) $byCluster[$c] = [];
        if (count($byCluster[$c]) >= KEYWORDS_PER_CLUSTER) continue;
        $byCluster[$c][] = $r["keyword"];
    }
    return $byCluster;
}

// step1_dataset.jsonl은 818줄이라 필요한 id만 골라도 전체를 훑어야 한다 —
// 매 요청마다 818줄 스캔은 가벼우니(수 ms) 별도 인덱스를 만들지 않는다.
function load_texts_by_id(array $wantedIds): array {
    $texts = [];
    if (!is_file(STEP1_JSONL)) return $texts;
    $wanted = array_flip($wantedIds);
    $fh = fopen(STEP1_JSONL, "r");
    while (($line = fgets($fh)) !== false) {
        $line = trim($line);
        if ($line === "") continue;
        $d = json_decode($line, true);
        if (!$d || !isset($d["id"]) || !isset($wanted[$d["id"]])) continue;
        $texts[$d["id"]] = $d["embedding_text"] ?? "";
    }
    fclose($fh);
    return $texts;
}

// 이 환경엔 mbstring 확장이 없어서(php -m에 없음) UTF-8 바이트 경계를
// 손으로 계산해 문자 단위로 자른다 — 바이트 단위 substr()을 쓰면 한글
// 문자 중간이 잘려 깨진 바이트가 남는다.
function utf8_head(string $s, int $maxChars): string {
    $len = strlen($s);
    $charCount = 0;
    $i = 0;
    while ($i < $len && $charCount < $maxChars) {
        $byte = ord($s[$i]);
        if ($byte < 0x80) $i += 1;
        elseif (($byte & 0xE0) === 0xC0) $i += 2;
        elseif (($byte & 0xF0) === 0xE0) $i += 3;
        elseif (($byte & 0xF8) === 0xF0) $i += 4;
        else $i += 1;
        $charCount++;
    }
    $truncated = substr($s, 0, $i);
    return ($i < $len) ? $truncated . "…" : $truncated;
}

/**
 * 요청에서 넘어온 군집별 표본 수 오버라이드를 정제한다. 실제 존재하는 군집
 * id만 남기고, 값은 1~30 범위로 잘라낸다(0 이하는 의미가 없고, 30 초과는
 * 프롬프트가 컨텍스트 한도에 가까워지는 걸 막기 위한 안전판 — README.md
 * "군집별 표본 배분 계산"의 토큰 예산 역산 참고). 유효한 항목이 하나도
 * 없으면 null을 반환해 호출자가 기본값(SAMPLES_PER_CLUSTER)을 쓰게 한다.
 */
function sanitize_samples_override($raw): ?array {
    if (!is_array($raw) || !$raw) return null;
    $validClusters = array_keys(load_cluster_ids());
    $out = [];
    foreach ($raw as $k => $v) {
        $c = (int)$k;
        if (!in_array($c, $validClusters, true)) continue;
        $n = (int)$v;
        if ($n < 1) $n = 1;
        if ($n > 30) $n = 30;
        $out[$c] = $n;
    }
    return $out ?: null;
}

/**
 * STEP3 산출물에서 군집별 {cluster, n_docs, keywords, sample_sentences} 블록을 만든다.
 * $samplesOverride를 주면 SAMPLES_PER_CLUSTER 상수 대신 그 값을 쓴다(웹 UI에서
 * 군집별 표본 수를 직접 지정하는 경로용 — sanitize_samples_override()로 먼저
 * 정제된 배열이어야 한다). 실패 시 null.
 */
function build_cluster_blocks(?array $samplesOverride = null): ?array {
    $byCluster = load_cluster_ids();
    if (!$byCluster) return null;
    $keywordsByCluster = load_keywords();
    $samplesCfg = $samplesOverride ?? SAMPLES_PER_CLUSTER;

    $sampleIds = [];
    foreach ($byCluster as $c => $ids) {
        $n = $samplesCfg[$c] ?? SAMPLES_PER_CLUSTER_DEFAULT;
        foreach (array_slice($ids, 0, $n) as $id) $sampleIds[] = $id;
    }
    $textsById = load_texts_by_id($sampleIds);

    $clusterBlocks = [];
    foreach ($byCluster as $c => $ids) {
        $n = $samplesCfg[$c] ?? SAMPLES_PER_CLUSTER_DEFAULT;
        $samples = [];
        foreach (array_slice($ids, 0, $n) as $id) {
            if (!empty($textsById[$id])) $samples[] = utf8_head($textsById[$id], SAMPLE_TEXT_MAXLEN);
        }
        $clusterBlocks[] = [
            "cluster" => $c,
            "n_docs" => count($ids),
            "keywords" => $keywordsByCluster[$c] ?? [],
            "sample_sentences" => $samples,
        ];
    }
    return $clusterBlocks;
}

/** [systemPrompt, userPrompt] 반환. */
function build_prompt(array $clusterBlocks): array {
    $nClusters = count($clusterBlocks);
    $userPrompt = "다음은 한국 해양안전심판원 재결서를 SBERT 임베딩 + K-Means로 군집화한 결과입니다. "
        . "군집은 총 {$nClusters}개(cluster 번호: " . implode(", ", array_column($clusterBlocks, "cluster")) . ")이며, "
        . "**반드시 {$nClusters}개 군집 전부에 대해 빠짐없이 하나씩** 라벨을 제안해야 합니다. 일부만 답하지 마세요.\n\n"
        . "각 군집의 특징 키워드(그 군집에서 유독 자주 나오는 단어)와 대표 문장 일부를 참고하여, "
        . "아래 사고원인 대분류 후보 중 가장 적절한 것을 고르거나(꼭 후보 안에서만 고를 필요는 없음, 더 적절한 라벨이 있으면 새로 제안) "
        . "그 근거를 한국어 2문장 이내로 설명해 주세요.\n\n"
        . "사고원인 대분류 후보: " . CANDIDATE_LABELS . "\n\n"
        . "다음 JSON 형식으로만 답하세요 (다른 텍스트 금지, clusters 배열 길이는 반드시 {$nClusters}):\n"
        . '{"clusters": [{"cluster": 0, "proposed_label": "...", "rationale": "...", "confidence": 0.0}]}' . "\n\n"
        . "군집 데이터:\n" . json_encode($clusterBlocks, JSON_UNESCAPED_UNICODE);

    return ["당신은 해양사고 원인 분류 전문가입니다. 반드시 유효한 JSON 객체만 출력하세요.", $userPrompt];
}

/**
 * 모델 응답(choices[0].message.content)에서 {"clusters":[...]}를 파싱. 실패 시 null.
 *
 * 로컬 모델(특히 소형)은 "JSON만 출력하라"는 지시를 항상 지키지는 않는다 — 실제로
 * 관찰된 패턴들:
 *   - ```json ... ``` 코드펜스로 감싸기
 *   - 앞에 여는 중괄호를 한 번 더 씀: "{\n{\"clusters\": [...]}"
 *   - 첫 항목 키에 따옴표를 중복해서 씀: "{\"\"cluster\": 0, ...}"
 *   - 앞뒤에 설명 텍스트를 덧붙이기
 * 정확히 파싱되는 시도부터 점점 관대해지는 순서로 여러 번 시도한다.
 */
function parse_clusters_response(string $content): ?array {
    $tryDecode = function (string $s): ?array {
        $parsed = json_decode($s, true);
        return (is_array($parsed) && isset($parsed["clusters"]) && is_array($parsed["clusters"]))
            ? $parsed["clusters"] : null;
    };

    if (($r = $tryDecode($content)) !== null) return $r;

    // 1) 마크다운 코드펜스 제거
    $stripped = trim(preg_replace('/^```(?:json)?\s*|\s*```\s*$/m', "", trim($content)));
    if (($r = $tryDecode($stripped)) !== null) return $r;

    // 2) 흔한 소형 모델 오타 보정: 중복 여는 중괄호, 중복 따옴표
    $repaired = preg_replace('/^\s*\{\s*(?=\{)/', "", $stripped, 1);
    $repaired = preg_replace('/\{""(?=\w)/u', '{"', $repaired);
    if (($r = $tryDecode($repaired)) !== null) return $r;

    // 3) 그래도 안 되면 첫 '{'~마지막 '}' 구간만 잘라내 마지막으로 시도
    //    (부연 설명이 JSON 앞뒤에 섞여 나온 경우 대비)
    $start = strpos($repaired, "{");
    $end = strrpos($repaired, "}");
    if ($start !== false && $end !== false && $end > $start) {
        if (($r = $tryDecode(substr($repaired, $start, $end - $start + 1))) !== null) return $r;
    }

    return null;
}

/**
 * STEP4 그라운딩 설정(표본 배분·절단 길이·키워드 수·후보라벨)을 식별하는
 * 버전 키. 이 중 하나라도 바뀌면 키가 자동으로 달라져 실행 기록이 다른 폴더에
 * 쌓인다 — 서로 다른 프롬프트로 만들어진 기록이 안정성 통계(api_multimodel_stability.php)
 * 에서 섞이는 걸 막기 위함이다. 사람이 폴더명만 보고도 어떤 설정인지 알 수 있게
 * "s{군집별표본수}_{짧은해시}" 형태로 만든다(해시는 표본수 외 다른 값 변경도 감지).
 *
 * $samplesOverride를 주면(웹 UI에서 직접 지정한 값) 그 조합 전용 폴더가 생긴다 —
 * 코드의 기본 SAMPLES_PER_CLUSTER와 별개로, 실제로 이 요청에 쓰인 표본 수를
 * 기준으로 버전을 나눠야 실행 기록과 저장 폴더가 항상 일치하기 때문이다.
 */
function config_version_key(?array $samplesOverride = null): string {
    $spc = $samplesOverride ?? SAMPLES_PER_CLUSTER;
    ksort($spc);
    $sig = [
        "samples_per_cluster" => $spc,
        "samples_default" => SAMPLES_PER_CLUSTER_DEFAULT,
        "sample_text_maxlen" => SAMPLE_TEXT_MAXLEN,
        "keywords_per_cluster" => KEYWORDS_PER_CLUSTER,
        "candidate_labels" => CANDIDATE_LABELS,
    ];
    $hash = substr(md5(json_encode($sig)), 0, 8);
    return "s" . implode("-", $spc) . "_" . $hash;
}

/** config_version_key($samplesOverride)에 대응하는 실행 기록 저장 디렉터리. 없으면 만든다. */
function runs_dir_for_config(?array $samplesOverride = null): string {
    $dir = __DIR__ . "/../step_4_process/output/runs/" . config_version_key($samplesOverride);
    if (!is_dir($dir)) mkdir($dir, 0775, true);
    return $dir;
}

/** step_4_process/output/runs/ 아래 존재하는 모든 버전 폴더 이름을 최신 순 없이 나열. */
function list_config_versions(): array {
    $base = __DIR__ . "/../step_4_process/output/runs";
    if (!is_dir($base)) return [];
    $versions = [];
    foreach (scandir($base) as $name) {
        if ($name === "." || $name === "..") continue;
        if (is_dir($base . "/" . $name)) $versions[] = $name;
    }
    sort($versions, SORT_STRING);
    return $versions;
}

/** chat-completions 엔드포인트 URL에서 "scheme://host[:port]"만 뽑아낸다(경로 제외). 실패 시 null. */
function derive_local_base_url(string $chatEndpoint): ?string {
    $parts = parse_url($chatEndpoint);
    if (!$parts || !isset($parts["scheme"], $parts["host"])) return null;
    $url = $parts["scheme"] . "://" . $parts["host"];
    if (isset($parts["port"])) $url .= ":" . $parts["port"];
    return $url;
}
