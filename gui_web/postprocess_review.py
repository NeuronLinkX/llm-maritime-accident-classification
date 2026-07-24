#!/usr/bin/env python3
"""출력 JSON 한 건에 needs_review/format_mismatch를 일관되게 주입한다.

기존에는 needs_review가 pdf_to_hwpx_then_decode.py(PDF→OCR 파이썬 경로)에서만
계산되고, body_decoder가 직접 만드는 HWP/HWPX native 출력에는 그 개념 자체가
없었다. 그래서 4개 필드가 통째로 비어도(예: 표준 "재결요약서" 서식이 아닌
전체 "재결서"가 섞여 들어온 경우) 조용히 통과됐다.

이 스크립트는 파이프라인 종류와 무관하게 최종 JSON을 다시 열어:
  1. 4개 필드 중 하나라도 비어 있으면        -> needs_review
  2. 장소 좌표의 분/초가 59를 넘으면          -> needs_review (기존 로직과 동일)
  3. field_confidence가 있고 0.6 미만이 있으면 -> needs_review (기존 로직과 동일)
  4. OCRmyPDF 경로인데 한글 문장에 라틴 문자 비율이 비정상적으로 높으면
     (Tesseract가 한글을 영어로 오인식하는 전형적 증상) -> needs_review
  5. 4개 필드가 전부 비어 있는데 원문에 표준 라벨(사건개요/관련선박/일시/
     장소/사고경위)이 하나도 없으면 -> format_mismatch=true로 별도 표시
     (다른 문서 양식이 섞여 들어왔다는 신호 — 재결요약서가 아니라 전체
     재결서 등)

기존 필드(needs_review, field_confidence)가 이미 있으면 존중하되, 이 스크립트가
찾아낸 추가 사유가 있으면 needs_review는 OR로 합치고 review_reasons에 사유를
남긴다. 즉 이미 PDF 파이프라인이 정확하게 계산해둔 needs_review=true는 절대
false로 덮어쓰지 않는다 — 더 엄격해질 수만 있고 느슨해지지는 않는다.
"""
import json
import re
import sys

KEYS = ["사건 개요", "일시", "장소", "사고 경위"]
# "일시"/"장소"는 "사고일시"/"사고장소"(전체 재결서에서 쓰는 라벨) 안에도
# 부분 문자열로 들어있어 판별력이 없다 — "재결요약서" 표준 서식에서만 쓰는
# 라벨만 판별 기준으로 삼는다.
STANDARD_LABELS = ["사건개요", "관련선박", "사고경위"]
LATIN_RATIO_THRESHOLD = 0.12


def validate_place_coords(value):
    m = re.search(
        r"북위\s*\d{1,2}도\s*(\d{1,3})분\s*(\d{1,3})초\s*동경\s*\d{1,3}도\s*(\d{1,3})분\s*(\d{1,3})초",
        value or "",
    )
    if not m:
        return False
    return any(int(part) > 59 for part in m.groups())


def latin_ratio(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    latin = [c for c in letters if c.isascii()]
    return len(latin) / len(letters)


def process(d):
    ks = d.get("keyword_sentences", {}) or {}
    fields = {k: " ".join(ks.get(k) or []) for k in KEYS}
    empty = [k for k in KEYS if not fields[k].strip()]

    conf = d.get("field_confidence") or {}
    low_conf = [k for k, v in conf.items() if isinstance(v, (int, float)) and v < 0.6]

    place_suspect = validate_place_coords(fields.get("장소", ""))

    normalized = (d.get("normalized_text") or "").replace(" ", "").replace("\n", "")
    has_any_label = any(lbl in normalized for lbl in STANDARD_LABELS)
    format_mismatch = len(empty) == len(KEYS) and not has_any_label

    # 필드를 다 합쳐서 비율을 재면 깨끗한 필드(예: 사고경위)가 깨진 필드(예:
    # 사건개요)의 비율을 희석시켜 놓친다 — 필드 단위로 각각 검사한다.
    garble_suspected = False
    garble_fields = []
    if d.get("extraction_mode") == "pdf_ocrmypdf_to_hwpx_cpp":
        for k, v in fields.items():
            if len(v) >= 15 and latin_ratio(v) > LATIN_RATIO_THRESHOLD:
                garble_suspected = True
                garble_fields.append(k)

    reasons = []
    if empty:
        reasons.append(f"필드 누락: {', '.join(empty)}")
    if place_suspect:
        reasons.append("좌표(분/초) 범위 이상 — OCR 오인식 의심")
    if low_conf:
        reasons.append(f"신뢰도 0.6 미만: {', '.join(low_conf)}")
    if garble_suspected:
        reasons.append(f"OCRmyPDF 결과에 라틴 문자 비율 과다({', '.join(garble_fields)}) — 한글 오인식(가비지) 의심")
    if format_mismatch:
        reasons.append("표준 라벨(사건개요/관련선박/일시/장소/사고경위) 없음 — 다른 문서 양식(예: 전체 재결서) 의심")

    computed_needs_review = bool(empty or place_suspect or low_conf or garble_suspected)
    # 기존 값을 존중하되, 이 스크립트가 새로 찾은 사유가 있으면 OR로만 강화한다.
    d["needs_review"] = bool(d.get("needs_review")) or computed_needs_review
    d["format_mismatch"] = format_mismatch
    if reasons:
        d["review_reasons"] = reasons
    return d


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: postprocess_review.py <json_path>")
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    d = process(d)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
