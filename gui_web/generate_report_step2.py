#!/usr/bin/env python3
"""STEP 2(SBERT 유사도 정량화) 결과를 report_step2.html로 정적 빌드한다.

step_2_process/embed.py + cpp/benchmark.cpp + tsne.py가 이미 만들어 둔
산출물(embeddings/*.csv)을 읽어서 report_step2_template.html에 데이터로
꽂아 넣는다 — STEP1의 generate_report.py와 같은 패턴이다.

    python3 generate_report_step2.py
"""
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEP2_DIR = ROOT.parent / "step_2_process"
EMB_DIR = STEP2_DIR / "embeddings"
TEMPLATE_PATH = ROOT / "report_step2_template.html"
REPORT_PATH = ROOT / "report_step2.html"

CHOSEN_MODEL = "ko-sroberta-sts"
TOP_CATEGORY_COUNT = 7  # 8칸 카테고리 팔레트 중 1칸은 "기타" 묶음에 쓴다


def load_benchmark():
    path = EMB_DIR / "benchmark_results.csv"
    models = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            models.append({
                "name": row["model"],
                "dim": int(row["dim"]),
                "n_records": int(row["n_records"]),
                "intra_n": int(row["intra_n"]),
                "intra_mean": float(row["intra_mean"]),
                "intra_std": float(row["intra_std"]),
                "inter_n": int(row["inter_n"]),
                "inter_mean": float(row["inter_mean"]),
                "inter_std": float(row["inter_std"]),
                "gap": float(row["gap"]),
            })
    models.sort(key=lambda m: -m["gap"])
    return models


def _bucket_categories(rows):
    # 상위 TOP_CATEGORY_COUNT종 외 나머지는 "기타"로 묶는다 — 2D 산점도와
    # 3D 시뮬레이션이 항상 같은 카테고리 분류·색상을 쓰도록 공용화했다.
    counts = Counter(r["category"] for r in rows)
    top_categories = [c for c, _ in counts.most_common(TOP_CATEGORY_COUNT)]
    top_set = set(top_categories)
    other_count = sum(1 for r in rows if r["category"] not in top_set)
    legend = top_categories + [f"기타({len(counts) - TOP_CATEGORY_COUNT}종)"] if other_count else top_categories
    legend_keys = top_categories + (["기타"] if other_count else [])
    return top_set, legend, legend_keys


def load_scatter():
    path = EMB_DIR / "tsne_2d.csv"
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    top_set, legend, legend_keys = _bucket_categories(rows)
    points = []
    for r in rows:
        cat = r["category"] if r["category"] in top_set else "기타"
        points.append({
            "id": r["id"], "category": cat, "filename": r["filename"],
            "x": float(r["x"]), "y": float(r["y"]),
        })

    return {"points": points, "legend": legend, "legend_keys": legend_keys}


def load_scatter_3d():
    path = EMB_DIR / "tsne_3d.csv"
    if not path.exists():
        return {"points": [], "legend": [], "legend_keys": []}
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    top_set, legend, legend_keys = _bucket_categories(rows)
    points = []
    for r in rows:
        cat = r["category"] if r["category"] in top_set else "기타"
        points.append({
            "id": r["id"], "category": cat, "filename": r["filename"],
            "x": float(r["x"]), "y": float(r["y"]), "z": float(r["z"]),
        })

    return {"points": points, "legend": legend, "legend_keys": legend_keys}


def load_pairs(kind):
    path = EMB_DIR / f"{CHOSEN_MODEL}_{kind}_pairs.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[:10]


def load_graph():
    # gui_web의 애니메이션 시뮬레이션이 그릴 k-NN 그래프. 좌표는 scatter의
    # id → {x,y,category}와 JS에서 조인해서 쓴다(여기서는 간선만 넘김).
    path = EMB_DIR / "knn_graph.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [{"a": r["id_a"], "b": r["id_b"], "sim": float(r["similarity"])} for r in rows]


def build_data():
    models = load_benchmark()
    chosen = next((m for m in models if m["name"] == CHOSEN_MODEL), models[0])
    scatter = load_scatter()

    summary = {
        "n_docs": chosen["n_records"],
        "n_models": len(models),
        "n_categories": len(set(p["category"] for p in scatter["points"])),
        "chosen_model": CHOSEN_MODEL,
        "gap": chosen["gap"],
        "intra_mean": chosen["intra_mean"],
        "inter_mean": chosen["inter_mean"],
    }

    return {
        "summary": summary,
        "models": models,
        "scatter": scatter,
        "scatter3d": load_scatter_3d(),
        "top_pairs": load_pairs("top"),
        "bottom_pairs": load_pairs("bottom"),
        "graph": load_graph(),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_mode": "static-build",
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
