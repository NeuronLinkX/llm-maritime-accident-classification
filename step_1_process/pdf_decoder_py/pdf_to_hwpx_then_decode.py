#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import paddle_ocr_fallback

KEYS = ["사건 개요", "일시", "장소", "사고 경위"]


def _spaced_pattern(word):
    """Regex matching `word` with arbitrary whitespace allowed between
    every character. OCR sometimes inserts stray spaces mid-word (e.g.
    "판시사항" -> "판 시사 항"), which a plain substring/`\\s*`-between-
    halves pattern misses.
    """
    return r"\s*".join(re.escape(ch) for ch in word)


# 사건개요/사고경위처럼 본문이 긴 필드에서, 다른 절(주문/참고사항/교훈 등)이나
# 재결요약서 정형 문구가 섞여 들어오면 그 앞에서 잘라낸다. C++ 쪽
# section_boundary_markers와 동일한 집합(주문 제외)이다 — C++의 "문장 맨
# 앞에서만 경계 인정" 규칙은 마침표가 없어 통째로 한 "문장"이 되어버리는
# 힌트 값에는 적용되지 않으므로, Python 쪽에서도 같은 표지를 걸러야 한다.
_SECTION_MARKER_WORDS = [
    "판시요지", "판시사항", "원인판단", "관련법규", "재결요지",
    "해양사고관련자", "참고사항", "교훈", "이재결요약서는", "법적효력",
]
HARD_STOP_RE = re.compile("|".join(_spaced_pattern(w) for w in _SECTION_MARKER_WORDS))
# 사건개요 전용: 위 절 표지에 더해 "주 문"도 섞이면 안 된다(사고경위에서는
# "주 문"이 컬럼 병합으로 문장 앞에 붙는 경우가 있어 strip_margin_label로
# 접두어만 제거하고 수집은 계속한다).
OVERVIEW_STOP_RE = re.compile(
    "|".join([_spaced_pattern("주문")] + [_spaced_pattern(w) for w in _SECTION_MARKER_WORDS])
)
# 장소 필드 안에 다른 필드 라벨이 컬럼 병합으로 끼어드는 경우 제거용.
PLACE_SIBLING_LABEL_RE = re.compile(
    "|".join(_spaced_pattern(w) for w in ("사건개요", "사고경위"))
)


def run_text(cmd):
    completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout = completed.stdout.decode("utf-8", "replace")
    stderr = completed.stderr.decode("utf-8", "replace")
    if completed.returncode != 0:
        raise RuntimeError(stderr.strip() or f"command failed: {' '.join(cmd)}")
    return stdout


def pdf_info(path):
    out = run_text(["pdfinfo", str(path)])
    pages = 0
    width = 0.0
    height = 0.0
    for line in out.splitlines():
        if line.startswith("Pages:"):
            pages = int(line.split(":", 1)[1].strip())
        elif line.startswith("Page size:") and not width:
            nums = re.findall(r"[\d.]+", line)
            if len(nums) >= 2:
                width, height = float(nums[0]), float(nums[1])
    return max(1, pages), width, height


def pdftotext_region(path, page, x=None, y=None, width=None, height=None):
    cmd = ["pdftotext", "-layout", "-enc", "UTF-8", "-f", str(page), "-l", str(page)]
    if x is not None:
        cmd += ["-x", str(int(x)), "-y", str(int(y)), "-W", str(int(width)), "-H", str(int(height))]
    cmd += [str(path), "-"]
    return run_text(cmd)


def extract_pdf_native_text(path):
    pages, width, height = pdf_info(path)
    logical_pages = []
    two_up = bool(width and height and width > height * 1.20)
    for page in range(1, pages + 1):
        if two_up:
            split = width / 2.0
            logical_pages.append(pdftotext_region(path, page, 0, 0, split, height))
            logical_pages.append(pdftotext_region(path, page, split, 0, width - split, height))
        else:
            logical_pages.append(pdftotext_region(path, page))
    text = "\n\f\n".join(part.strip("\n") for part in logical_pages if part.strip())
    return text, pages, "landscape_two_up" if two_up else "portrait_or_single"


def collapse_spaces(text):
    return re.sub(r"\s+", " ", text or "").strip()


def visible_len(text):
    return len(re.sub(r"\s+", "", text or ""))


def hangul_count(text):
    return sum(1 for ch in text or "" if "가" <= ch <= "힣")


def paragraph_lines(text):
    lines = []
    for raw in (text or "").replace("\r", "\n").splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


def cleanup_value(text):
    text = collapse_spaces(text)
    text = re.sub(r"^[\s:：ㆍ·|□\[\]\-ㅇ○]+", "", text)
    text = re.sub(r"^[가-하]\.\s*", "", text)
    return text.strip()


# "재결요약서" 표준 서식에서 "사건개요" 라벨이 실제로 가리키는 것은 판시요지
# (가./나. 서술문)가 아니라 "□ 관련선박" 표 블록이다(HWP native 문서들에서
# "사건 개요" 필드가 "관련선박: 선명 용도 총톤수/길이 ..." 형태로 채워지는
# 것과 동일한 관례). 판시요지 라벨이 "판시요지"가 아니라 "판시사항"으로
# 적혀 있거나 가./나. 문장이 "사안"/"사건"이 아닌 다른 말로 끝나는 문서가
# 많아 위 패턴이 실패하는 경우, 이 표 블록을 대신 사용한다.
_OVERVIEW_TABLE_STOP_RE = re.compile(
    r"□?\s*(?:" + "|".join(
        _spaced_pattern(w) for w in ("일시", "장소", "사고경위")
    ) + ")"
)


def extract_related_vessel_overview(text):
    compact = collapse_spaces(text)
    match = re.search(r"관\s*련\s*선\s*박(.*)", compact)
    if not match:
        return ""
    rest = match.group(1)
    stop = _OVERVIEW_TABLE_STOP_RE.search(rest)
    if stop:
        rest = rest[:stop.start()]
    value = cleanup_value(rest)
    if visible_len(value) < 6:
        return ""
    return f"관련선박: {value}"


def extract_overview(text):
    compact = collapse_spaces(text)
    patterns = [
        r"판시요지\s*가\.\s*(.*?)(?:\s*나\.|\s*2\.\s*관련법규|\s*관련법규|\s*원인판단)",
        r"가\.\s*(.*?사안)(?:\s*나\.|\s*2\.|\s*관련법규|\s*원인판단)",
        r"가\.\s*(.*?사건)(?:\s*나\.|\s*2\.|\s*관련법규|\s*원인판단)",
    ]
    for pat in patterns:
        match = re.search(pat, compact)
        if match:
            value = re.sub(r"^판\s*시\s*사\s*항\s*", "", cleanup_value(match.group(1)))
            # 2단 레이아웃 native 텍스트에서는 다른 열의 라벨(판시사항/주문 등)이
            # 문장 한가운데 섬처럼 끼어드는 경우가 흔해서(예: "...어구줄에 판 시
            # 사 항 맞아 부상한 사건임"), 거기서 뒤를 통째로 잘라내면 진짜 결말이
            # 함께 날아간다. 그 노이즈 단어만 지우고 나머지는 유지한다.
            value = cleanup_value(OVERVIEW_STOP_RE.sub(" ", value))
            if value:
                return value
    return extract_related_vessel_overview(compact)


# OCR마다 연/월/일/시/분을 구분하는 구두점이 제각각이다: "2023. 7. 8. 13:30",
# "2023.7.8.13.:30"(마침표 오삽입), "2023년 8월 23일 05시 55"(분 표시자
# 누락) 등. 예전에는 이런 변형이 하나 나올 때마다 정규식을 하나씩 추가하는
# 식이었는데(추가될 변형은 무한하다), 그 대신 "숫자 5개(연/월/일/시/분)가
# 순서대로 나오고 그 사이 구두점은 뭐든 상관없다"는 관대한 패턴 하나로
# 통합한다. 연도가 20xx로 시작하고 월/일/시/분이 각각 유효한 범위 안에 있어야
# 채택하므로, 임의의 숫자 나열(예: 재결번호)을 날짜로 오인할 위험은 낮다.
DATE_FUZZY_RE = re.compile(
    r"(20\d{2})\D{0,2}(\d{1,2})\D{0,2}(\d{1,2})\D{0,4}(\d{1,2})\D{0,2}(\d{1,2})"
)


def extract_date(lines, text):
    candidates = []
    for i, line in enumerate(lines):
        if re.search(r"일\s*[시지]|I\s*시", line):
            candidates.append(" ".join(lines[i:i + 2]))
    candidates.append("\n".join(lines))
    for candidate in candidates:
        m = DATE_FUZZY_RE.search(candidate)
        if m:
            y, mo, d, h, mi = [int(x) for x in m.groups()]
            if 1 <= mo <= 12 and 1 <= d <= 31 and 0 <= h <= 23 and 0 <= mi <= 59:
                if "년" in m.group(0):
                    return f"{y}년 {mo}월 {d}일 {h:02d}시 {mi:02d}분"
                return f"{y}. {mo}. {d}. {h:02d}:{mi:02d}경"
    return ""


def normalize_place(text):
    text = cleanup_value(text)
    text = re.sub(r"\s*[·ㆍ]\s*(?:동경)?\s*", " 동경 ", text)
    text = re.sub(r"사건\s*개요\s*", "", text)
    text = PLACE_SIBLING_LABEL_RE.sub(" ", text)
    text = re.sub(
        r"북위\s*(\d+)\s*(?:도|m)?\s*(\d+)\s*(?:분|[4%])?\s*(\d+)\s*(?:초|%)?\s*(?:동경|-)\s*(\d+)\s*(?:도|m)?\s*(\d+)\s*(?:분|[2%])?\s*(\d+)\s*(?:초|%)?",
        r"북위 \1도 \2분 \3초 동경 \4도 \5분 \6초",
        text,
    )
    text = re.sub(r"\s+\)", ")", text)
    text = cleanup_value(text)
    coord_region = re.search(r"(북위\s*\d{1,2}도\s*\d{1,3}분\s*\d{1,3}초\s*동경\s*\d{1,3}도\s*\d{1,3}분\s*\d{1,3}초(?:\s*\([^\)]*해상\))?)", text)
    if coord_region:
        return cleanup_value(coord_region.group(1))
    return text


def validate_place_coords(value):
    """True if the 북위/동경 minutes or seconds are out of range (OCR garble).

    We only flag this as suspect for review; we never try to force-correct
    the digits, since a wrong "fix" is worse than leaving it for a human.
    """
    m = re.search(
        r"북위\s*\d{1,2}도\s*(\d{1,3})분\s*(\d{1,3})초\s*동경\s*\d{1,3}도\s*(\d{1,3})분\s*(\d{1,3})초",
        value or "",
    )
    if not m:
        return False
    return any(int(part) > 59 for part in m.groups())


def extract_place(lines):
    for i, line in enumerate(lines):
        if not ("장 소" in line or "장소" in line or "북위" in line or re.search(r"4\s*A", line)):
            continue
        chunk = []
        for nxt in lines[i:i + 6]:
            if "사고경위" in nxt or "사고 경위" in nxt:
                break
            chunk.append(nxt)
            if "해상" in nxt:
                break
        text = " ".join(chunk)
        text = re.sub(r".*장\s*[소A]\s*[:：]?", "", text)
        value = normalize_place(text)
        if "북위" in value or "해상" in value:
            return value
    return ""


def strip_margin_label(line):
    line = cleanup_value(line)
    line = re.sub(r"^주\s*문\s+", "", line)
    line = re.sub(r"^사건\s*개요\s+", "", line)
    return cleanup_value(line)


# 사고경위 라벨 바로 다음, 아직 실제 서술 내용을 하나도 못 모은 상태에서
# 마주치는 "절 표지 한 줄짜리" 라인이다. 컬럼 레이아웃 OCR에서는 다음 페이지의
# 좌측 라벨 열(주문/참고사항/교훈)이 실제 사고경위 서술보다 먼저 인식되는
# 경우가 있는데, 이걸 진짜 절 경계로 오인하면 본문을 전혀 못 모으고 끝나버린다.
BARE_LABEL_RE = re.compile(
    "^(" + "|".join(_spaced_pattern(w) for w in _SECTION_MARKER_WORDS) + ")$"
)

# "주문"은 실제 본문 문장 앞머리에 컬럼 병합으로 붙는 경우(예: "주문 쪽 안전
# 핀이 부러지면서...")가 있어 HARD_STOP_RE에는 넣지 않고 strip_margin_label로
# 접두어만 제거한다. 하지만 "주문"이 다른 내용 없이 그 줄 전체인 경우는
# 진짜 절 헤더(판단/처분문 섹션)이므로, 이미 내용을 모은 뒤에 만나면 거기서
# 멈춰야 한다.
STANDALONE_ORDER_RE = re.compile("^" + _spaced_pattern("주문") + "$")

# 표의 "사건개요/일시/장소" 상위 셀 라벨이 OCR에서 "사고경위" 항목 바로
# 옆/아래에 겹쳐 인식되는 경우가 있다(예: "사고경위" 다음 줄에 "사 건개요"만
# 덩그러니 찍힘). 이런 라벨 잔재를 실제 사고경위 서술로 착각해서 맨 앞에
# 붙이면, 그 문자열이 결국 "사고 경위: 사건개요 ..." 형태의 힌트가 되어
# C++ 쪽에서 "사건개요" 라벨로 오인되어 통째로 잘려나간다.
SIBLING_BARE_LABEL_RE = re.compile(
    "^(" + "|".join(_spaced_pattern(w) for w in ("사건개요", "일시", "장소")) + ")$"
)


def extract_accident(lines):
    collecting = False
    acc = []
    seen_content = False

    def note(item):
        nonlocal seen_content
        acc.append(item)
        if len(item) >= 6 and not BARE_LABEL_RE.match(item):
            seen_content = True

    for line in lines:
        compact = collapse_spaces(line)
        if not collecting:
            if "사고경위" not in compact and "사고 경위" not in compact:
                continue
            collecting = True
            rest = re.split(r"사고\s*경위|사고경위", compact, maxsplit=1)[-1]
            rest = strip_margin_label(rest)
            if SIBLING_BARE_LABEL_RE.match(rest):
                continue
            stop = HARD_STOP_RE.search(rest)
            if stop:
                prefix = cleanup_value(rest[:stop.start()])
                if prefix:
                    note(prefix)
                if seen_content:
                    break
                continue
            if rest:
                note(rest)
            continue

        if SIBLING_BARE_LABEL_RE.match(compact):
            continue
        if STANDALONE_ORDER_RE.match(compact):
            if seen_content:
                break
            continue

        stop = HARD_STOP_RE.search(compact)
        if stop and stop.start() == 0:
            if seen_content:
                break
            continue
        if stop:
            item = strip_margin_label(compact[:stop.start()])
            if item:
                note(item)
            if seen_content:
                break
            continue
        item = strip_margin_label(compact)
        if item:
            note(item)
    text = cleanup_value(" | ".join(acc))
    start = re.search(r"([가-힣0-9]+호는\s+20\d{2}|[가-힣0-9]+는\s+20\d{2}|ㅇ\s*[가-힣0-9]+호는)", text)
    if start:
        text = text[start.start():]
    return cleanup_value(text)


def extract_fields(text, lines):
    fields = {
        "사건 개요": extract_overview(text),
        "일시": extract_date(lines, text),
        "장소": extract_place(lines),
        "사고 경위": extract_accident(lines),
    }
    if fields["사건 개요"] and fields["사고 경위"] and \
            collapse_spaces(fields["사건 개요"]) == collapse_spaces(fields["사고 경위"]):
        fields["사건 개요"] = ""
    return fields


def fields_complete(fields):
    return all(collapse_spaces(fields.get(k, "")) for k in KEYS)


def native_quality_ok(native_text, fields):
    return (
        fields_complete(fields)
        and visible_len(native_text) >= 300
        and hangul_count(native_text) >= 50
    )


def merge_fields(base, extra):
    """Fill only the empty slots of `base` from `extra`; never overwrite a
    value that's already present. Returns (merged_dict, keys_that_were_added).
    """
    merged = dict(base)
    added = []
    for key in KEYS:
        if not collapse_spaces(merged.get(key, "")) and collapse_spaces(extra.get(key, "")):
            merged[key] = extra[key]
            added.append(key)
    return merged, added


def run_ocrmypdf_sidecar(input_path, td, lang):
    if not shutil.which("ocrmypdf"):
        raise RuntimeError("ocrmypdf is not installed")
    out_pdf = Path(td) / "ocrmypdf_output.pdf"
    sidecar = Path(td) / "ocrmypdf_sidecar.txt"
    cmd = [
        "ocrmypdf", "--force-ocr", "--deskew",
        "--oversample", "300",
        "--tesseract-pagesegmode", "4",
        "--tesseract-thresholding", "sauvola",
        "--tesseract-timeout", os.environ.get("TESSERACT_TIMEOUT", "20"),
        "--tesseract-non-ocr-timeout", os.environ.get("TESSERACT_NON_OCR_TIMEOUT", "8"),
        "--jobs", "1",
        "-l", lang or "kor+eng",
        "--sidecar", str(sidecar),
        "--output-type", "pdf",
        str(input_path), str(out_pdf),
    ]
    timeout = int(os.environ.get("OCRMYPDF_TIMEOUT", "45"))
    completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "ocrmypdf failed")
    return sidecar.read_text(encoding="utf-8", errors="replace")


def make_section_xml(lines):
    body = [f"<hp:p><hp:run><hp:t>{escape(line)}</hp:t></hp:run></hp:p>" for line in lines]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">\n'
        + "\n".join(body)
        + "\n</hp:sec>\n"
    )


def write_minimal_hwpx(path, lines, preview_text):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("version.xml", '<?xml version="1.0" encoding="UTF-8"?><version/>')
        zf.writestr("Contents/section0.xml", make_section_xml(lines))
        zf.writestr("Preview/PrvText.txt", preview_text)
        zf.writestr("META-INF/manifest.xml", '<?xml version="1.0" encoding="UTF-8"?><manifest/>')


def decode_hwpx_with_cpp(decoder, hwpx_path):
    completed = subprocess.run([decoder, str(hwpx_path), "hwpx"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "C++ HWPX decoder failed")
    return json.loads(completed.stdout)


FIELD_TIER_CONFIDENCE = {"native": 1.0, "paddle": 0.75, "ocrmypdf": 0.65}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path")
    parser.add_argument("--decoder", required=True)
    parser.add_argument("--ocr", default="auto", choices=["auto", "always", "never"])
    parser.add_argument("--ocr-lang", default="kor+eng")
    parser.add_argument("--ocr-dpi", default="300")
    parser.add_argument("--ocr-psm", default="4")
    parser.add_argument("--min-native-chars", default="80")
    args = parser.parse_args()

    input_path = Path(args.input_path)

    # Step A: PDF native text 추출
    native_text, page_count, layout_type = extract_pdf_native_text(input_path)
    native_lines = paragraph_lines(native_text)

    # Step B: native text에서 4개 필드 추출
    fields = extract_fields(native_text, native_lines)
    tier_by_field = {k: ("native" if fields.get(k) else "") for k in KEYS}

    source_text = native_text
    source_lines = native_lines
    extraction_mode = "pdf_native_to_hwpx_cpp"

    paddle_attempted = False
    paddle_used = False
    paddle_error = ""
    ocr_attempted = False
    ocr_used = False
    ocr_error = ""

    debug_dir = None
    if os.environ.get("KEEP_PDF_DEBUG") == "1":
        debug_dir = Path("test_output/debug_pdf") / input_path.stem
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "native.txt").write_text(native_text, encoding="utf-8")

    # Step C: native 결과가 4개 필드를 모두 채우고 품질 검증을 통과하면 그대로 사용.
    native_ok = args.ocr != "always" and native_quality_ok(native_text, fields)

    if args.ocr != "never" and not native_ok:
        # Step D: PaddleOCR fallback (1차 로컬 OCR 엔진)
        use_paddle = os.environ.get("USE_PADDLE_OCR", "auto") != "0"
        if use_paddle:
            paddle_attempted = True
            dpi = int(os.environ.get("PDF_RENDER_DPI", "240"))
            max_pages = int(os.environ.get("PADDLE_OCR_MAX_PAGES", "2"))
            render_dir = str(debug_dir) if debug_dir else None
            paddle_text, paddle_lines, paddle_error = paddle_ocr_fallback.ocr_pdf(
                input_path, dpi=dpi, max_pages=max_pages, render_dir=render_dir,
                two_up=(layout_type == "landscape_two_up"),
            )
            if paddle_error:
                print(f"[paddle-ocr] skipped: {paddle_error}", file=sys.stderr)
            elif paddle_text.strip():
                if debug_dir:
                    (debug_dir / "paddle_ocr.txt").write_text(paddle_text, encoding="utf-8")
                # Step E: PaddleOCR 결과에서 필드 추출
                paddle_fields = extract_fields(paddle_text, paddle_lines)
                merged, added = merge_fields(fields, paddle_fields)
                richer_hangul = hangul_count(paddle_text) > hangul_count(source_text) * 1.2
                # Step F: PaddleOCR가 native보다 더 채우거나 한글 품질이 더 좋으면 채택
                if added or args.ocr == "always" or (not fields_complete(fields) and richer_hangul):
                    fields = merged
                    for k in added:
                        tier_by_field[k] = "paddle"
                    paddle_used = True
                    extraction_mode = "pdf_paddleocr_to_hwpx_cpp"
                    if richer_hangul or len(paddle_lines) > len(source_lines):
                        source_text = paddle_text
                        source_lines = paddle_lines

        # OCRmyPDF: PaddleOCR가 없거나 여전히 부족하면 보조 엔진으로 시도
        if not fields_complete(fields):
            ocr_attempted = True
            try:
                with tempfile.TemporaryDirectory(prefix="pdf_ocrmypdf_") as ocr_td:
                    ocrmypdf_text = run_ocrmypdf_sidecar(input_path, ocr_td, args.ocr_lang)
                ocrmypdf_lines = paragraph_lines(ocrmypdf_text)
                ocrmypdf_fields = extract_fields(ocrmypdf_text, ocrmypdf_lines)
                if debug_dir:
                    (debug_dir / "ocrmypdf.txt").write_text(ocrmypdf_text, encoding="utf-8")
                merged, added = merge_fields(fields, ocrmypdf_fields)
                if added or args.ocr == "always":
                    fields = merged
                    for k in added:
                        tier_by_field[k] = "ocrmypdf"
                    ocr_used = True
                    extraction_mode = "pdf_ocrmypdf_to_hwpx_cpp"
                    if len(ocrmypdf_lines) > len(source_lines):
                        source_text = ocrmypdf_text
                        source_lines = ocrmypdf_lines
            except Exception as exc:
                ocr_error = str(exc)

    place_suspect = validate_place_coords(fields.get("장소", ""))

    # Step H: 최종 텍스트와 필드 힌트를 임시 HWPX로 만들어 C++ HWPX parser에 태운다.
    lines = source_lines
    hint_lines = [f"{key}: {value}" for key, value in fields.items() if value]
    if hint_lines:
        lines = hint_lines + [f"참고사항: PDF converted through {extraction_mode}"] + lines

    with tempfile.TemporaryDirectory(prefix="pdf_to_hwpx_") as td:
        hwpx_path = Path(td) / (input_path.stem + ".hwpx")
        write_minimal_hwpx(hwpx_path, lines, source_text)
        result = decode_hwpx_with_cpp(args.decoder, hwpx_path)

    # field_confidence/needs_review는 python이 추정한 fields가 아니라 C++가
    # 실제로 만들어낸 최종 keyword_sentences를 기준으로 판정한다. C++ 쪽에서
    # 의미 없는 값(페이지 번호 조각 등)을 걸러내고 비워버릴 수 있어서, 두
    # 값이 어긋나면 최종 출력 쪽이 진실이다.
    final_keywords = result.get("keyword_sentences", {}) or {}

    def field_filled(k):
        return bool(final_keywords.get(k))

    confidence = {}
    for k in KEYS:
        if not field_filled(k):
            confidence[k] = 0.0
        else:
            confidence[k] = FIELD_TIER_CONFIDENCE.get(tier_by_field.get(k, ""), 0.5)
    if place_suspect and "장소" in confidence:
        confidence["장소"] = min(confidence["장소"], 0.4)

    needs_review = (
        not all(field_filled(k) for k in KEYS)
        or place_suspect
        or any(v < 0.6 for v in confidence.values())
    )

    result["format"] = "pdf"
    result["extraction_mode"] = extraction_mode
    result["pdf_layout_type"] = layout_type
    result["ocr_attempted"] = ocr_attempted
    result["ocr_used"] = ocr_used
    result["ocr_error"] = ocr_error
    result["paddle_attempted"] = paddle_attempted
    result["paddle_used"] = paddle_used
    result["paddle_error"] = paddle_error
    result["page_count"] = page_count
    result["native_visible_chars"] = visible_len(native_text)
    result["chosen_visible_chars"] = visible_len(source_text)
    result["field_confidence"] = confidence
    result["needs_review"] = needs_review
    if not native_text.strip() and not paddle_used and not ocr_used:
        result["ocr_error"] = result["ocr_error"] or "PDF has no extractable native text"
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
