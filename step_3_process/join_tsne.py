#!/usr/bin/env python3
"""STEP 2의 t-SNE 2D 좌표에 STEP 3 군집 번호를 붙인다.

같은 좌표(step_2_process/embeddings/tsne_2d.csv)를 재사용해서, gui_web이
"원래 카테고리로 색칠한 그림(STEP2)"과 "K-Means 군집으로 색칠한 같은 그림
(STEP3)"을 나란히 비교할 수 있게 한다 — 좌표가 같아야 같은 점의 위치를
그대로 비교할 수 있다.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TSNE_PATH = ROOT.parent / "step_2_process" / "embeddings" / "tsne_2d.csv"
CLUSTERS_PATH = ROOT / "output" / "clusters.csv"
OUT_PATH = ROOT / "output" / "tsne_clusters.csv"


def main():
    cluster_by_id = {}
    with open(CLUSTERS_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cluster_by_id[row["id"]] = row["cluster"]

    rows_out = []
    with open(TSNE_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cluster = cluster_by_id.get(row["id"])
            if cluster is None:
                continue
            rows_out.append({**row, "cluster": cluster})

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "category", "filename", "x", "y", "cluster"])
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"{len(rows_out)}건 조인 완료 → {OUT_PATH}")


if __name__ == "__main__":
    main()
