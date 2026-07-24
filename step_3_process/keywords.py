#!/usr/bin/env python3
"""STEP 3 — 군집별 주요 사고 인자(키워드) 도출.

kmeans(C++)가 만든 output/clusters.csv(문서별 군집 번호)를 입력으로 받아,
각 군집에 속한 문서들의 원문(사고_경위/사건_개요)을 Komoran으로 형태소
분석해 명사를 뽑고, 군집별 빈도 상위 키워드와 "이 군집에서 유독 자주
나오는" 특징 키워드를 함께 산출한다.

형태소 분석은 검증된 한국어 NLP 생태계(KoNLPy/Komoran)가 필요한 영역이라
Python이 담당한다 — Layer.md STEP3 설계의 언어 분담과 동일한 원칙이다.
군집화 자체(K-Means, Elbow/Silhouette)는 cpp/kmeans.cpp가 이미 끝냈다.

실행:
    source ../step_2_process/sbert_env/bin/activate
    python3 keywords.py
"""
import csv
import json
from collections import Counter
from pathlib import Path

from konlpy.tag import Komoran

ROOT = Path(__file__).resolve().parent
CLUSTERS_CSV = ROOT / "output" / "clusters.csv"
DATASET_PATH = ROOT.parent / "step_2_process" / "from_step1" / "step1_dataset.jsonl"
OUT_CSV = ROOT / "output" / "cluster_keywords.csv"

TOP_N = 20
MIN_NOUN_LEN = 2
MIN_FREQ_FOR_DISTINCTIVE = 3

# 사고 재결서 전반에 걸쳐 너무 흔해서 군집을 구분하는 데 도움이 안 되는 명사.
# (원 논문도 "경계/발견/협력" 같은 사고 원인 관련 명사는 그대로 남기되,
#  "사건/사고/때/경우"처럼 문서 형식상 반복되는 상투어만 제거하는 방식을 따른다.)
STOPWORDS = {
    "사건", "사고", "경우", "때문", "이후", "당시", "관련", "이하", "이상",
    "부분", "정도", "필요", "가능", "발생", "확인", "판단", "조치", "상황",
    "이유", "결과", "내용", "기타", "해당",
}


def load_clusters():
    rows = []
    with open(CLUSTERS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def load_texts_by_id():
    texts = {}
    with open(DATASET_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            texts[d["id"]] = d.get("embedding_text", "")
    return texts


def extract_nouns(komoran, text):
    # NNG(일반명사)만 남기고 NNP(고유명사)는 제외한다 — 그렇지 않으면 선박명
    # ("혜성", "포세이돈" 등)이 명사로 잡혀 군집 키워드를 오염시킨다.
    try:
        tagged = komoran.pos(text)
    except Exception:
        return []
    return [w for w, tag in tagged if tag == "NNG" and len(w) >= MIN_NOUN_LEN and w not in STOPWORDS]


def main():
    clusters = load_clusters()
    texts = load_texts_by_id()
    komoran = Komoran()

    cluster_ids = sorted({int(r["cluster"]) for r in clusters})
    cluster_docs = {c: [] for c in cluster_ids}
    for r in clusters:
        cluster_docs[int(r["cluster"])].append(r["id"])

    print(f"군집 {len(cluster_ids)}개, 문서 {len(clusters)}건 로드")

    # 군집별 명사 빈도 + 전체 코퍼스 명사 빈도(특징 키워드 판별용)
    cluster_counters = {}
    corpus_counter = Counter()

    for c in cluster_ids:
        counter = Counter()
        for doc_id in cluster_docs[c]:
            text = texts.get(doc_id, "")
            nouns = extract_nouns(komoran, text)
            counter.update(nouns)
            corpus_counter.update(nouns)
        cluster_counters[c] = counter
        print(f"  군집 {c}: 문서 {len(cluster_docs[c])}건, 고유 명사 {len(counter)}개")

    rows_out = []
    for c in cluster_ids:
        counter = cluster_counters[c]
        n_docs = len(cluster_docs[c])

        # 1) 단순 빈도 상위 키워드
        top_freq = counter.most_common(TOP_N)

        # 2) 특징 키워드: 이 군집 내 비중이 전체 코퍼스 내 비중보다 높은 정도
        #    score = (군집 내 비율) / (전체 코퍼스 내 비율) — TF-IDF의 단순화 버전
        cluster_total = sum(counter.values()) or 1
        corpus_total = sum(corpus_counter.values()) or 1
        distinctive = []
        for word, freq in counter.items():
            if freq < MIN_FREQ_FOR_DISTINCTIVE:
                continue
            cluster_ratio = freq / cluster_total
            corpus_ratio = corpus_counter[word] / corpus_total
            score = cluster_ratio / corpus_ratio if corpus_ratio > 0 else 0.0
            distinctive.append((word, freq, score))
        distinctive.sort(key=lambda x: x[2], reverse=True)
        top_distinctive = distinctive[:TOP_N]

        print(f"\n=== 군집 {c} (n={n_docs}) ===")
        print("  빈도 상위:", ", ".join(f"{w}({f})" for w, f in top_freq[:10]))
        print("  특징 키워드:", ", ".join(f"{w}(x{s:.1f})" for w, f, s in top_distinctive[:10]))

        for rank, (word, freq) in enumerate(top_freq, 1):
            rows_out.append({
                "cluster": c, "n_docs": n_docs, "rank": rank,
                "keyword": word, "freq": freq, "kind": "frequency",
            })
        for rank, (word, freq, score) in enumerate(top_distinctive, 1):
            rows_out.append({
                "cluster": c, "n_docs": n_docs, "rank": rank,
                "keyword": word, "freq": freq, "kind": "distinctive", "score": round(score, 3),
            })

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["cluster", "n_docs", "rank", "keyword", "freq", "kind", "score"])
        writer.writeheader()
        for row in rows_out:
            writer.writerow(row)

    print(f"\n결과 저장: {OUT_CSV}")


if __name__ == "__main__":
    main()
