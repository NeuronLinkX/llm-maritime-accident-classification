"""법령 코퍼스(persona_model/data/*.md)에 대한 단순 키워드 기반 근사 검색.

주의(한계): 이 모듈은 임베딩 기반 RAG가 아니라 "제N조(...)" 단위로 자른 조문에
대해 원문 부분일치 개수로 점수를 매기는 경량 근사 검색이다. STEP4 지시서는
런타임 법령 검색 컴포넌트의 구체 사양을 지정하지 않았으므로, 페르소나가
요구하는 [RETRIEVED_LEGAL_CONTEXT] 필드를 채우기 위한 최소 구현으로 제공한다.
정교한 검색 성능이 필요하면 STEP2/3에서 이미 쓰인 SBERT 임베딩으로 교체할 것.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

_ARTICLE_RE = re.compile(r"(제\s?\d+조(?:의\s?\d+)?\s*\([^)]*\))")


@dataclass
class LegalChunk:
    doc_id: str
    file_name: str
    article: str
    text: str


def _doc_id_for(index: int) -> str:
    return f"DOC-{index:02d}"


def load_legal_chunks(data_dir: str | Path, logger: logging.Logger) -> list[LegalChunk]:
    data_dir = Path(data_dir)
    md_files = sorted(data_dir.glob("*.md"))
    chunks: list[LegalChunk] = []
    for idx, path in enumerate(md_files, start=1):
        text = path.read_text(encoding="utf-8", errors="ignore")
        matches = list(_ARTICLE_RE.finditer(text))
        doc_id = _doc_id_for(idx)
        if not matches:
            chunks.append(LegalChunk(doc_id=doc_id, file_name=path.name, article="전문", text=text[:2000]))
            continue
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunk_text = text[start:end].strip()
            chunks.append(
                LegalChunk(doc_id=doc_id, file_name=path.name, article=m.group(1), text=chunk_text[:1200])
            )
    logger.info("법령 코퍼스 %d개 문서에서 조문 %d개 청크 로드", len(md_files), len(chunks))
    return chunks


_STOPWORDS = {"있다", "한다", "그", "이", "그리고", "또는", "등", "이를", "위해", "대한", "관한"}


def _keywords(text: str) -> set[str]:
    tokens = re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", text)
    return {t for t in tokens if t not in _STOPWORDS}


def retrieve(fact_text: str, chunks: list[LegalChunk], top_k: int = 5) -> str:
    query_kw = _keywords(fact_text)
    if not query_kw or not chunks:
        return "(관련 법령 검색 결과 없음)"

    scored = []
    for chunk in chunks:
        chunk_kw = _keywords(chunk.text)
        score = len(query_kw & chunk_kw)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]
    if not top:
        return "(관련 법령 검색 결과 없음)"

    lines = []
    for i, (score, chunk) in enumerate(top, start=1):
        excerpt = chunk.text[:120].replace("\n", " ")
        lines.append(
            f"[SOURCE {chunk.doc_id}-C{i:04d}] {chunk.file_name}, {chunk.article} — {excerpt}"
        )
    return "\n".join(lines)
