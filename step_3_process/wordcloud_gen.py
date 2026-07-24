#!/usr/bin/env python3
"""STEP 3 군집별 워드클라우드 이미지를 생성한다.

keywords.py가 만든 output/cluster_keywords.csv(군집별 명사 빈도)를 실제
그림으로 바꾼다. 원 논문(윤보리 외, 2023)도 군집 해석을 워드클라우드로
시각화했다 — Layer.md STEP3 설계의 "워드클라우드/시각화 = Python" 항목을
채우는 스크립트다.

한글 렌더링을 위해 시스템에 이미 설치된 Noto Serif CJK KR을 사용한다
(fc-list로 확인됨: /usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc).

실행:
    source ../step_2_process/sbert_env/bin/activate
    python3 wordcloud_gen.py
"""
import csv
from collections import defaultdict
from pathlib import Path

from wordcloud import WordCloud

ROOT = Path(__file__).resolve().parent
KEYWORDS_CSV = ROOT / "output" / "cluster_keywords.csv"
OUT_DIR = ROOT / "output"
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"

# gui_web(PHP 내장 서버의 document root)에도 사본을 둔다 — php -S는 보안상
# document root 바깥(../step_3_process/...)으로 나가는 정적 파일 요청을
# 404로 막기 때문에, 이미지가 gui_web/ 안에도 있어야 report_step3.php에서
# 깨지지 않고 보인다.
GUI_ASSETS_DIR = ROOT.parent / "gui_web" / "assets" / "wordclouds"


def load_frequencies():
    # kind=="frequency"(단순 빈도)는 "출항/조업/해상"처럼 모든 군집에 고르게
    # 나오는 상투어가 크게 그려져 워드클라우드가 군집을 구분하는 데 도움이
    # 안 된다. kind=="distinctive"(이 군집에서 유독 자주 나오는 정도)의
    # freq를 크기로 써서, 실제로 그 군집을 특징짓는 단어가 크게 보이게 한다.
    freqs = defaultdict(dict)
    with open(KEYWORDS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["kind"] != "distinctive":
                continue
            c = int(row["cluster"])
            freqs[c][row["keyword"]] = int(row["freq"])
    return freqs


def main():
    freqs = load_frequencies()
    print(f"군집 {len(freqs)}개")
    GUI_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    for c, freq_map in sorted(freqs.items()):
        wc = WordCloud(
            font_path=FONT_PATH,
            width=900, height=560,
            background_color="white",
            colormap="viridis",
            prefer_horizontal=0.9,
            max_words=40,
        ).generate_from_frequencies(freq_map)

        out_path = OUT_DIR / f"wordcloud_cluster_{c}.png"
        wc.to_file(str(out_path))
        gui_path = GUI_ASSETS_DIR / f"wordcloud_cluster_{c}.png"
        wc.to_file(str(gui_path))
        print(f"  군집 {c}: {out_path} (+ {gui_path})")

    print("완료")


if __name__ == "__main__":
    main()
