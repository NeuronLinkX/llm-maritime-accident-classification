#!/usr/bin/env python3
"""STEP 3(K-Means 군집화) 결과를 report_step3.html로 정적 빌드한다.

step_3_process/cpp/kmeans.cpp + keywords.py + wordcloud_gen.py + join_tsne.py가
이미 만들어 둔 산출물(step_3_process/output/*.csv, *.png)을 읽어서
report_step3_template.html에 데이터로 꽂아 넣는다.

    python3 generate_report_step3.py
"""
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEP3_DIR = ROOT.parent / "step_3_process"
OUT_DIR = STEP3_DIR / "output"
TEMPLATE_PATH = ROOT / "report_step3_template.html"
REPORT_PATH = ROOT / "report_step3.html"

TOP_KEYWORDS = 10


def load_k_selection():
    path = OUT_DIR / "k_selection.csv"
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({
                "k": int(row["k"]),
                "wcss": float(row["wcss"]),
                "silhouette": float(row["avg_silhouette"]),
                "is_elbow": row["is_elbow"] == "1",
                "is_silhouette_best": row["is_silhouette_best"] == "1",
                "is_chosen": row["is_chosen"] == "1",
            })
    return rows


def load_clusters():
    path = OUT_DIR / "clusters.csv"
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_keywords():
    path = OUT_DIR / "cluster_keywords.csv"
    by_cluster = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["kind"] != "distinctive":
                continue
            by_cluster[int(row["cluster"])].append({
                "keyword": row["keyword"], "freq": int(row["freq"]), "score": float(row["score"]),
            })
    for c in by_cluster:
        by_cluster[c] = by_cluster[c][:TOP_KEYWORDS]
    return by_cluster


def load_scatter():
    path = OUT_DIR / "tsne_clusters.csv"
    points = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            points.append({
                "id": row["id"], "category": row["category"], "filename": row["filename"],
                "x": float(row["x"]), "y": float(row["y"]), "cluster": int(row["cluster"]),
            })
    return points


def build_data():
    k_sel = load_k_selection()
    clusters = load_clusters()
    keywords = load_keywords()
    scatter_points = load_scatter()

    chosen = next((r for r in k_sel if r["is_chosen"]), None)
    elbow = next((r for r in k_sel if r["is_elbow"]), None)
    sil_best = next((r for r in k_sel if r["is_silhouette_best"]), None)

    by_cluster_docs = defaultdict(list)
    for r in clusters:
        by_cluster_docs[int(r["cluster"])].append(r)

    cluster_ids = sorted(by_cluster_docs.keys())
    clusters_out = []
    for c in cluster_ids:
        docs = by_cluster_docs[c]
        cat_counts = defaultdict(int)
        for d in docs:
            cat_counts[d["category"]] += 1
        top_cats = sorted(cat_counts.items(), key=lambda x: -x[1])[:5]
        clusters_out.append({
            "cluster": c,
            "n_docs": len(docs),
            "top_categories": [{"category": k, "count": v} for k, v in top_cats],
            "keywords": keywords.get(c, []),
            "wordcloud": f"assets/wordclouds/wordcloud_cluster_{c}.png",
        })

    summary = {
        "n_docs": len(clusters),
        "chosen_k": chosen["k"] if chosen else None,
        "chosen_silhouette": chosen["silhouette"] if chosen else None,
        "elbow_k": elbow["k"] if elbow else None,
        "silhouette_k": sil_best["k"] if sil_best else None,
        "n_clusters": len(cluster_ids),
    }

    insights = build_insights(clusters_out, summary)

    return {
        "summary": summary,
        "k_selection": k_sel,
        "clusters": clusters_out,
        "scatter": {"points": scatter_points, "n_clusters": len(cluster_ids)},
        "insights": insights,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_mode": "static-build",
    }


def build_insights(clusters_out, summary):
    # 소결(STEP3 페이지 하단)에 쓰일 데이터 — "잘 분리된 군집"과 "모호한 큰
    # 군집"을 특징 키워드의 편중도(score = 군집 내 비중 ÷ 전체 비중)로 자동
    # 판별한다. 하드코딩된 서술이 아니라 실제 산출물에서 계산하므로, 데이터가
    # 바뀌면(예: 재실행 후 군집 구성이 달라지면) 소결도 그대로 따라간다.
    total_docs = sum(c["n_docs"] for c in clusters_out) or 1
    scored = []
    for c in clusters_out:
        top_kw = c["keywords"][0] if c["keywords"] else None
        top3 = [k["keyword"] for k in c["keywords"][:3]]
        scored.append({
            "cluster": c["cluster"], "n_docs": c["n_docs"],
            "top_keyword": ", ".join(top3) if top3 else "-",
            "top_score": top_kw["score"] if top_kw else 0.0,
            "pct": round(c["n_docs"] / total_docs * 100, 1),
        })

    by_score_desc = sorted(scored, key=lambda x: -x["top_score"])
    strong = by_score_desc[:2]

    by_score_asc = sorted(scored, key=lambda x: (x["top_score"], -x["n_docs"]))
    weak = by_score_asc[0] if by_score_asc else None

    return {
        "clusters": scored,
        "strong": strong,
        "weak": weak,
        "chosen_silhouette": round(summary["chosen_silhouette"], 4) if summary.get("chosen_silhouette") is not None else None,
    }


def main():
    if not TEMPLATE_PATH.exists():
        sys.exit(f"템플릿을 찾을 수 없습니다: {TEMPLATE_PATH}")

    data = build_data()
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    start_marker, end_marker = "/*__DATA__*/", "/*__END_DATA__*/"
    start = template.index(start_marker) + len(start_marker)
    end = template.index(end_marker)
    new_html = template[:start] + " " + json.dumps(data, ensure_ascii=False) + " " + template[end:]

    REPORT_PATH.write_text(new_html, encoding="utf-8")
    print(json.dumps(data["summary"], ensure_ascii=False, indent=2))
    print(f"saved: {REPORT_PATH} ({REPORT_PATH.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
