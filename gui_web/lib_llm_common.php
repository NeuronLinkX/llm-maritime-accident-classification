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

// local_llm_server.py(load_config())와 같은 파일을 공유한다 — 모델/포트는
// 그쪽이, 프롬프트·생성 기본값(temperature)은 여기가 읽어서 두 언어 쪽 설정이
// 어긋나지 않게 한다. 파일이 없거나 JSON이 깨졌어도 예외를 던지지 않고
// 아래 _DEFAULT_* 값으로 조용히 폴백한다("실패는 조용히 폴백" 원칙, STEP1과 동일).
const CONFIG_JSON_PATH = __DIR__ . "/../config/config.json";

/** config/config.json을 읽어 배열로 반환한다. 없거나 깨졌으면 빈 배열. */
function app_config(): array {
    static $cfg = null;
    if ($cfg !== null) return $cfg;
    if (!is_file(CONFIG_JSON_PATH)) return $cfg = [];
    $raw = file_get_contents(CONFIG_JSON_PATH);
    $decoded = json_decode($raw, true);
    return $cfg = (is_array($decoded) ? $decoded : []);
}

// 해양안전심판원(KMST) 자체 사고원인 분류체계 — 국내기준이나 국제기준이 없어 KMST가 자체 제정한
// 3대분류(운항과실 13종 / 취급 불량 및 결함 3종 / 기타 6종, 아래 순서 그대로)와 그 세부항목 22종.
// 프롬프트에는 세부항목만 평평하게 나열한다 — 예전에 "[대분류] 항목1, 항목2 / [대분류] ..." 형태로
// 대괄호 태그를 같이 보여줬더니, 로컬 소형 모델(Qwen2.5-3B)이 "대괄호를 옮겨 적지 말라"는 지시를
// 무시하고 태그를 답변에 그대로(심지어 잘못된 대분류로) 베껴 썼다 — 태그가 프롬프트에 없으면 애초에
// 베낄 대상이 없다. 대분류가 필요하면(예: 상위 집계) 이 배열의 순서로 서버 쪽에서 역으로 찾는다.
// 항목 이름 안의 접속 표현은 쉼표(항목 구분자)와 구분하기 위해 가운뎃점(·)으로 적는다.
const _DEFAULT_CANDIDATE_LABELS = "출항준비 불량, 수로조사 불충분, 침로의 선정·유지불량, 선위확인 소홀, "
    . "조선 부적절, 경계소홀, 황천대비·대응 불량, 묘박·계류의 부적절, 항행법규 위반, 복무감독 소홀, "
    . "당직근무 태만, 운항과실 기타, 선내작업안전수칙 미준수, "
    . "선체·기관설비 결함, 기관설비 취급 불량, 화기취급 불량·전선노후·합선, "
    . "여객·화물의 적재 불량, 선박운항관리 부적절, 승무원 배승 부적절, 항해원조시설 등의 부적절, "
    . "기상 등 불가항력, 기타";
const _DEFAULT_SYSTEM_PROMPT = "당신은 해양사고 원인 분류 전문가입니다. 반드시 유효한 JSON 객체만 출력하세요.";
const _DEFAULT_INSTRUCTION = "각 군집의 특징 키워드(그 군집에서 유독 자주 나오는 단어)와 대표 문장 일부를 근거로, "
    . "아래 해양안전심판원(KMST) 자체 사고원인 분류체계(국내·국제 공통기준이 없어 KMST가 자체 제정, "
    . "대분류 3종 아래 세부항목으로 구성)에서 가장 적절한 세부항목을 반드시 후보 목록에 있는 이름 그대로 골라 주세요. "
    . "새 라벨을 지어내지 마세요. 군집 키워드와 대표 문장 중 실제로 해당 항목을 뒷받침하는 부분을 찾아 "
    . "가장 가까운 항목을 고르세요. \"기타\"는 나머지 항목 전부가 근거로 뒷받침되지 않을 때만 선택하세요 — "
    . "판단이 애매하다는 이유만으로 고르지 마세요. rationale에는 참고한 군집 키워드를 최소 1개 이상 그대로 "
    . "인용하세요. \"기타\"를 고른 경우에는 어떤 다른 항목들을 검토했고 왜 맞지 않았는지 구체적으로 쓰세요. "
    . "(같은 표본을 반복 실행해도 라벨이 매번 다른 문구로 흔들리지 않게 하기 위함입니다.) "
    . "그 근거를 한국어 2문장 이내로 설명해 주세요.";
const _DEFAULT_TEMPERATURE = 0.2;

/**
 * config.json의 candidate_labels를 프롬프트용 평문 문자열로 만든다.
 * config.json에는 사람이 KMST 표를 그대로 보고 편집할 수 있게 대분류(운항과실/
 * 취급 불량 및 결함/기타) 키 아래 세부항목 배열로 적어 두지만, 모델에게 보낼 때는
 * 대분류 태그 없이 세부항목만 평평하게 이어붙인다 — 대괄호 태그를 프롬프트에
 * 노출하면 소형 로컬 모델(Qwen2.5-3B)이 그 태그를 답변에 그대로(심지어 잘못된
 * 대분류로) 베껴 쓰는 문제가 있었다. "_"로 시작하는 키(예: _comment)는 사람이
 * 남긴 설명이므로 건너뛴다. 과거 형식(평문 문자열)이 들어와도 그대로 지원한다.
 */
function candidate_labels(): string {
    $raw = app_config()["prompt"]["candidate_labels"] ?? null;
    if ($raw === null) return _DEFAULT_CANDIDATE_LABELS;
    if (is_string($raw)) return $raw;
    if (!is_array($raw)) return _DEFAULT_CANDIDATE_LABELS;

    $items = [];
    foreach ($raw as $key => $value) {
        if (is_string($key) && str_starts_with($key, "_")) continue; // _comment 등
        if (is_array($value)) {
            foreach ($value as $item) $items[] = (string)$item;
        } else {
            $items[] = (string)$value;
        }
    }
    return $items ? implode(", ", $items) : _DEFAULT_CANDIDATE_LABELS;
}

function system_prompt(): string {
    return app_config()["prompt"]["system_prompt"] ?? _DEFAULT_SYSTEM_PROMPT;
}

/** instruction이 배열(줄 단위)이면 개행으로 이어붙이고, 문자열이면 그대로 쓴다. */
function prompt_instruction(): string {
    $raw = app_config()["prompt"]["instruction"] ?? null;
    if ($raw === null) return _DEFAULT_INSTRUCTION;
    if (is_array($raw)) return implode("\n", array_map("strval", $raw));
    return (string)$raw;
}
/** api_llm_label.php / api_local_llm_label.php가 공유하는 생성 temperature 기본값. */
function default_temperature(): float {
    return (float)(app_config()["generation"]["default_temperature"] ?? _DEFAULT_TEMPERATURE);
}

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
//
// id는 유니코드 NFC로 정규화한 뒤 비교한다. macOS 파일시스템(HFS+/APFS)은 한글
// 파일명을 NFD(자모 분해형)로 저장하는데, STEP1을 macOS에서 재실행하면
// step1_dataset.jsonl의 id가 NFD가 되는 반면 STEP2/3 산출물(clusters.csv 등)이
// 이전 실행(NFC)에서 그대로 남아 있으면 두 쪽 id가 어긋난다 — 화면에는 완전히
// 같은 글자로 보이지만 바이트가 달라 정확 문자열 비교(===)가 전부 실패하고,
// 대표문장(sample_sentences)이 조용히 0건이 되어(예외 없음) STEP4가 근거 문장
// 없이 키워드 8개만으로 라벨링해야 하는 상황으로 이어졌다.
function load_texts_by_id(array $wantedIds): array {
    $texts = [];
    if (!is_file(STEP1_JSONL)) return $texts;
    $normalize = fn(string $s): string => \Normalizer::normalize($s, \Normalizer::FORM_C) ?: $s;
    // normalizedId => 원본 요청 id. 호출자는 원본 id로 다시 조회하므로, 매치되면
    // 정규화된 폼이 아니라 원본 형태의 키로 돌려줘야 호출부 조회가 깨지지 않는다.
    $wanted = [];
    foreach ($wantedIds as $id) $wanted[$normalize($id)] = $id;

    $fh = fopen(STEP1_JSONL, "r");
    while (($line = fgets($fh)) !== false) {
        $line = trim($line);
        if ($line === "") continue;
        $d = json_decode($line, true);
        if (!$d || !isset($d["id"])) continue;
        $normId = $normalize($d["id"]);
        if (!isset($wanted[$normId])) continue;
        $texts[$wanted[$normId]] = $d["embedding_text"] ?? "";
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

/**
 * [systemPrompt, userPrompt] 반환. system_prompt/instruction/candidate_labels는
 * config/config.json의 "prompt" 절에서 읽는다(없으면 _DEFAULT_* 폴백) — 문구를
 * 바꾸고 싶을 때 코드를 고치지 않고 config.json만 수정하면 되게 하기 위함이다.
 * 군집 수·JSON 스키마 등 요청마다 달라지는 구조적인 부분만 여기 코드로 남긴다.
 */
function build_prompt(array $clusterBlocks): array {
    $nClusters = count($clusterBlocks);
    $userPrompt = "다음은 한국 해양안전심판원 재결서를 SBERT 임베딩 + K-Means로 군집화한 결과입니다. "
        . "군집은 총 {$nClusters}개(cluster 번호: " . implode(", ", array_column($clusterBlocks, "cluster")) . ")이며, "
        . "**반드시 {$nClusters}개 군집 전부에 대해 빠짐없이 하나씩** 라벨을 제안해야 합니다. 일부만 답하지 마세요.\n\n"
        . prompt_instruction() . "\n\n"
        . "사고원인 후보: " . candidate_labels() . "\n\n"
        . "다음 JSON 형식으로만 답하세요 (다른 텍스트 금지, clusters 배열 길이는 반드시 {$nClusters}. "
        . "proposed_label은 위 후보 목록의 이름과 정확히 일치해야 하며, 새로 지어낸 표현은 쓰지 마세요):\n"
        . '{"clusters": [{"cluster": 0, "proposed_label": "...", "rationale": "...", "confidence": 0.0}]}' . "\n\n"
        . "군집 데이터:\n" . json_encode($clusterBlocks, JSON_UNESCAPED_UNICODE);

    return [system_prompt(), $userPrompt];
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
        "candidate_labels" => candidate_labels(),
        "system_prompt" => system_prompt(),
        "instruction" => prompt_instruction(),
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
