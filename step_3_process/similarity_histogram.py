#!/usr/bin/env python3
"""STEP 3 부록 — 818건 전체 문서 쌍의 실제 코사인 유사도 분포(히스토그램).

similarity_sample.py는 군집당 2건씩 뽑은 "예시"였지만, 이 스크립트는 818건을
전부 사용해 가능한 모든 문서 쌍(818×817/2 = 334,153쌍)의 코사인 유사도를
실제로 계산한다 — 표본이 아니라 전수 계산. 그 다음 "같은 군집끼리(intra)"와
"다른 군집끼리(inter)" 두 그룹으로 나눠 같은 구간(bin)으로 히스토그램을 만든다.
두 분포가 잘 갈라져 보이면 군집화가 실제 임베딩 공간에서도 의미 있게
분리됐다는 뜻이고, 겹쳐 보이면(이 도메인처럼 문장이 정형화된 경우 흔함) 군집
경계가 무디다는 뜻이다.

임베딩은 이미 L2 정규화돼 있으므로(embed.py 참고) 내적이 곧 코사인 유사도라,
818×818 행렬곱 한 번으로 전체 쌍을 계산한다(818개 문서 규모에서는 수 초 이내).

실행:
    source ../step_2_process/sbert_env/bin/activate
    python3 similarity_histogram.py
"""
import csv
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
EMB_BIN = ROOT / "from_step2" / "ko-sroberta-sts.bin"
META_CSV = ROOT / "from_step2" / "meta.csv"
CLUSTERS_CSV = ROOT / "output" / "clusters.csv"
OUT_CSV = ROOT / "output" / "similarity_histogram.csv"

DIM = 768
N_BINS = 50


def main():
    meta = []
    with open(META_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            meta.append(row)
    n = len(meta)

    vecs = np.fromfile(EMB_BIN, dtype=np.float32).reshape(n, DIM)

    cluster_of = {}
    with open(CLUSTERS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cluster_of[row["id"]] = int(row["cluster"])
    clusters = np.array([cluster_of.get(row["id"], -1) for row in meta])

    # 818x818 코사인 유사도 행렬(내적 = 코사인 유사도, L2 정규화됨) — 상삼각만 쓴다.
    sim = vecs @ vecs.T
    iu, ju = np.triu_indices(n, k=1)
    pair_sims = sim[iu, ju]
    same_cluster = clusters[iu] == clusters[ju]
    valid = (clusters[iu] >= 0) & (clusters[ju] >= 0)

    intra = pair_sims[valid & same_cluster]
    inter = pair_sims[valid & ~same_cluster]

    lo = float(pair_sims[valid].min())
    hi = float(pair_sims[valid].max())
    edges = np.linspace(lo, hi, N_BINS + 1)

    intra_counts, _ = np.histogram(intra, bins=edges)
    inter_counts, _ = np.histogram(inter, bins=edges)

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bin_start", "bin_end", "intra_count", "inter_count"])
        for i in range(N_BINS):
            w.writerow([f"{edges[i]:.6f}", f"{edges[i+1]:.6f}", int(intra_counts[i]), int(inter_counts[i])])

    print(f"saved: {OUT_CSV} (전체 쌍 {len(pair_sims):,}개 — 같은 군집 {len(intra):,} / 다른 군집 {len(inter):,}, {N_BINS} bins)")


if __name__ == "__main__":
    main()
