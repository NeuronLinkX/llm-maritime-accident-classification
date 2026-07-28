<?php
/**
 * STEP 1~3 결과를 하나의 통합 리포트로 실시간으로 보여주는 뷰어.
 *
 * report_template.html은 STEP1(전처리) + STEP2(SBERT 유사도 정량화) +
 * STEP3(K-Means 군집화)를 한 페이지에 담는다. STEP1 데이터는 이 파일이
 * 직접 만들고(재결서 data/ 폴더를 그 자리에서 스캔), STEP2/STEP3 데이터는
 * lib_step2_data.php / lib_step3_data.php(각각 Step2\, Step3\ 네임스페이스)의
 * build_data()를 그대로 불러 쓴다 — CSV 파싱 로직을 여기 또 구현하지 않기
 * 위함이다. 세 데이터 소스 모두 무겁지 않아(파일 스캔·CSV 읽기) 매 요청마다
 * 다시 읽어도 STEP1 단독 버전만큼 가볍다.
 *
 * 실행: php -S localhost:8000  (이 파일이 있는 디렉토리에서)
 * 접속: http://localhost:8000/report.php
 */

declare(strict_types=1);

header("Content-Type: text/html; charset=utf-8");
header("Cache-Control: no-store");

require_once __DIR__ . "/lib_step2_data.php";
require_once __DIR__ . "/lib_step3_data.php";

const ROOT = __DIR__;
// gui_web/은 시각화 전용 폴더로 분리돼 있고, 실제 data/·data_output/은
// 옆 폴더인 step_1_process/에 있다.
const STEP1_PROCESS_DIR = ROOT . "/../step_1_process";
const DATA_DIR = STEP1_PROCESS_DIR . "/data";
const OUT_DIR = STEP1_PROCESS_DIR . "/data_output";
const TEMPLATE_PATH = ROOT . "/report_template.html";

const KEYS = ["사건 개요", "일시", "장소", "사고 경위"];
const ELIGIBLE_EXTS = ["hwp", "hwpx", "pdf"];

function nfc(string $s): string {
    if (class_exists("Normalizer")) {
        $n = \Normalizer::normalize($s, \Normalizer::FORM_C);
        if ($n !== false) return $n;
    }
    return $s;
}

function ext_of(string $filename): string {
    $dot = strrpos($filename, ".");
    return $dot === false ? "" : strtolower(substr($filename, $dot + 1));
}

function stem_of(string $filename): string {
    $dot = strrpos($filename, ".");
    return $dot === false ? $filename : substr($filename, 0, $dot);
}

function category_of(string $filename): string {
    $name = nfc($filename);
    $pos = strpos($name, "_");
    if ($pos !== false) return substr($name, 0, $pos);
    $dot = strrpos($name, ".");
    return $dot === false ? $name : substr($name, 0, $dot);
}

function list_data_files(): array {
    if (!is_dir(DATA_DIR)) return [[], []];
    $entries = scandir(DATA_DIR);
    $eligible = [];
    $excluded = [];
    foreach ($entries as $name) {
        if ($name === "." || $name === "..") continue;
        $path = DATA_DIR . "/" . $name;
        if (!is_file($path)) continue;
        if (in_array(ext_of($name), ELIGIBLE_EXTS, true)) {
            $eligible[] = $name;
        } else {
            $excluded[] = $name;
        }
    }
    sort($eligible, SORT_STRING);
    sort($excluded, SORT_STRING);
    return [$eligible, $excluded];
}

// 출력 파일명 규칙이 두 가지 공존한다:
//   - run_data_gpu.sh(신버전): "foo.hwp.json" — 확장자 포함, hwp/pdf stem
//     충돌을 막기 위함.
//   - run_decoder_data.sh(기존 스크립트): "foo.json" — 확장자 없이 stem만.
// 신버전 이름이 있으면 그걸 우선 쓰고, 없으면 구버전 이름으로 대체한다.
function load_one(string $filename, string $stem): array {
    $jsonPath = OUT_DIR . "/" . $filename . ".json";
    $logPath = OUT_DIR . "/" . $filename . ".stderr.log";

    if (!is_file($jsonPath) || filesize($jsonPath) === 0) {
        $legacyJsonPath = OUT_DIR . "/" . $stem . ".json";
        $legacyLogPath = OUT_DIR . "/" . $stem . ".stderr.log";
        if (is_file($legacyJsonPath) && filesize($legacyJsonPath) > 0) {
            $jsonPath = $legacyJsonPath;
            $logPath = $legacyLogPath;
        }
    }

    if (!is_file($jsonPath) || filesize($jsonPath) === 0) {
        $err = "";
        if (is_file($logPath)) {
            $err = trim((string)file_get_contents($logPath));
        }
        return ["stem" => $stem, "ok" => false, "error" => $err !== "" ? $err : "출력 파일 없음"];
    }

    $raw = file_get_contents($jsonPath);
    $d = json_decode($raw, true);
    if ($d === null && json_last_error() !== JSON_ERROR_NONE) {
        return ["stem" => $stem, "ok" => false, "error" => "JSON 파싱 실패: " . json_last_error_msg()];
    }

    $ks = $d["keyword_sentences"] ?? [];
    $fields = [];
    $filled = 0;
    foreach (KEYS as $k) {
        $parts = $ks[$k] ?? [];
        $joined = is_array($parts) ? implode(" ", $parts) : (string)$parts;
        $fields[$k] = $joined;
        if (trim($joined) !== "") $filled++;
    }

    return [
        "stem" => $stem,
        "ok" => true,
        "format" => $d["format"] ?? "",
        "extraction_mode" => $d["extraction_mode"] ?? "",
        "pdf_layout_type" => $d["pdf_layout_type"] ?? "",
        "page_count" => $d["page_count"] ?? 0,
        "ocr_attempted" => (bool)($d["ocr_attempted"] ?? false),
        "ocr_used" => (bool)($d["ocr_used"] ?? false),
        "paddle_attempted" => (bool)($d["paddle_attempted"] ?? false),
        "paddle_used" => (bool)($d["paddle_used"] ?? false),
        "needs_review" => (bool)($d["needs_review"] ?? false),
        "review_reasons" => $d["review_reasons"] ?? [],
        "format_mismatch" => (bool)($d["format_mismatch"] ?? false),
        "field_confidence" => $d["field_confidence"] ?? new \stdClass(),
        "filled_count" => $filled,
        "fields" => $fields,
    ];
}

function build_step1_data(): array {
    [$eligibleNames, $excludedNames] = list_data_files();

    $rows = [];
    foreach ($eligibleNames as $name) {
        $stem = stem_of($name);
        $row = load_one($name, $stem);
        $row["filename"] = nfc($name);
        $row["ext"] = ext_of($name);
        $row["category"] = category_of($name);
        $rows[] = $row;
    }

    $excludedRows = array_map(fn($n) => ["filename" => nfc($n), "ext" => ext_of($n)], $excludedNames);

    $nTotal = count($eligibleNames) + count($excludedNames);
    $nOk = count(array_filter($rows, fn($r) => $r["ok"]));
    $nFail = count($rows) - $nOk;
    $nReview = count(array_filter($rows, fn($r) => $r["ok"] && $r["needs_review"]));
    $nComplete = count(array_filter($rows, fn($r) => $r["ok"] && $r["filled_count"] === 4));

    $catCounts = [];
    foreach ($rows as $r) {
        $catCounts[$r["category"]] = ($catCounts[$r["category"]] ?? 0) + 1;
    }
    arsort($catCounts);
    $categories = array_keys($catCounts);

    // 처리 실패 행에는 추출 메타데이터 키가 없을 수 있다.
    $formats = array_values(array_unique(array_filter(array_map(fn($r) => $r["format"] ?? "", $rows))));
    sort($formats, SORT_STRING);
    $modes = array_values(array_unique(array_filter(array_map(fn($r) => $r["extraction_mode"] ?? "", $rows))));
    sort($modes, SORT_STRING);

    $summary = [
        "total_files_in_folder" => $nTotal,
        "eligible" => count($eligibleNames),
        "excluded" => count($excludedNames),
        "success" => $nOk,
        "failed" => $nFail,
        "needs_review" => $nReview,
        "complete_4_fields" => $nComplete,
    ];

    return [
        "summary" => $summary,
        "rows" => $rows,
        "excluded" => $excludedRows,
        "categories" => $categories,
        "formats" => $formats,
        "modes" => $modes,
        "generated_at" => date("Y-m-d H:i:s"),
        "source_mode" => "live-php",
    ];
}

if (!is_file(TEMPLATE_PATH)) {
    http_response_code(500);
    echo "<h1>템플릿을 찾을 수 없습니다</h1><p>" . htmlspecialchars(TEMPLATE_PATH) . " 파일이 report.php와 같은 디렉토리에 있어야 합니다.</p>";
    exit;
}
if (!is_dir(DATA_DIR)) {
    http_response_code(500);
    echo "<h1>data/ 디렉토리를 찾을 수 없습니다</h1><p>" . htmlspecialchars(DATA_DIR) . "</p>";
    exit;
}

$data = [
    "step1" => build_step1_data(),
    "step2" => \Step2\build_data(),
    "step3" => \Step3\build_data(),
];
$json = json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);

$template = file_get_contents(TEMPLATE_PATH);
$startMarker = "/*__DATA__*/";
$endMarker = "/*__END_DATA__*/";
$startPos = strpos($template, $startMarker);
$endPos = strpos($template, $endMarker);
if ($startPos === false || $endPos === false) {
    http_response_code(500);
    echo "<h1>템플릿에 데이터 마커가 없습니다</h1>";
    exit;
}
$startPos += strlen($startMarker);
$html = substr($template, 0, $startPos) . " " . $json . " " . substr($template, $endPos);

echo $html;
