#!/usr/bin/env python3
"""STEP 2 임베딩 공간을 gui_web에서 애니메이션으로 보여주기 위한 k-NN 그래프.

문서마다 코사인 유사도가 가장 높은 이웃 K개를 찾아 간선(edge) 목록으로
저장한다. top/bottom 50쌍(benchmark.cpp)은 "극단값 예시"였다면, 이 그래프는
"임베딩 공간이 실제로 어떻게 연결되어 있는지"를 네트워크로 보여주기 위한
것 — gui_web STEP2 페이지의 애니메이션 시뮬레이션이 이 파일을 그린다.

818 x 818 쌍이지만 numpy 행렬곱 한 번이면 되는 크기라 C++로 옮길 필요는
없다(이미 정규화된 벡터라 코사인 유사도 = 내적).
"""
import csv
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
EMB_DIR = ROOT / "embeddings"
BIN_PATH = EMB_DIR / "ko-sroberta-sts.bin"
META_PATH = EMB_DIR / "meta.csv"
OUT_PATH = EMB_DIR / "knn_graph.csv"

DIM = 768
K = 4  # 문서당 이웃 수


def main():
    ids = []
    with open(META_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ids.append(row["id"])
    n = len(ids)

    emb = np.fromfile(BIN_PATH, dtype=np.float32).reshape(n, DIM)
    sim = emb @ emb.T  # 이미 L2 정규화됨 → 내적 = 코사인 유사도

    edges = set()
    rows = []
    for i in range(n):
        row = sim[i].copy()
        row[i] = -2.0  # 자기 자신 제외
        nn_idx = np.argpartition(-row, K)[:K]
        for j in nn_idx:
            key = (i, int(j)) if i < j else (int(j), i)
            if key in edges:
                continue
            edges.add(key)
            rows.append((ids[key[0]], ids[key[1]], float(sim[key[0], key[1]])))

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id_a", "id_b", "similarity"])
        for a, b, s in rows:
            writer.writerow([a, b, round(s, 4)])

    print(f"노드 {n}개, 간선 {len(rows)}개 → {OUT_PATH}")


if __name__ == "__main__":
    main()
