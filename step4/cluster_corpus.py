"""STEP3 산출물(K-means 군집)을 페르소나 체인 입력으로 조립한다.

구 STEP4(gui_web/lib_llm_common.php의 build_cluster_blocks())와 동일한 방식:
- 군집별 문서 id 목록의 앞에서부터 N개(samples_per_cluster)를 뽑아
- 그 문서의 embedding_text(STEP1 산출물)를 sample_maxlen자로 잘라 "대표 문장"으로 쓴다.
- 군집별 빈도/특징 키워드는 cluster_keywords.csv를 그대로 쓴다.
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ClusterUnit:
    cluster_id: str  # "cluster_0" 형태 (doc_id 슬롯 재사용)
    n_docs: int
    freq_keywords: list[str] = field(default_factory=list)
    distinctive_keywords: list[str] = field(default_factory=list)
    sample_sentences: list[str] = field(default_factory=list)


def _load_cluster_ids(clusters_csv: Path) -> dict[str, list[str]]:
    by_cluster: dict[str, list[str]] = {}
    with open(clusters_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_cluster.setdefault(row["cluster"], []).append(row["id"])
    return by_cluster


def _load_keywords(cluster_keywords_csv: Path) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    with open(cluster_keywords_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            c = row["cluster"]
            bucket = out.setdefault(c, {"frequency": [], "distinctive": []})
            kind = row["kind"]
            if kind in bucket:
                bucket[kind].append(row["keyword"])
    return out


def _load_texts_by_id(step1_jsonl: Path, wanted_ids: set[str]) -> dict[str, str]:
    texts: dict[str, str] = {}
    with open(step1_jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            doc_id = d.get("id")
            if doc_id in wanted_ids:
                texts[doc_id] = d.get("embedding_text", "")
    return texts


def _utf8_head(s: str, max_chars: int) -> str:
    return s if len(s) <= max_chars else s[:max_chars] + "…"


def load_clusters(
    clusters_csv: str | Path,
    cluster_keywords_csv: str | Path,
    step1_jsonl: str | Path,
    samples_per_cluster: dict[str, int],
    samples_per_cluster_default: int,
    sample_text_maxlen: int,
    logger: logging.Logger,
) -> list[ClusterUnit]:
    clusters_csv = Path(clusters_csv)
    cluster_keywords_csv = Path(cluster_keywords_csv)
    step1_jsonl = Path(step1_jsonl)
    for p in (clusters_csv, cluster_keywords_csv, step1_jsonl):
        if not p.exists():
            raise FileNotFoundError(f"군집 자산이 없습니다: {p}")

    by_cluster = _load_cluster_ids(clusters_csv)
    keywords_by_cluster = _load_keywords(cluster_keywords_csv)

    sample_ids: set[str] = set()
    picks: dict[str, list[str]] = {}
    for c, ids in by_cluster.items():
        n = samples_per_cluster.get(c, samples_per_cluster_default)
        picked = ids[:n]
        picks[c] = picked
        sample_ids.update(picked)

    texts_by_id = _load_texts_by_id(step1_jsonl, sample_ids)

    units: list[ClusterUnit] = []
    for c in sorted(by_cluster.keys(), key=int):
        kw = keywords_by_cluster.get(c, {"frequency": [], "distinctive": []})
        samples = [
            _utf8_head(texts_by_id[doc_id], sample_text_maxlen)
            for doc_id in picks.get(c, [])
            if texts_by_id.get(doc_id)
        ]
        units.append(
            ClusterUnit(
                cluster_id=f"cluster_{c}",
                n_docs=len(by_cluster[c]),
                freq_keywords=kw["frequency"],
                distinctive_keywords=kw["distinctive"],
                sample_sentences=samples,
            )
        )

    logger.info(
        "군집 로드 완료: %d개 군집 (%s), 대표문장 총 %d개",
        len(units),
        {u.cluster_id: u.n_docs for u in units},
        sum(len(u.sample_sentences) for u in units),
    )
    if not units:
        raise RuntimeError("로드된 군집이 0개입니다.")
    return units
