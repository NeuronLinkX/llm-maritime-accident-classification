#!/usr/bin/env python3
"""STEP 1~3 결과를 하나의 report.html로 정적 빌드한다.

report_template.html은 STEP1(전처리) + STEP2(SBERT 유사도 정량화) +
STEP3(K-Means 군집화)를 한 페이지에 담은 통합 리포트다. 이 스크립트는
STEP1 데이터는 직접 만들고, STEP2/STEP3 데이터는 같은 폴더의
generate_report_step2.py / generate_report_step3.py를 모듈로 불러와
그 build_data()를 그대로 재사용한다 — 세 스크립트 사이에 CSV 파싱 로직을
중복 구현하지 않기 위함이다.

    python3 generate_report.py

실시간으로(재실행 없이) 항상 최신 상태를 보고 싶다면 report.php를 쓴다
(PHP 내장 서버로 `php -S localhost:8000` 후 접속하면 요청마다 원본을 새로
읽는다). 이 스크립트와 report.php는 같은 report_template.html을 공유한다.
"""
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import generate_report_step2 as step2_gen
import generate_report_step3 as step3_gen

# gui_web/은 시각화 전용 폴더로 분리돼 있고, 실제 data/·data_output/은
# 옆 폴더인 step_1_process/에 있다.
STEP1_PROCESS_DIR = ROOT.parent / "step_1_process"
DATA_DIR = STEP1_PROCESS_DIR / "data"
OUT_DIR = STEP1_PROCESS_DIR / "data_output"
TEMPLATE_PATH = ROOT / "report_template.html"
REPORT_PATH = ROOT / "report.html"

KEYS = ["사건 개요", "일시", "장소", "사고 경위"]
ELIGIBLE_EXTS = {".hwp", ".hwpx", ".pdf"}


def category_of(filename):
    # 파일명 정규화(NFC) — 일부 파일명이 NFD로 저장돼 있어 정규화 없이는
    # 겉보기엔 같은 "인명사상" 같은 접두어가 서로 다른 문자열로 취급된다.
    name = unicodedata.normalize("NFC", filename)
    return name.split("_", 1)[0] if "_" in name else name.rsplit(".", 1)[0]


def eligible_inputs():
    return sorted(p for p in DATA_DIR.iterdir() if p.is_file() and p.suffix.lower() in ELIGIBLE_EXTS)


def excluded_inputs():
    return sorted(p for p in DATA_DIR.iterdir() if p.is_file() and p.suffix.lower() not in ELIGIBLE_EXTS)


def load_one(filename, stem):
    # 출력 파일명 규칙이 두 가지 공존한다:
    #   - run_data_gpu.sh(신버전): "foo.hwp.json" — 확장자 포함, hwp/pdf
    #     stem 충돌을 막기 위함.
    #   - run_decoder_data.sh(기존 스크립트): "foo.json" — 확장자 없이 stem만.
    # 신버전 이름이 있으면 그걸 우선 쓰고, 없으면 구버전 이름으로 대체한다.
    json_path = OUT_DIR / f"{filename}.json"
    log_path = OUT_DIR / f"{filename}.stderr.log"
    if not json_path.exists() or json_path.stat().st_size == 0:
        legacy_json_path = OUT_DIR / f"{stem}.json"
        legacy_log_path = OUT_DIR / f"{stem}.stderr.log"
        if legacy_json_path.exists() and legacy_json_path.stat().st_size > 0:
            json_path, log_path = legacy_json_path, legacy_log_path
    if not json_path.exists() or json_path.stat().st_size == 0:
        err = ""
        if log_path.exists():
            err = log_path.read_text(encoding="utf-8", errors="replace").strip()
        return {"stem": stem, "ok": False, "error": err or "출력 파일 없음"}
    try:
        d = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return {"stem": stem, "ok": False, "error": f"JSON 파싱 실패: {exc}"}

    ks = d.get("keyword_sentences", {}) or {}
    fields = {k: " ".join(ks.get(k, []) or []) for k in KEYS}
    filled = sum(1 for k in KEYS if fields[k].strip())

    return {
        "stem": stem,
        "ok": True,
        "format": d.get("format", ""),
        "extraction_mode": d.get("extraction_mode", ""),
        "pdf_layout_type": d.get("pdf_layout_type") or "",
        "page_count": d.get("page_count", 0),
        "ocr_attempted": bool(d.get("ocr_attempted")),
        "ocr_used": bool(d.get("ocr_used")),
        "paddle_attempted": bool(d.get("paddle_attempted")),
        "paddle_used": bool(d.get("paddle_used")),
        "needs_review": bool(d.get("needs_review")),
        "review_reasons": d.get("review_reasons") or [],
        "format_mismatch": bool(d.get("format_mismatch")),
        "field_confidence": d.get("field_confidence", {}),
        "filled_count": filled,
        "fields": fields,
    }


def build_data():
    elig = eligible_inputs()
    excl = excluded_inputs()

    rows = []
    for p in elig:
        stem = p.stem
        row = load_one(p.name, stem)
        row["filename"] = unicodedata.normalize("NFC", p.name)
        row["ext"] = p.suffix.lower().lstrip(".")
        row["category"] = category_of(p.name)
        rows.append(row)

    excluded_rows = [
        {"filename": unicodedata.normalize("NFC", p.name), "ext": p.suffix.lower().lstrip(".")}
        for p in excl
    ]

    n_total_files = len(elig) + len(excl)
    n_ok = sum(1 for r in rows if r["ok"])
    n_fail = sum(1 for r in rows if not r["ok"])
    n_review = sum(1 for r in rows if r["ok"] and r["needs_review"])
    n_complete = sum(1 for r in rows if r["ok"] and r["filled_count"] == 4)

    categories = sorted({r["category"] for r in rows}, key=lambda c: -sum(1 for r in rows if r["category"] == c))
    formats = sorted({r["format"] for r in rows if r.get("format")})
    modes = sorted({r["extraction_mode"] for r in rows if r.get("extraction_mode")})

    summary = {
        "total_files_in_folder": n_total_files,
        "eligible": len(elig),
        "excluded": len(excl),
        "success": n_ok,
        "failed": n_fail,
        "needs_review": n_review,
        "complete_4_fields": n_complete,
    }

    return {
        "summary": summary,
        "rows": rows,
        "excluded": excluded_rows,
        "categories": categories,
        "formats": formats,
        "modes": modes,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_mode": "static-build",
    }


def main():
    if not TEMPLATE_PATH.exists():
        sys.exit(f"템플릿을 찾을 수 없습니다: {TEMPLATE_PATH}")

    step1 = build_data()
    print("STEP1:", json.dumps(step1["summary"], ensure_ascii=False))
    step2 = step2_gen.build_data()
    print("STEP2:", json.dumps(step2["summary"], ensure_ascii=False))
    step3 = step3_gen.build_data()
    print("STEP3:", json.dumps(step3["summary"], ensure_ascii=False))

    data = {"step1": step1, "step2": step2, "step3": step3}

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    marker_re_start = "/*__DATA__*/"
    marker_re_end = "/*__END_DATA__*/"
    start = template.index(marker_re_start) + len(marker_re_start)
    end = template.index(marker_re_end)
    new_html = template[:start] + " " + json.dumps(data, ensure_ascii=False) + " " + template[end:]

    REPORT_PATH.write_text(new_html, encoding="utf-8")
    print(f"saved: {REPORT_PATH} ({REPORT_PATH.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
