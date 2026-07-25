#!/usr/bin/env python3
"""STEP 3 부록 — 실제 임베딩 기반 문서 간 코사인 유사도 예시.

818건 전체를 하나의 표로 보여줄 수는 없으니, 군집별로 그 군집을 가장
잘 대표하는 문서 2개씩(군집 중심 벡터에 가장 가까운 문서)을 뽑아 실제
SBERT 임베딩(ko-sroberta-sts, STEP2에서 채택된 모델)으로 문서 간 코사인
유사도 행렬을 계산한다. 임베딩이 이미 L2 정규화돼 있으므로(embed.py 참고)
내적이 곧 코사인 유사도다. gui_web 리포트의 STEP3 마지막에 히트맵으로
보여주는 데 쓰인다 — "같은 군집끼리는 실제로 유사도가 높다"를 눈으로
확인시켜주는 용도.

실행:
    source ../step_2_process/sbert_env/bin/activate
    python3 similarity_sample.py
"""
import csv
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
EMB_BIN = ROOT / "from_step2" / "ko-sroberta-sts.bin"
META_CSV = ROOT / "from_step2" / "meta.csv"
CLUSTERS_CSV = ROOT / "output" / "clusters.csv"
OUT_CSV = ROOT / "output" / "similarity_sample.csv"

DIM = 768
PER_CLUSTER = 2


def main():
    meta = []
    with open(META_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            meta.append(row)

    vecs = np.fromfile(EMB_BIN, dtype=np.float32).reshape(len(meta), DIM)

    cluster_of = {}
    with open(CLUSTERS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cluster_of[row["id"]] = int(row["cluster"])

    by_cluster = {}
    for i, row in enumerate(meta):
        c = cluster_of.get(row["id"])
        if c is None:
            continue
        by_cluster.setdefault(c, []).append(i)

    # 군집별로 중심 벡터(centroid)에 가장 가까운 문서를 "대표 문서"로 뽑는다.
    selected = []  # (index, id, category, filename, cluster)
    for c in sorted(by_cluster.keys()):
        idxs = by_cluster[c]
        sub = vecs[idxs]
        centroid = sub.mean(axis=0)
        centroid = centroid / (np.linalg.norm(centroid) + 1e-9)
        sims_to_centroid = sub @ centroid
        order = np.argsort(-sims_to_centroid)[:PER_CLUSTER]
        for o in order:
            i = idxs[o]
            selected.append((i, meta[i]["id"], meta[i]["category"], meta[i]["filename"], c))

    sel_idx = [s[0] for s in selected]
    sub_vecs = vecs[sel_idx]
    sim_matrix = sub_vecs @ sub_vecs.T  # L2 정규화돼 있어 내적 = 코사인 유사도

    header = ["id", "category", "filename", "cluster"] + [s[1] for s in selected]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row_i, s in enumerate(selected):
            w.writerow([s[1], s[2], s[3], s[4]] + [f"{v:.6f}" for v in sim_matrix[row_i]])

    print(f"saved: {OUT_CSV} ({len(selected)}건, 군집 {len(by_cluster)}개 x 대표문서 {PER_CLUSTER}개)")


if __name__ == "__main__":
    main()
