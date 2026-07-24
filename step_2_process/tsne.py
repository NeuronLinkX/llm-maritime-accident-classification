#!/usr/bin/env python3
"""STEP 2 임베딩 공간을 2D로 투영해 시각화용 좌표를 만든다.

ko-sroberta-sts(채택된 모델)의 818개 768차원 임베딩을 t-SNE로 2D에 투영한다.
목적은 "SBERT가 만든 유사도 공간이 실제로 어떻게 생겼는지"를 사람이 눈으로
볼 수 있게 하는 것 — gui_web STEP2 페이지의 산점도가 이 좌표를 그대로 쓴다.

같은 좌표를 STEP3에서도 재사용한다: STEP3는 원래 카테고리 대신 K-Means가
찾아낸 군집 번호로 같은 점들을 다시 색칠해서, "정답 라벨 없이 임베딩만으로
비슷한 사고를 얼마나 잘 묶어내는지"를 STEP2 그림과 나란히 비교할 수 있게 한다.

실행:
    source sbert_env/bin/activate
    python3 tsne.py
"""
import csv
from pathlib import Path

import numpy as np
from sklearn.manifold import TSNE

ROOT = Path(__file__).resolve().parent
EMB_DIR = ROOT / "embeddings"
BIN_PATH = EMB_DIR / "ko-sroberta-sts.bin"
META_PATH = EMB_DIR / "meta.csv"
OUT_PATH_2D = EMB_DIR / "tsne_2d.csv"
OUT_PATH_3D = EMB_DIR / "tsne_3d.csv"

DIM = 768


def run_tsne(emb, n_components, perplexity):
    tsne = TSNE(n_components=n_components, perplexity=perplexity, metric="euclidean",
                init="pca", random_state=42)
    return tsne.fit_transform(emb)


def main():
    records = []
    with open(META_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            records.append(row)
    n = len(records)
    print(f"레코드 {n}건 로드")

    emb = np.fromfile(BIN_PATH, dtype=np.float32).reshape(n, DIM)

    # 임베딩이 이미 L2 정규화되어 있으므로 cosine 유사도와 euclidean 순위가
    # 사실상 같은 순서를 준다 — t-SNE 기본 metric(euclidean)을 그대로 쓴다.
    perplexity = min(30, max(5, n // 20))

    print(f"t-SNE 2D 실행 (perplexity={perplexity})…")
    coords2d = run_tsne(emb, 2, perplexity)
    with open(OUT_PATH_2D, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "category", "filename", "x", "y"])
        for r, (x, y) in zip(records, coords2d):
            writer.writerow([r["id"], r["category"], r["filename"],
                              round(float(x), 4), round(float(y), 4)])
    print(f"저장: {OUT_PATH_2D}")

    # 3D는 gui_web STEP2 페이지의 회전 가능한 네트워크 시뮬레이션 전용 —
    # 정적 산점도(2D)와는 별개 투영이라 좌표가 다르다.
    print(f"t-SNE 3D 실행 (perplexity={perplexity})…")
    coords3d = run_tsne(emb, 3, perplexity)
    with open(OUT_PATH_3D, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "category", "filename", "x", "y", "z"])
        for r, (x, y, z) in zip(records, coords3d):
            writer.writerow([r["id"], r["category"], r["filename"],
                              round(float(x), 4), round(float(y), 4), round(float(z), 4)])
    print(f"저장: {OUT_PATH_3D}")


if __name__ == "__main__":
    main()
