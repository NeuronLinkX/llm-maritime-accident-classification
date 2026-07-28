#!/usr/bin/env bash

set -u
set -o pipefail

DECODER="./build/body_decoder"
INPUT_DIR="./data"
OUTPUT_DIR="./data_output"
OCR_DPI="${OCR_DPI:-240}"

# PDF/OCR 파이프라인 옵션.
export PDF_RENDER_DPI="${PDF_RENDER_DPI:-300}"
export PADDLE_OCR_MAX_PAGES="${PADDLE_OCR_MAX_PAGES:-2}"
export USE_PADDLE_OCR="${USE_PADDLE_OCR:-auto}"
export KEEP_PDF_DEBUG="${KEEP_PDF_DEBUG:-0}"
export OCRMYPDF_TIMEOUT="${OCRMYPDF_TIMEOUT:-25}"
export RETRY_FAILED_ONLY="${RETRY_FAILED_ONLY:-0}"
export RETRY_REVIEW_ONLY="${RETRY_REVIEW_ONLY:-0}"
export OCR_MODE="${OCR_MODE:-auto}"

mkdir -p "$OUTPUT_DIR"

# ── 색상/스타일 (실제 터미널에 붙어 있을 때만) ──────────────────
# 로그 파일로 리다이렉트되거나 CI 환경이면 이스케이프 코드가 그대로
# 텍스트로 남아 지저분해지므로, IS_TTY일 때만 켠다. NO_COLOR=1로 끌 수도 있다.
IS_TTY=0
[[ -t 1 ]] && IS_TTY=1

if [[ $IS_TTY -eq 1 && -z "${NO_COLOR:-}" ]]; then
    C_RESET=$'\e[0m'; C_BOLD=$'\e[1m'; C_DIM=$'\e[2m'
    C_RED=$'\e[31m'; C_GREEN=$'\e[32m'; C_YELLOW=$'\e[33m'
    C_BLUE=$'\e[34m'; C_MAGENTA=$'\e[35m'; C_CYAN=$'\e[36m'; C_GRAY=$'\e[90m'
else
    C_RESET=""; C_BOLD=""; C_DIM=""
    C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_MAGENTA=""; C_CYAN=""; C_GRAY=""
fi

hr() { printf '%s\n' "${C_DIM}────────────────────────────────────────────────────────────────${C_RESET}"; }

section() {
    echo
    printf '%s\n' "${C_BOLD}${C_CYAN}▌ $1${C_RESET}"
    hr
}

kv() { printf '  %s%-24s%s %s\n' "$C_GRAY" "$1" "$C_RESET" "$2"; }
ok()    { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$1"; }
bad()   { printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$1"; }
warn()  { printf '  %s⚠%s %s\n' "$C_YELLOW" "$C_RESET" "$1"; }
info()  { printf '  %s·%s %s\n' "$C_BLUE" "$C_RESET" "$1"; }

# ── 진행률 바 (터미널 맨 아래 한 줄 고정) ──────────────────────
# 표준출력이 실제 터미널일 때만 사용한다. 로그 파일로 리다이렉트되거나
# CI 환경이면 스크롤 영역 조작이 의미 없거나 화면을 깨뜨릴 수 있으므로 건너뛴다.
BAR_WIDTH=30
TERM_LINES=0
PROGRESS_TOTAL=0
PROGRESS_DONE=0
PROGRESS_START_NS=0

# 초 단위 정수를 "H:MM:SS"로 포맷.
fmt_duration() {
    local total_s=$1
    local h=$(( total_s / 3600 ))
    local m=$(( (total_s % 3600) / 60 ))
    local s=$(( total_s % 60 ))
    printf '%d:%02d:%02d' "$h" "$m" "$s"
}

# 남은 시간 추정에 쓸 시작 시각은 TTY 여부와 무관하게 항상 기록한다 —
# 백그라운드로 돌려서 로그 파일로 리다이렉트된 경우(IS_TTY=0)에도
# progress_log_line()에서 남은시간을 계산해 일반 로그 줄로 남기기 위함.
progress_init() {
    PROGRESS_TOTAL=$1
    PROGRESS_START_NS="$(date +%s%N)"
    [[ $IS_TTY -eq 1 && $PROGRESS_TOTAL -gt 0 ]] || return 0
    TERM_LINES=$(tput lines 2>/dev/null || echo 0)
    [[ $TERM_LINES -gt 2 ]] || { IS_TTY=0; return 0; }
    # 맨 아래 한 줄을 로딩바 전용으로 남기고, 그 위쪽만 스크롤되게 영역을 나눈다.
    printf '\e[1;%dr' "$((TERM_LINES - 1))"
    tput civis 2>/dev/null || true   # 커서 깜빡임 숨김 (선택)
    draw_progress
}

# 지금까지 처리한 파일 수 기준으로 평균 처리 시간을 추정해
# "elapsed_s remaining_s"를 공백으로 구분해 출력한다(호출부에서 read로 나눠 받는다).
# 아직 한 건도 처리하지 못했으면(0건) 나눗셈이 불가능하므로 remaining_s=-1.
estimate_progress_time() {
    local now_ns elapsed_s remaining_s
    now_ns="$(date +%s%N)"
    elapsed_s=$(( (now_ns - PROGRESS_START_NS) / 1000000000 ))
    if [[ $PROGRESS_DONE -gt 0 ]]; then
        remaining_s=$(( elapsed_s * (PROGRESS_TOTAL - PROGRESS_DONE) / PROGRESS_DONE ))
    else
        remaining_s=-1
    fi
    echo "$elapsed_s $remaining_s"
}

# TTY가 아닐 때(백그라운드+로그 리다이렉트) 쓰는 평범한 한 줄짜리 진행 로그.
# tail -f로 지켜봐도 남은시간이 보이도록 파일마다 한 줄씩 남긴다.
progress_log_line() {
    [[ $PROGRESS_TOTAL -gt 0 ]] || return 0
    local elapsed_s remaining_s percent eta_str
    read -r elapsed_s remaining_s < <(estimate_progress_time)
    percent=$(( PROGRESS_DONE * 100 / PROGRESS_TOTAL ))
    if [[ $remaining_s -ge 0 ]]; then
        eta_str="$(fmt_duration "$remaining_s")"
    else
        eta_str="계산 중"
    fi
    echo "[진행] ${PROGRESS_DONE}/${PROGRESS_TOTAL} (${percent}%) · 경과 $(fmt_duration "$elapsed_s") · 남은시간 ${eta_str}"
}

draw_progress() {
    [[ $IS_TTY -eq 1 && $PROGRESS_TOTAL -gt 0 ]] || return 0
    local percent=$(( PROGRESS_DONE * 100 / PROGRESS_TOTAL ))
    local filled=$(( BAR_WIDTH * PROGRESS_DONE / PROGRESS_TOTAL ))
    local empty=$(( BAR_WIDTH - filled ))
    local fill_str rest_str bar_color
    fill_str=$(printf '%*s' "$filled" '')
    fill_str=${fill_str// /█}
    rest_str=$(printf '%*s' "$empty" '')
    rest_str=${rest_str// /░}
    bar_color="$C_CYAN"
    [[ $percent -ge 100 ]] && bar_color="$C_GREEN"

    local elapsed_s remaining_s eta_str
    read -r elapsed_s remaining_s < <(estimate_progress_time)
    if [[ $remaining_s -ge 0 ]]; then
        eta_str="$(fmt_duration "$remaining_s")"
    else
        eta_str="계산 중"
    fi

    tput sc 2>/dev/null || true
    tput cup "$((TERM_LINES - 1))" 0 2>/dev/null || true
    tput el 2>/dev/null || true
    printf '%s진행률%s %s[%s%s%s]%s %s%3d%%%s (%d/%d) %s· 경과 %s · 남은시간 %s%s' \
        "$C_BOLD" "$C_RESET" \
        "$bar_color" "$fill_str" "$C_GRAY" "$rest_str" "$C_RESET" \
        "$C_BOLD" "$percent" "$C_RESET" "$PROGRESS_DONE" "$PROGRESS_TOTAL" \
        "$C_DIM" "$(fmt_duration "$elapsed_s")" "$eta_str" "$C_RESET"
    tput rc 2>/dev/null || true
}

progress_advance() {
    PROGRESS_DONE=$((PROGRESS_DONE + 1))
    if [[ $IS_TTY -eq 1 ]]; then
        draw_progress
    else
        progress_log_line
    fi
}

progress_finish() {
    [[ $IS_TTY -eq 1 && $PROGRESS_TOTAL -gt 0 ]] || return 0
    printf '\e[r'          # 스크롤 영역을 화면 전체로 복원
    tput cnorm 2>/dev/null || true
    tput cup "$((TERM_LINES - 1))" 0 2>/dev/null || true
    tput el 2>/dev/null || true
    echo
}

section "STEP 0 · 빌드 및 Python 문법 검사"

make clean && make
python3 -m py_compile \
    pdf_decoder_py/pdf_to_hwpx_then_decode.py \
    pdf_decoder_py/paddle_ocr_fallback.py

if [[ ! -x "$DECODER" ]]; then
    bad "실행 파일이 없습니다: $DECODER"
    exit 1
fi
ok "빌드 및 문법 검사 통과"

if [[ "$USE_PADDLE_OCR" != "0" ]]; then
    if python3 -c "import paddleocr" >/dev/null 2>&1; then
        ok "PaddleOCR 감지됨 — 1차 로컬 OCR fallback으로 사용합니다."
    else
        warn "PaddleOCR가 설치되어 있지 않습니다. 이 단계는 건너뛰고"
        info "native 추출 → OCRmyPDF fallback 순서로 계속 진행합니다."
        info "설치하려면: pip install \"paddleocr[doc-parser]\""
    fi
fi

RUN_DECODER="$OUTPUT_DIR/.body_decoder.run"
cp "$DECODER" "$RUN_DECODER"
chmod +x "$RUN_DECODER"
# 임시 실행 파일 정리 + (진행률 바를 썼다면) 터미널 스크롤 영역/커서 복원을
# 스크립트가 어떻게 끝나든(정상 종료, Ctrl+C, 에러) 항상 같이 처리한다.
cleanup() {
    rm -f "$RUN_DECODER"
    progress_finish
}
trap cleanup EXIT

FILES=()
while IFS= read -r -d '' input_path; do
    retry_filename="$(basename "$input_path")"
    retry_json="$OUTPUT_DIR/${retry_filename}.json"

    if [[ "$RETRY_FAILED_ONLY" == "1" ]]; then
        # 비어 있거나, JSON이 깨졌거나, 정상 산출물의 핵심 키가 없는 파일만
        # 실패 건으로 간주한다. stderr 로그는 성공 처리 중에도 생길 수 있어
        # 실패 판정 기준으로 사용하지 않는다.
        if [[ -s "$retry_json" ]] \
            && jq -e '(.keyword_sentences | type) == "object"' "$retry_json" >/dev/null 2>&1; then
            continue
        fi
    fi

    if [[ "$RETRY_REVIEW_ONLY" == "1" ]]; then
        # 유효한 기존 결과 중 품질 검사에서 needs_review=true로 판정된
        # 문서만 다시 처리한다.
        if [[ ! -s "$retry_json" ]] \
            || ! jq -e '.needs_review == true' "$retry_json" >/dev/null 2>&1; then
            continue
        fi
    fi
    FILES+=("$input_path")
done < <(
    find "$INPUT_DIR" -maxdepth 1 -type f \
        \( -iname '*.hwp' -o -iname '*.hwpx' -o -iname '*.pdf' \) \
        -print0 | sort -z
)

section "STEP 1 · body_decoder 통합 테스트"
FILE_COUNT="${#FILES[@]}"
kv "입력 파일 수" "$FILE_COUNT"
kv "출력 디렉터리" "$OUTPUT_DIR"
kv "PDF OCR DPI (ocrmypdf)" "$OCR_DPI"
kv "PDF_RENDER_DPI (paddle)" "$PDF_RENDER_DPI"
kv "PADDLE_OCR_MAX_PAGES" "$PADDLE_OCR_MAX_PAGES"
kv "USE_PADDLE_OCR" "$USE_PADDLE_OCR"
kv "RETRY_FAILED_ONLY" "$RETRY_FAILED_ONLY"
kv "RETRY_REVIEW_ONLY" "$RETRY_REVIEW_ONLY"
kv "OCR_MODE" "$OCR_MODE"
kv "KEEP_PDF_DEBUG" "$KEEP_PDF_DEBUG"
kv "OCRMYPDF_TIMEOUT" "$OCRMYPDF_TIMEOUT"
hr

success=0
failure=0
file_idx=0

progress_init "$FILE_COUNT"

if [[ $FILE_COUNT -gt 0 ]]; then
for input_path in "${FILES[@]}"; do
    file_idx=$((file_idx + 1))
    filename="$(basename "$input_path")"

    # 출력 파일명은 확장자를 포함한 원본 파일명 전체를 키로 쓴다(예:
    # "foo.hwp.json", "foo.pdf.json") — 같은 stem을 가진 .hwp/.pdf가 동시에
    # 존재할 때(예: 유정호, 창원호) 한쪽 출력이 다른 쪽을 덮어쓰는 걸 막기 위함.
    json_path="$OUTPUT_DIR/${filename}.json"
    log_path="$OUTPUT_DIR/${filename}.stderr.log"
    tmp_log_path="$OUTPUT_DIR/.${filename}.stderr.tmp"
    rm -f "$log_path" "$tmp_log_path"

    echo
    printf '%s[%d/%d]%s %s%s%s\n' "$C_DIM" "$file_idx" "$FILE_COUNT" "$C_RESET" "$C_BOLD" "$filename" "$C_RESET"

    start_ns="$(date +%s%N)"

    "$RUN_DECODER" "$input_path" auto \
        --ocr="$OCR_MODE" \
        --ocr-lang=kor+eng \
        --ocr-dpi="$OCR_DPI" \
        --ocr-psm=4 \
        --min-native-chars=80 \
        >"$json_path" \
        2>"$tmp_log_path"
    exit_code=$?

    if [[ -s "$tmp_log_path" ]]; then
        filtered_log_path="$OUTPUT_DIR/.${filename}.stderr.filtered"
        # ANSI 색상 코드를 먼저 벗겨낸 뒤, PaddleOCR/파이썬이 정상 실행 중에도
        # 찍는 안내성 로그(모델 로딩, ccache 경고 등)를 걸러낸다.
        sed -r 's/\x1b\[[0-9;]*m//g' "$tmp_log_path" \
            | grep -Ev '^(Empty page!!|Detected [0-9]+ diacritics|Estimating resolution as [0-9]+|\[paddle-ocr\] skipped: .*|.*UserWarning: No ccache found.*|  *warnings\.warn\(warning_message\)|Creating model: .*|Model files already exist\..*|Checking connectivity to the model hosters.*|Using official model .*)$' \
            >"$filtered_log_path" || true
        if [[ -s "$filtered_log_path" ]]; then
            mv "$filtered_log_path" "$log_path"
        else
            rm -f "$filtered_log_path"
        fi
        rm -f "$tmp_log_path"
    else
        rm -f "$tmp_log_path"
    fi

    end_ns="$(date +%s%N)"
    elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))

    if [[ $exit_code -ne 0 ]]; then
        bad "실패 (종료 코드 ${exit_code}, ${elapsed_ms} ms)"

        if [[ -s "$log_path" ]]; then
            printf '  %sSTDERR%s\n' "$C_RED" "$C_RESET"
            sed -n '1,20p' "$log_path" | sed "s/^/    /"
        fi

        failure=$((failure + 1))
        progress_advance
        continue
    fi

    if [[ ! -s "$json_path" ]]; then
        bad "JSON 출력이 비어 있습니다."
        failure=$((failure + 1))
        progress_advance
        continue
    fi

    if command -v jq >/dev/null 2>&1; then
        if ! jq empty "$json_path" 2>/dev/null; then
            bad "유효하지 않은 JSON입니다: $json_path"
            failure=$((failure + 1))
            progress_advance
            continue
        fi

        # native(hwp/hwpx) 경로는 needs_review 개념 자체가 없어서 4개 필드가
        # 통째로 비어도 조용히 통과되던 문제를 여기서 보정한다. PDF 파이프라인이
        # 이미 계산해둔 needs_review=true는 덮어쓰지 않고 OR로만 강화한다.
        python3 ../gui_web/postprocess_review.py "$json_path" || warn "후처리(needs_review 재계산) 실패: $json_path"

        ok "성공 (${elapsed_ms} ms)"
        jq -r '
            "  format               : \(.format)",
            "  extraction_mode      : \(.extraction_mode)",
            "  pdf_layout_type      : \(.pdf_layout_type // "-")",
            "  paragraph_count      : \(.para_text_count)",
            "  record_count         : \(.record_count)",
            "  page_count           : \(.page_count)",
            "  ocr_attempted        : \(.ocr_attempted)",
            "  ocr_used             : \(.ocr_used)",
            "  ocr_error            : \(.ocr_error)",
            "  paddle_attempted     : \(.paddle_attempted // false)",
            "  paddle_used          : \(.paddle_used // false)",
            "  paddle_error         : \(.paddle_error // "")",
            "  needs_review         : \(.needs_review // false)",
            "  format_mismatch      : \(.format_mismatch // false)",
            "  review_reasons       : \(if .review_reasons then (.review_reasons | join(" / ")) else "-" end)",
            "  native_visible_chars : \(.native_visible_chars)",
            "  chosen_visible_chars : \(.chosen_visible_chars)",
            "  native_quality_score : \(.native_quality_score)",
            "  chosen_quality_score : \(.chosen_quality_score)",
            "  normalized_text_len  : \(.normalized_text | length)",
            "  sentence_count       : \(.sentences | length)",
            "  사건 개요            : \(.keyword_sentences["사건 개요"] | join(" | "))",
            "  일시                 : \(.keyword_sentences["일시"] | join(" | "))",
            "  장소                 : \(.keyword_sentences["장소"] | join(" | "))",
            "  사고 경위            : \(.keyword_sentences["사고 경위"] | join(" | "))"
        ' "$json_path" | sed "s/^/${C_GRAY}/;s/\$/${C_RESET}/"
    else
        ok "성공 (${elapsed_ms} ms)"
        info "jq가 없어 JSON 상세 요약을 생략합니다."
        info "설치 명령: sudo apt install -y jq"
    fi

    if [[ -s "$log_path" ]]; then
        warn "stderr 메시지가 있습니다: $log_path"
        sed -n '1,10p' "$log_path" | sed "s/^/    /"
    fi

    printf '  %s→%s %s\n' "$C_BLUE" "$C_RESET" "$json_path"
    success=$((success + 1))
    progress_advance
done
fi

echo
section "전처리 완료"
total_elapsed_s=$(( $(date +%s%N) / 1000000000 - PROGRESS_START_NS / 1000000000 ))
if [[ $failure -eq 0 ]]; then
    ok "성공 ${C_BOLD}${success}${C_RESET}건"
else
    bad "성공 ${success}건 / 실패 ${C_BOLD}${failure}${C_RESET}건"
fi
kv "총 소요 시간" "$(fmt_duration "$total_elapsed_s")"
hr

# ── STEP 2(SBERT 유사도 정량화) 인계용 데이터셋 내보내기 ──────────
# data_output/의 낱개 JSON을 STEP 2가 한 번에 스트리밍해서 읽을 수 있는
# JSONL로 정리해 ../step_2_process/from_step1/에 둔다. 파이썬 없이 jq만 쓴다.
# "사고 경위"를 임베딩 텍스트로 우선 채택한다 — "사건 개요"는 상당수(관련선박
# 표 폴백 케이스)가 서술형이 아니라 표 데이터라서 유사도 비교에 부적합하고,
# "사고 경위"만 코퍼스 전체에서 일관되게 서술형 사고 경과 텍스트다.
if command -v jq >/dev/null 2>&1; then
    section "STEP 2 인계용 데이터셋 내보내기"
    # "input"이라고만 하면 처음 보는 사람은 뭘 넣으라는 건지 헷갈릴 수 있어서,
    # "STEP 1에서 왔다"는 게 이름만 봐도 드러나게 지었다.
    STEP2_INPUT_DIR="../step_2_process/from_step1"
    STEP2_OUT_PATH="$STEP2_INPUT_DIR/step1_dataset.jsonl"
    mkdir -p "$STEP2_INPUT_DIR"
    : > "$STEP2_OUT_PATH"   # 재실행 시 이전 결과가 안 남게 매번 새로 씀

    export_total=0
    export_written=0
    export_skip_bad=0
    export_skip_no_text=0

    for json_path in "$OUTPUT_DIR"/*.json; do
        [[ -e "$json_path" ]] || continue
        export_total=$((export_total + 1))

        if [[ ! -s "$json_path" ]] || ! jq empty "$json_path" >/dev/null 2>&1; then
            export_skip_bad=$((export_skip_bad + 1))
            continue
        fi

        # 출력 파일명이 "foo.json"(구버전)이거나 "foo.hwp.json"(신버전,
        # 확장자 포함)일 수 있다 — 둘 다에서 원본 파일명의 stem만 뽑는다.
        base="$(basename "$json_path" .json)"
        stem="${base%.hwp}"; stem="${stem%.hwpx}"; stem="${stem%.pdf}"
        category="${stem%%_*}"

        line="$(jq -c --arg id "$stem" --arg filename "$base" --arg category "$category" '
            ((.keyword_sentences["사고 경위"] // []) | join(" ")) as $accident
            | ((.keyword_sentences["사건 개요"] // []) | join(" ")) as $overview
            | {
                id: $id,
                filename: $filename,
                category: $category,
                format: (.format // ""),
                extraction_mode: (.extraction_mode // ""),
                needs_review: (.needs_review // false),
                field_confidence: (.field_confidence // {}),
                "사건_개요": $overview,
                "일시": ((.keyword_sentences["일시"] // []) | join(" ")),
                "장소": ((.keyword_sentences["장소"] // []) | join(" ")),
                "사고_경위": $accident,
                embedding_text: (if ($accident | length) > 0 then $accident else $overview end)
              }
            | select(.embedding_text | length > 0)
        ' "$json_path" 2>/dev/null)"

        if [[ -z "$line" ]]; then
            export_skip_no_text=$((export_skip_no_text + 1))
            continue
        fi

        echo "$line" >> "$STEP2_OUT_PATH"
        export_written=$((export_written + 1))
    done

    export_n_review=0
    if [[ -s "$STEP2_OUT_PATH" ]]; then
        export_n_review=$(jq -s '[.[] | select(.needs_review == true)] | length' "$STEP2_OUT_PATH")
    fi

    kv "스캔한 JSON" "$export_total"
    kv "내보낸 레코드" "$export_written (needs_review=true 포함: $export_n_review)"
    kv "제외(빈/손상 JSON)" "$export_skip_bad"
    kv "제외(임베딩 텍스트 없음)" "$export_skip_no_text"
    kv "저장 위치" "$STEP2_OUT_PATH"
    hr
else
    warn "jq가 없어 STEP 2 데이터셋 내보내기를 건너뜁니다. 설치: sudo apt install -y jq"
fi

if [[ $failure -gt 0 ]]; then
    exit 1
fi
