#!/usr/bin/env python3
"""회귀 테스트 — 오늘까지 고친 버그들의 실제 재현 파일을 data/에서 직접
가져와 재처리해서, 그 버그들이 다시 발생하지 않는지 확인한다.

파일을 별도 폴더에 복사해두지 않고 data/ 원본을 그대로 참조한다(중복 저장
없음) — 이 스크립트는 이 파일들을 읽기만 하고 절대 수정/삭제하지 않는다.

exact-text 스냅샷 비교 대신 "이 버그가 재발하면 반드시 깨지는" 구체적인
단언(assert)을 파일마다 정의해두는 방식을 쓴다. OCR 결과는 재실행마다
글자 단위로 미세하게 흔들릴 수 있어서, 완전히 똑같은 텍스트를 요구하면
오탐(false fail)이 잦기 때문이다.

사용법 (프로젝트 어디서든):
    python3 tests/regression_test.py            # 빌드된 ./body_decoder 사용
    python3 tests/regression_test.py --keep     # 처리 결과를 tests/regression_output/에 남겨둠(디버깅용)
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
DATA_DIR = ROOT / "data"
OUT_DIR = TESTS_DIR / "regression_output"
DECODER = ROOT / "build" / "body_decoder"
POSTPROCESS = ROOT.parent / "gui_web" / "postprocess_review.py"


def keyword(d, key):
    return " ".join((d.get("keyword_sentences", {}) or {}).get(key, []) or [])


# 파일마다 "이 조건이 깨지면 오늘 고친 버그가 재발한 것"이라는 구체적 단언.
# check(d) -> True 면 통과, 문자열이면 그게 실패 사유.
CHECKS = {
    "인명사상_어선_603조일호_선원사망사건.hwp": lambda d: (
        d.get("ok", True) and not d.get("needs_review") and
        all(keyword(d, k) for k in ["사건 개요", "일시", "장소", "사고 경위"])
    ) or "정상 hwp native 파일인데 needs_review가 켜지거나 필드가 비었음(오탐 회귀)",

    "좌초_어선_제66함성호_좌초사건.hwpx": lambda d: (
        not d.get("needs_review") and
        all(keyword(d, k) for k in ["사건 개요", "일시", "장소", "사고 경위"])
    ) or "정상 hwpx native 파일인데 needs_review가 켜지거나 필드가 비었음(오탐 회귀)",

    # ②③ native 경로 품질검증 + 이질적 문서(표준 "재결요약서"가 아니라 전체
    # "재결서") 감지 회귀 확인. "사고일시"/"사고장소" 별칭으로 일시/장소는
    # 뽑히고, "사고발생원인" 폴백으로 사고경위도 이제 뽑힌다(의도된 개선).
    # 다만 "관련선박" 표 자체가 없는 문서라 사건개요는 여전히 비어 있어야
    # 하고, 그래서 needs_review는 계속 켜져 있어야 한다.
    "인명사상_어선_제609천금호_선원부상사건.hwp": lambda d: (
        d.get("needs_review") is True and
        not keyword(d, "사건 개요") and bool(keyword(d, "사고 경위"))
    ) or "전체 재결서 케이스인데 needs_review가 안 켜지거나 사고경위 폴백이 안 됨 — ②③ 회귀",

    # "사고 경위" 라벨이 없을 때 "사고발생원인"/"사고발생 원인"에서 대신
    # 뽑는 폴백 회귀 확인. 이 문서는 "나. 사고발생 원인" 다음 문장이
    # "이 좌초사건은 ~~~ 것이다."처럼 결론형으로 곧바로 시작해서, 서술
    # 재시작 탐지(find_narrative_restart_offset)를 그대로 적용하면 첫
    # 문장부터 통째로 끊기던 버그였다.
    "좌초_어선_어선호_좌초사건.hwp": lambda d: (
        bool(keyword(d, "사고 경위"))
    ) or "'사고발생원인' 폴백이 결론형 첫 문장에서 끊김 — check_narrative_restart 회귀",

    # "관련선박" 표 기반 사건개요 추출 회귀 확인.
    "기관손상_카페리선_골드카페리호_기관손상사건.pdf": lambda d: (
        "관련선박" in keyword(d, "사건 개요") and not d.get("needs_review")
    ) or "'관련선박' 표 기반 사건개요 추출이 깨짐 — extract_related_vessel_overview 회귀",

    # 날짜 정규식(마침표 오삽입 "13.:30") 회귀 확인.
    "인명사상_예인선_재원3호_선원부상사건.pdf": lambda d: (
        bool(re.search(r"\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*\d{1,2}:\d{2}", keyword(d, "일시")))
    ) or "'13.:30' 류 마침표 오삽입 날짜 패턴 매칭이 깨짐 — extract_date 회귀",

    # 날짜 정규식(분 단위자 누락 "55경") 회귀 확인. 표기 형식(콜론 "05:55"
    # vs 한글 단위 "05시 55분")은 둘 다 정답이라 형식 무관하게 "분 자리가
    # 55로 채워졌는지"만 확인한다. "55분"/":55" 뒤에 한글이 바로 붙어(예:
    # "55경") \b가 안 먹으므로 경계 없이 그냥 부분일치로 확인한다.
    "전복_어선_해광호_전복사건.pdf": lambda d: (
        bool(re.search(r"55\s*분|:\s*55", keyword(d, "일시")))
    ) or "'55경'처럼 '분' 누락된 날짜 패턴 매칭이 깨짐 — extract_date 회귀",

    "충돌_낚시어선_파트너호ㆍ부선_금신에스비2005호_충돌사건.pdf": lambda d: (
        not d.get("needs_review") and
        all(keyword(d, k) for k in ["사건 개요", "일시", "장소", "사고 경위"])
    ) or "landscape_two_up PDF(PaddleOCR)에서 4필드 완성이 깨짐",

    "기타_어선_동강2호_닻줄_손상사건.pdf": lambda d: (
        not d.get("needs_review") and
        all(keyword(d, k) for k in ["사건 개요", "일시", "장소", "사고 경위"])
    ) or "landscape_two_up PDF(native)에서 4필드 완성이 깨짐",

    # native(hwp/hwpx) 경로에서 발견한 C++ 추출 버그 3종 회귀 확인
    # (body_decoder_postprocess.hpp).

    # "일시" 값이 "2023. 12. 23. 09:19"처럼 끝에 "경" 없이 순수 숫자로만
    # 적히면 한글이 없다는 이유로 is_meaningful_chunk()가 버리던 버그.
    "전복_어선_제105동건호_전복사건.hwpx": lambda d: (
        bool(keyword(d, "일시"))
    ) or "'경' 없는 순수 숫자 일시값이 버려짐 — is_meaningful_chunk 회귀",

    # "장소" 라벨이 "사고장소"(합성어)로 적히면 별칭 목록에 없어서 못 찾던 버그.
    "좌초_준설선_조원G-13호_좌초사건.hwp": lambda d: (
        bool(keyword(d, "장소"))
    ) or "'사고장소' 합성어 라벨을 못 찾음 — keyword_rules 별칭 회귀",

    # "해양사고관련자"가 사고경위 서술문의 주어로 쓰였는데 절 표지로 오인돼
    # 사고경위 수집이 첫 문장부터 통째로 끊기던 버그.
    "인명사상_예인선_효종호_선원사망사건.hwp": lambda d: (
        bool(keyword(d, "사고 경위"))
    ) or "'해양사고관련자'가 문장 주어로 쓰인 경우를 절 표지로 오인 — find_label_boundary_start_offset 회귀",
}


def run_one(input_path):
    out_path = OUT_DIR / f"{input_path.name}.json"
    log_path = OUT_DIR / f"{input_path.name}.stderr.log"
    with open(out_path, "w", encoding="utf-8") as out_f, open(log_path, "w", encoding="utf-8") as log_f:
        completed = subprocess.run(
            [str(DECODER), str(input_path), "auto",
             "--ocr=auto", "--ocr-lang=kor+eng", "--ocr-dpi=240", "--ocr-psm=4", "--min-native-chars=80"],
            stdout=out_f, stderr=log_f,
        )
    if completed.returncode != 0 or out_path.stat().st_size == 0:
        return None, f"디코더 실패(exit={completed.returncode})"
    subprocess.run([sys.executable, str(POSTPROCESS), str(out_path)], check=False)
    try:
        return json.loads(out_path.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, f"JSON 파싱 실패: {exc}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="tests/regression_output/를 지우지 않고 남겨둠")
    args = parser.parse_args()

    if not DECODER.exists():
        sys.exit(f"[ERROR] {DECODER} 가 없습니다. 먼저 make로 빌드하세요.")

    try:
        import paddleocr  # noqa: F401
    except ImportError:
        print("[WARN] 이 셸에 paddleocr가 없습니다 — PaddleOCR 대신 OCRmyPDF로 폴백되어")
        print("[WARN] PDF 관련 검사가 실제와 다르게 실패할 수 있습니다.")
        print("[WARN] GPU venv를 먼저 활성화하세요: source ~/paddle_dev_test/bin/activate")
        print()

    OUT_DIR.mkdir(exist_ok=True)

    missing = [name for name in CHECKS if not (DATA_DIR / name).exists()]
    if missing:
        print("[WARN] data/에서 다음 파일을 찾지 못해 건너뜁니다(원본이 옮겨졌거나 이름이 바뀐 듯):")
        for name in missing:
            print(f"         - {name}")
        print()

    passed, failed = 0, 0
    for name, check in CHECKS.items():
        f = DATA_DIR / name
        if not f.exists():
            continue
        d, err = run_one(f)
        if err:
            print(f"  [FAIL] {name} — {err}")
            failed += 1
            continue
        result = check(d)
        if result is True:
            print(f"  [PASS] {name}")
            passed += 1
        else:
            print(f"  [FAIL] {name} — {result}")
            failed += 1

    print()
    print(f"통과 {passed} / 실패 {failed} (전체 {passed + failed}건)")

    if not args.keep:
        import shutil
        shutil.rmtree(OUT_DIR, ignore_errors=True)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
