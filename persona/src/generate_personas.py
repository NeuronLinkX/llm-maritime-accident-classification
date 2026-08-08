#!/usr/bin/env python3
"""KMST 법령 코퍼스와 prompt.txt로 3-Type Persona 산출물을 생성한다.

기본 실행 위치:
    /home/jiwoo/Desktop/workspace/SBERT/persona/src

설계 원칙:
* 필수 국내 문서 10개를 모두 EOF까지 읽고 검증한다.
* IMO CI Code는 INTERNATIONAL_GUIDANCE로 격리한다.
* 문서를 조문 단위로 분할하고 BM25 검색으로 Qwen3-14B에 근거를 제공한다.
* LLM 출력이 실패해도 법적 안전장치를 포함한 결정론적 페르소나를 보존한다.
* official_ratio와 analytic_contribution_score를 절대 혼합하지 않는다.
* OBD 및 예측모델 구현은 범위에서 제외한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import textwrap
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


TOOL_VERSION = "1.2.0"
DEFAULT_MODEL_NAME = "Qwen3-14B"
DEFAULT_MODEL_CACHE = Path("~/.cache/huggingface/hub/models--Qwen--Qwen3-14B").expanduser()
DEFAULT_OUTPUT = Path(
    "/home/jiwoo/Desktop/workspace/SBERT/llm_based_root_cause_classification_system/persona_model"
)

REQUIRED_DOMESTIC_FILES = (
    "행정규칙_해양안전심판원 정보공개규정.md",
    "행정규칙_해양사고관련자 징계량 결정 지침.md",
    "행정규칙_해양안전심판원 심판관,조사관 등 연수교육 운영 지침.md",
    "행정규칙_해양사고의 조사 및 심판에 관한 법률의 적용대상이 아닌 수상레저기구.md",
    "행정규칙_해양사고의 조사 및 심판에 관한 법률에 따른 과태료의 가중처분에 관한 세부 지침.md",
    "행정규칙_해양사고의 조사 및 심판에 관한 사무 처리 요령.md",
    "행정규칙_해양사고 특별조사부 운영지침.md",
    "법령_해양사고의 조사 및 심판에 관한 법률 시행규칙.md",
    "법령_해양사고의 조사 및 심판에 관한 법률 시행령.md",
    "법령_해양사고의 조사 및 심판에 관한 법률.md",
)
IMO_FILE = "국제기준_IMO 해양사고 조사협약(CI Code) 개요.md"

REQUIRED_OUTPUT_FILES = (
    "README.md",
    "corpus_manifest.json",
    "corpus_validation_report.md",
    "legal_source_hierarchy.md",
    "common_persona_policy.md",
    "persona_01_fact_evidence_analyst.md",
    "persona_01_output_schema.json",
    "persona_02_causation_legal_validator.md",
    "persona_02_output_schema.json",
    "persona_03_contribution_labeling_qa.md",
    "persona_03_output_schema.json",
    "persona_pipeline_master_prompt.md",
    "cause_label_taxonomy.json",
    "contribution_scoring_policy.md",
    "goldset_candidate_policy.md",
    "data_leakage_prevention_policy.md",
    "persona_generation_validation_report.md",
    "token_count_report.json",
)

ARTICLE_RE = re.compile(r"(?m)^\s*(?:#+\s*)?(제\s*\d+\s*조(?:의\s*\d+)?(?:\([^\n)]*\))?)")
DATE_RE = re.compile(r"\[?시행\s+(\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}\.?)\]?")
REVISION_RE = re.compile(
    r"\[?[^\n\]]*(?:법률|대통령령|해양수산부령|훈령|예규)\s*제?[^\n\]]*?[,]?\s*"
    r"(\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}\.?)"
)
TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9_]+")


class BuildError(RuntimeError):
    """사용자 수정이 필요한 생성 실패."""


@dataclass(frozen=True)
class Document:
    document_id: str
    file_name: str
    absolute_path: str
    document_type: str
    hierarchy_rank: int
    title: str
    effective_date: str
    revision_date: str
    file_size: int
    sha256: str
    encoding: str
    read_status: str
    article_count: int
    char_count: int
    line_count: int
    notes: str
    text: str

    def manifest_entry(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("text")
        return data


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    file_name: str
    document_type: str
    hierarchy_rank: int
    article: str
    start_line: int
    end_line: int
    text: str


@dataclass(frozen=True)
class PersonaSpec:
    persona_id: str
    korean_name: str
    english_name: str
    persona_type: str
    output_file: str
    schema_file: str
    mission: str
    core_question: str
    duties: tuple[str, ...]
    prohibitions: tuple[str, ...]
    retrieval_query: str
    handoff: str


PERSONAS = (
    PersonaSpec(
        persona_id="KMST-P01",
        korean_name="사실·증거 구조화 분석관",
        english_name="Maritime Casualty Fact and Evidence Structuring Analyst",
        persona_type="EXTRACTION_AND_STRUCTURING",
        output_file="persona_01_fact_evidence_analyst.md",
        schema_file="persona_01_output_schema.json",
        mission="재결서에서 사실, 진술, 증거, 행위자와 시간순 사건을 출처 위치와 함께 구조화한다.",
        core_question="객관적으로 확인되는 사실은 무엇이며 각 사실을 지지하거나 반박하는 증거는 무엇인가?",
        duties=(
            "사건번호·재결기관·사고유형·발생일시·장소를 추출한다.",
            "사고 전·중·후 타임라인을 구성한다.",
            "인정사실, 당사자 주장, 증인 진술, 객관적 기록, 전문가 의견을 분리한다.",
            "증거 충돌과 누락정보를 표시하고 모든 항목에 원문 위치를 연결한다.",
            "불필요한 개인정보를 비식별화한다.",
        ),
        prohibitions=(
            "최종 사고원인이나 법적 책임을 확정하지 않는다.",
            "official_ratio 또는 analytic_contribution_score를 계산하지 않는다.",
            "재결서에 없는 사실·증거·조문을 생성하지 않는다.",
            "당사자 주장을 인정사실로 바꾸지 않는다.",
        ),
        retrieval_query=(
            "해양사고 조사 사실조사 증거수집 질문 검사 조서 비밀준수 정보공개 "
            "사고접수 조사관 특별조사 재결서 기록"
        ),
        handoff="PERSONA_2에 사실·증거 구조와 누락·충돌 목록을 전달한다.",
    ),
    PersonaSpec(
        persona_id="KMST-P02",
        korean_name="사고원인·법령 정합성 검증관",
        english_name="Maritime Casualty Causation and Legal Consistency Validator",
        persona_type="CAUSATION_AND_LEGAL_VALIDATION",
        output_file="persona_02_causation_legal_validator.md",
        schema_file="persona_02_output_schema.json",
        mission="구조화된 사실·증거로 인과관계를 검증하고 재결 판단과 법령 근거의 정합성을 확인한다.",
        core_question="어떤 요인이 어떤 인과경로로 사고 또는 피해 확대에 기여했고 무엇이 이를 뒷받침하는가?",
        duties=(
            "재결서 명시 원인과 모델이 추가 식별한 원인 후보를 분리한다.",
            "직접원인·기여원인·배경요인·피해 확대요인을 구분한다.",
            "반대 증거, 대체 원인, 반사실적 방지 가능성을 검토한다.",
            "국내 법령과 하위 행정규칙의 위계를 적용한다.",
            "IMO CI Code 내용은 international_guidance로만 분리한다.",
        ),
        prohibitions=(
            "실제 재결문·징계·민사상 과실비율·형사책임을 결정하지 않는다.",
            "모델 추론을 재결기관의 명시적 판단처럼 표현하지 않는다.",
            "IMO 지침을 국내 법령상 의무로 표현하지 않는다.",
            "근거가 불충분한 원인을 확정하지 않는다.",
        ),
        retrieval_query=(
            "해양사고 원인 규명 심판 재결 심리 증거 법령 적용 제척 기피 회피 "
            "징계량 결정 시정 개선 권고 심판관 인과관계"
        ),
        handoff="PERSONA_3에 검증된 원인, 증거 ID, 법령 근거, 불확실성을 전달한다.",
    ),
    PersonaSpec(
        persona_id="KMST-P03",
        korean_name="원인기여도·레이블링 품질관리관",
        english_name="Causal Contribution Scoring and Labeling Quality Controller",
        persona_type="QUANTIFICATION_LABELING_AND_QA",
        output_file="persona_03_contribution_labeling_qa.md",
        schema_file="persona_03_output_schema.json",
        mission="검증된 원인을 연구용 기여도와 예측 학습용 GoldSet 후보 레이블로 변환하고 품질을 점검한다.",
        core_question="검증된 원인을 재현 가능하고 법적 의미와 분리된 연구용 수치·레이블로 어떻게 표현할 것인가?",
        duties=(
            "명시된 공식 원인제공비율만 official_ratio로 원문 그대로 추출한다.",
            "모델 점수는 analytic_contribution_score로 분리한다.",
            "근거 강도·인과적 근접성·반사실성·재결 명시성·자료 일치성을 평가한다.",
            "점수 근거, 범위, 신뢰도와 정량화 불가 사유를 기록한다.",
            "전문가 검증 전 결과를 GOLDSET_CANDIDATE로 표시하고 데이터 누출을 점검한다.",
        ),
        prohibitions=(
            "분석점수를 공식 비율·민사상 과실비율·법적 책임으로 표현하지 않는다.",
            "공식 비율이 없는 사건에 official_ratio를 생성하지 않는다.",
            "근거 부족 사건을 억지로 수치화하지 않는다.",
            "OBD 특징·예측모델·실시간 알람을 설계하지 않는다.",
        ),
        retrieval_query=(
            "해양사고 원인제공비율 원인 기여 징계 기준 과태료 재결 통계 레이블 "
            "공정 종합 판단 증거 원인규명"
        ),
        handoff="전문가 검토 대상으로 GoldSet 후보 레이블과 QA 결과를 출력한다.",
    ),
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_corpus = script_dir.parent / "KMST"
    parser = argparse.ArgumentParser(
        description="prompt.txt와 KMST 법령 코퍼스로 3-Type Persona를 생성합니다."
    )
    parser.add_argument("--prompt", type=Path, default=script_dir / "prompt.txt")
    parser.add_argument("--corpus", type=Path, default=default_corpus)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_CACHE)
    parser.add_argument(
        "--engine",
        choices=("transformers", "dry-run"),
        default="transformers",
        help="dry-run은 GPU 없이 전체 파일 구조와 기본 페르소나를 검증합니다.",
    )
    parser.add_argument(
        "--dtype", choices=("auto", "bfloat16", "float16", "float32"), default="bfloat16"
    )
    parser.add_argument("--load-in-4bit", action="store_true")
    parser.add_argument(
        "--force-gpu",
        action="store_true",
        help="device_map=auto 대신 모델 전체를 cuda:0에 배치합니다.",
    )
    parser.add_argument("--attn-implementation", default=None)
    parser.add_argument("--max-new-tokens", type=int, default=6144)
    parser.add_argument("--max-legal-context-chars", type=int, default=24000)
    parser.add_argument("--top-k", type=int, default=18)
    parser.add_argument("--repair-attempts", type=int, default=1)
    parser.add_argument("--progress-every-tokens", type=int, default=128)
    parser.add_argument("--progress-every-seconds", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def log(message: str) -> None:
    print(f"[KMST] {message}", flush=True)


def read_text(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    if not data:
        raise BuildError(f"빈 파일입니다: {path}")
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise BuildError(f"지원 인코딩으로 읽을 수 없습니다: {path}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify_document(file_name: str) -> tuple[str, int]:
    if file_name == IMO_FILE:
        return "INTERNATIONAL_GUIDANCE", 6
    if file_name.startswith("행정규칙_"):
        return "ADMINISTRATIVE_RULE", 4
    if "시행규칙" in file_name:
        return "ENFORCEMENT_RULE", 3
    if "시행령" in file_name:
        return "ENFORCEMENT_DECREE", 2
    return "LAW", 1


def extract_title(text: str, file_name: str) -> str:
    for line in text.splitlines():
        candidate = re.sub(r"^\s*#+\s*", "", line).strip()
        if candidate and len(candidate) <= 160:
            return candidate
    return Path(file_name).stem.split("_", 1)[-1]


def normalize_date(match: re.Match[str] | None) -> str:
    if not match:
        return ""
    return re.sub(r"\s+", "", match.group(1)).rstrip(".").replace("/", ".").replace("-", ".")


def load_corpus(corpus_dir: Path) -> tuple[list[Document], list[str]]:
    if not corpus_dir.is_dir():
        raise BuildError(f"법령 코퍼스 디렉터리가 없습니다: {corpus_dir}")
    required = list(REQUIRED_DOMESTIC_FILES) + [IMO_FILE]
    missing = [name for name in required if not (corpus_dir / name).is_file()]
    if missing:
        return [], missing

    documents: list[Document] = []
    seen_hashes: dict[str, str] = {}
    for index, file_name in enumerate(required, start=1):
        path = (corpus_dir / file_name).resolve()
        text, encoding = read_text(path)
        digest = sha256_file(path)
        document_type, rank = classify_document(file_name)
        article_count = len(ARTICLE_RE.findall(text))
        notes: list[str] = []
        if article_count == 0:
            notes.append("명시적인 '제N조' 패턴 없음; 문단 단위로 색인")
        if digest in seen_hashes:
            notes.append(f"중복 내용 가능성: {seen_hashes[digest]}")
        seen_hashes[digest] = file_name
        if document_type == "INTERNATIONAL_GUIDANCE":
            notes.append("국제 보조지침 전용; 국내 법령 근거로 사용 금지")
        documents.append(
            Document(
                document_id=f"DOC-{index:02d}",
                file_name=file_name,
                absolute_path=str(path),
                document_type=document_type,
                hierarchy_rank=rank,
                title=extract_title(text, file_name),
                effective_date=normalize_date(DATE_RE.search(text)),
                revision_date=normalize_date(REVISION_RE.search(text)),
                file_size=path.stat().st_size,
                sha256=digest,
                encoding=encoding,
                read_status="COMPLETE",
                article_count=article_count,
                char_count=len(text),
                line_count=text.count("\n") + 1,
                notes="; ".join(notes),
                text=text,
            )
        )
    return documents, []


def split_large_section(
    lines: list[str], start_line: int, max_chars: int = 5000, overlap_lines: int = 3
) -> Iterable[tuple[int, int, str]]:
    cursor = 0
    while cursor < len(lines):
        size = 0
        end = cursor
        while end < len(lines) and (size + len(lines[end]) + 1 <= max_chars or end == cursor):
            size += len(lines[end]) + 1
            end += 1
        yield start_line + cursor, start_line + end - 1, "\n".join(lines[cursor:end]).strip()
        if end >= len(lines):
            break
        cursor = max(cursor + 1, end - overlap_lines)


def chunk_document(document: Document, max_chars: int = 5000) -> list[Chunk]:
    lines = document.text.splitlines()
    article_starts: list[tuple[int, str]] = []
    for line_index, line in enumerate(lines):
        match = ARTICLE_RE.match(line)
        if match:
            article_starts.append((line_index, re.sub(r"\s+", "", match.group(1))))
    if not article_starts:
        article_starts = [(0, "문서전체")]
    elif article_starts[0][0] > 0:
        article_starts.insert(0, (0, "전문·총칙"))

    chunks: list[Chunk] = []
    serial = 1
    for idx, (start, article) in enumerate(article_starts):
        end = article_starts[idx + 1][0] if idx + 1 < len(article_starts) else len(lines)
        section_lines = lines[start:end]
        for part_start, part_end, text in split_large_section(section_lines, start + 1, max_chars):
            if not text:
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"{document.document_id}-C{serial:04d}",
                    document_id=document.document_id,
                    file_name=document.file_name,
                    document_type=document.document_type,
                    hierarchy_rank=document.hierarchy_rank,
                    article=article,
                    start_line=part_start,
                    end_line=part_end,
                    text=text,
                )
            )
            serial += 1
    return chunks


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text) if len(token) > 1]


class BM25Index:
    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b
        self.term_frequencies: list[Counter[str]] = []
        self.doc_lengths: list[int] = []
        document_frequency: Counter[str] = Counter()
        for chunk in self.chunks:
            terms = tokenize(chunk.text)
            frequencies = Counter(terms)
            self.term_frequencies.append(frequencies)
            self.doc_lengths.append(len(terms))
            document_frequency.update(frequencies.keys())
        count = max(1, len(self.chunks))
        self.avgdl = sum(self.doc_lengths) / count
        self.idf = {
            term: math.log(1 + (count - freq + 0.5) / (freq + 0.5))
            for term, freq in document_frequency.items()
        }

    def search(self, query: str, top_k: int) -> list[tuple[float, Chunk]]:
        query_terms = tokenize(query)
        results: list[tuple[float, Chunk]] = []
        for idx, chunk in enumerate(self.chunks):
            score = 0.0
            length = self.doc_lengths[idx]
            frequencies = self.term_frequencies[idx]
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / max(1.0, self.avgdl)
                )
                score += self.idf.get(term, 0.0) * frequency * (self.k1 + 1) / denominator
            if score > 0:
                # 같은 관련도라면 상위 법령을 우선한다.
                score += (7 - chunk.hierarchy_rank) * 1e-4
                results.append((score, chunk))
        return sorted(results, key=lambda item: item[0], reverse=True)[:top_k]


def select_grounding_chunks(
    index: BM25Index,
    query: str,
    all_chunks: Sequence[Chunk],
    top_k: int,
    max_chars: int,
) -> list[Chunk]:
    ranked = index.search(query, top_k=max(top_k * 3, 30))
    selected: list[Chunk] = []
    selected_ids: set[str] = set()
    total = 0

    # 필수 문서 10개와 IMO 문서가 모두 검색 기반 컨텍스트에 최소 한 번 나타나게 한다.
    by_file: dict[str, list[tuple[float, Chunk]]] = defaultdict(list)
    for score, chunk in ranked:
        by_file[chunk.file_name].append((score, chunk))
    for file_name in list(REQUIRED_DOMESTIC_FILES) + [IMO_FILE]:
        candidates = by_file.get(file_name)
        if not candidates:
            candidates = [(0.0, c) for c in all_chunks if c.file_name == file_name]
        if not candidates:
            continue
        chunk = candidates[0][1]
        excerpt_len = min(len(chunk.text), 1800)
        if total + excerpt_len > max_chars:
            break
        selected.append(chunk)
        selected_ids.add(chunk.chunk_id)
        total += excerpt_len

    for _, chunk in ranked:
        if chunk.chunk_id in selected_ids:
            continue
        excerpt_len = min(len(chunk.text), 2800)
        if total + excerpt_len > max_chars:
            break
        selected.append(chunk)
        selected_ids.add(chunk.chunk_id)
        total += excerpt_len
        if len(selected) >= top_k:
            break
    return selected


def render_legal_context(chunks: Sequence[Chunk], max_chars: int) -> str:
    sections: list[str] = []
    used = 0
    for chunk in chunks:
        guidance = (
            "\n[주의: INTERNATIONAL_GUIDANCE — 국내 법령 근거로 사용 금지]"
            if chunk.document_type == "INTERNATIONAL_GUIDANCE"
            else ""
        )
        excerpt_limit = 1600 if chunk.document_type != "INTERNATIONAL_GUIDANCE" else 900
        section = (
            f"[SOURCE {chunk.chunk_id}]\n"
            f"file={chunk.file_name}\n"
            f"type={chunk.document_type}\n"
            f"article={chunk.article}\n"
            f"lines={chunk.start_line}-{chunk.end_line}{guidance}\n"
            f"{chunk.text[:excerpt_limit]}"
        )
        if used + len(section) > max_chars:
            remaining = max_chars - used
            if remaining >= 500:
                sections.append(section[:remaining])
            break
        sections.append(section)
        used += len(section)
    return "\n\n---\n\n".join(sections)


def schema_p01() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.local/kmst/persona-01.schema.json",
        "title": "KMST-P01 사실·증거 구조화 결과",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "case_metadata",
            "actors",
            "vessels",
            "environment",
            "timeline",
            "facts",
            "evidence",
            "evidence_conflicts",
            "missing_information",
            "privacy_actions",
            "handoff_status",
        ],
        "properties": {
            "case_metadata": {"type": "object"},
            "actors": {"type": "array", "items": {"type": "object"}},
            "vessels": {"type": "array", "items": {"type": "object"}},
            "environment": {"type": "object"},
            "timeline": {"type": "array", "items": {"type": "object"}},
            "facts": {"type": "array", "items": {"type": "object"}},
            "evidence": {"type": "array", "items": {"type": "object"}},
            "evidence_conflicts": {"type": "array", "items": {"type": "object"}},
            "missing_information": {"type": "array", "items": {"type": "string"}},
            "privacy_actions": {"type": "array", "items": {"type": "string"}},
            "handoff_status": {
                "enum": ["READY_FOR_PERSONA_2", "RETURN_FOR_CORRECTION"]
            },
        },
    }


def schema_p02() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.local/kmst/persona-02.schema.json",
        "title": "KMST-P02 사고원인·법령 정합성 검증 결과",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "case_id",
            "causal_graph",
            "causes",
            "alternative_causes",
            "legal_conflicts",
            "unresolved_issues",
            "handoff_status",
        ],
        "properties": {
            "case_id": {"type": "string"},
            "causal_graph": {"type": "object"},
            "causes": {"type": "array", "items": {"type": "object"}},
            "alternative_causes": {"type": "array", "items": {"type": "object"}},
            "legal_conflicts": {"type": "array", "items": {"type": "object"}},
            "unresolved_issues": {"type": "array", "items": {"type": "string"}},
            "handoff_status": {
                "enum": ["READY_FOR_PERSONA_3", "RETURN_FOR_CORRECTION"]
            },
        },
    }


def schema_p03() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.local/kmst/persona-03.schema.json",
        "title": "KMST-P03 원인기여도·레이블링 품질관리 결과",
        "type": "object",
        "additionalProperties": False,
        "required": ["case_id", "official_ratio", "cause_labels", "incident_labels", "quality_assurance"],
        "properties": {
            "case_id": {"type": "string"},
            "official_ratio": {
                "type": "object",
                "required": ["present", "subjects"],
                "properties": {
                    "present": {"type": "boolean"},
                    "subjects": {"type": "array", "items": {"type": "object"}},
                    "source_excerpt": {"type": "string"},
                    "source_location": {"type": "string"},
                },
            },
            "cause_labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "cause_id",
                        "label_code",
                        "official_ratio",
                        "analytic_contribution_score",
                        "confidence",
                        "human_review_required",
                        "review_status",
                    ],
                    "properties": {
                        "cause_id": {"type": "string"},
                        "label_code": {"type": "string"},
                        "official_ratio": {"type": ["number", "null"]},
                        "analytic_contribution_score": {
                            "type": ["number", "null"],
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "confidence": {
                            "enum": ["HIGH", "MEDIUM", "LOW", "NOT_SCORABLE"]
                        },
                        "human_review_required": {"const": True},
                        "review_status": {"const": "GOLDSET_CANDIDATE"},
                    },
                },
            },
            "incident_labels": {"type": "object"},
            "quality_assurance": {"type": "object"},
        },
    }


SCHEMAS = {
    "KMST-P01": schema_p01(),
    "KMST-P02": schema_p02(),
    "KMST-P03": schema_p03(),
}


def json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def safe_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(content)
        temp_name = handle.name
    os.replace(temp_name, path)


def prepare_output_dir(output_dir: Path, backup: bool) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = [output_dir / name for name in REQUIRED_OUTPUT_FILES if (output_dir / name).exists()]
    if not existing or not backup:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = output_dir / "_backup" / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in existing:
        shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def resolve_model_path(candidate: Path) -> Path:
    path = candidate.expanduser().resolve()
    if (path / "config.json").is_file():
        return path
    ref_main = path / "refs" / "main"
    if ref_main.is_file():
        revision = ref_main.read_text(encoding="utf-8").strip()
        snapshot = path / "snapshots" / revision
        if (snapshot / "config.json").is_file():
            return snapshot
    snapshots = path / "snapshots"
    if snapshots.is_dir():
        candidates = sorted(
            (item for item in snapshots.iterdir() if (item / "config.json").is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0]
    raise BuildError(
        f"Hugging Face 모델 스냅샷을 찾지 못했습니다: {path}\n"
        "모델 루트 또는 config.json이 있는 snapshots/<revision> 경로를 --model로 지정하세요."
    )


class TransformersEngine:
    def __init__(
        self,
        model_path: Path,
        dtype: str,
        load_in_4bit: bool,
        force_gpu: bool,
        attn_implementation: str | None,
        seed: int,
    ):
        try:
            import torch
            import transformers
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise BuildError("requirements.txt의 패키지를 먼저 설치하세요.") from exc

        self.torch = torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        self.model_path = resolve_model_path(model_path)
        log(f"모델 로드: {self.model_path}")
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_path, local_files_only=True, trust_remote_code=True
        )
        if force_gpu and not torch.cuda.is_available():
            raise BuildError("--force-gpu가 지정됐지만 torch.cuda.is_available()이 False입니다.")
        model_kwargs: dict[str, Any] = {
            "device_map": {"": "cuda:0"} if force_gpu else "auto",
            "local_files_only": True,
            "trust_remote_code": True,
            "low_cpu_mem_usage": True,
        }
        if attn_implementation:
            model_kwargs["attn_implementation"] = attn_implementation
        if load_in_4bit:
            try:
                from transformers import BitsAndBytesConfig
            except ImportError as exc:
                raise BuildError("4-bit 로딩에는 bitsandbytes가 필요합니다.") from exc
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
            )
        else:
            dtype_map = {
                "auto": "auto",
                "bfloat16": torch.bfloat16,
                "float16": torch.float16,
                "float32": torch.float32,
            }
            transformers_major = int(transformers.__version__.split(".", 1)[0])
            dtype_key = "dtype" if transformers_major >= 5 else "torch_dtype"
            model_kwargs[dtype_key] = dtype_map[dtype]
        self.model = AutoModelForCausalLM.from_pretrained(self.model_path, **model_kwargs)
        self.model.eval()
        self.force_gpu = force_gpu
        self.last_generation_stats: dict[str, Any] = {}
        device_map = getattr(self.model, "hf_device_map", {})
        if device_map:
            device_summary = Counter(str(device) for device in device_map.values())
            log(f"모델 장치 배치 요약: {dict(device_summary)}")
            if force_gpu and any(not str(device).startswith("cuda") for device in device_map.values()):
                raise BuildError(f"GPU 강제 배치 실패: {dict(device_summary)}")
        else:
            model_device = str(next(self.model.parameters()).device)
            log(f"모델 기본 장치: {model_device}")
            if force_gpu and not model_device.startswith("cuda"):
                raise BuildError(f"GPU 강제 배치 실패: model_device={model_device}")

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def _chat_prompt(self, messages: list[dict[str, str]]) -> str:
        kwargs = {"tokenize": False, "add_generation_prompt": True}
        try:
            return self.tokenizer.apply_chat_template(messages, enable_thinking=False, **kwargs)
        except TypeError:
            return self.tokenizer.apply_chat_template(messages, **kwargs)

    def generate(
        self,
        messages: list[dict[str, str]],
        max_new_tokens: int,
        task_label: str = "GENERATION",
        progress_every_tokens: int = 128,
        progress_every_seconds: float = 30.0,
    ) -> str:
        from transformers import StoppingCriteria, StoppingCriteriaList

        prompt = self._chat_prompt(messages)
        inputs = self.tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
        input_length = inputs["input_ids"].shape[1]
        configured_context = getattr(self.model.config, "max_position_embeddings", None)
        tokenizer_context = getattr(self.tokenizer, "model_max_length", None)
        plausible_limits = [
            int(value)
            for value in (configured_context, tokenizer_context)
            if isinstance(value, (int, float)) and 2048 <= value <= 1_000_000
        ]
        context_window = min(plausible_limits) if plausible_limits else 40960
        available_generation = context_window - input_length - 64
        if available_generation < 1024:
            raise BuildError(
                f"입력 컨텍스트가 너무 큽니다: input={input_length:,}, context={context_window:,}. "
                "--max-legal-context-chars 값을 줄이세요."
            )
        effective_max_new_tokens = min(max_new_tokens, available_generation)
        if effective_max_new_tokens < max_new_tokens:
            log(
                "컨텍스트 한도에 맞춰 max_new_tokens 자동 조정: "
                f"{max_new_tokens:,} -> {effective_max_new_tokens:,}"
            )
        log(
            f"입력 토큰: task={task_label}, input_tokens={input_length:,}, "
            f"context_window={context_window:,}, generation_cap={effective_max_new_tokens:,}"
        )

        torch_module = self.torch

        class LineProgressCriteria(StoppingCriteria):
            """생성 중 로그 파이프에 보이는 줄 단위 진행률을 출력한다."""

            def __init__(self) -> None:
                self.started_at = time.monotonic()
                self.last_report_at = self.started_at
                self.last_report_tokens = 0

            def __call__(
                self,
                input_ids: Any,
                scores: Any,
                **kwargs: Any,
            ) -> Any:
                generated = max(0, int(input_ids.shape[1]) - input_length)
                now = time.monotonic()
                token_due = generated - self.last_report_tokens >= progress_every_tokens
                time_due = now - self.last_report_at >= progress_every_seconds
                if generated > 0 and (token_due or time_due):
                    elapsed = max(1e-9, now - self.started_at)
                    rate = generated / elapsed
                    remaining = max(0, effective_max_new_tokens - generated)
                    eta = remaining / rate if rate > 0 else 0.0
                    percent = min(99.9, generated / effective_max_new_tokens * 100.0)
                    print(
                        "[KMST][PROGRESS] "
                        f"task={task_label} "
                        f"percent={percent:.1f}% "
                        f"tokens={generated}/{effective_max_new_tokens} "
                        f"rate={rate:.2f}_tokens_per_sec "
                        f"elapsed_seconds={elapsed:.0f} "
                        f"eta_seconds={eta:.0f}",
                        flush=True,
                    )
                    self.last_report_at = now
                    self.last_report_tokens = generated
                return torch_module.zeros(
                    input_ids.shape[0], dtype=torch_module.bool, device=input_ids.device
                )

        if self.torch.cuda.is_available():
            self.torch.cuda.synchronize()
        progress = LineProgressCriteria()
        print(
            "[KMST][PROGRESS] "
            f"task={task_label} percent=0.0% tokens=0/{effective_max_new_tokens} status=started",
            flush=True,
        )
        try:
            device = next(self.model.parameters()).device
            inputs = {key: value.to(device) for key, value in inputs.items()}
        except StopIteration:
            pass
        with self.torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=effective_max_new_tokens,
                do_sample=False,
                use_cache=True,
                pad_token_id=self.tokenizer.eos_token_id,
                stopping_criteria=StoppingCriteriaList([progress]),
            )
        generated_tokens = max(0, int(output.shape[1]) - input_length)
        if self.torch.cuda.is_available():
            self.torch.cuda.synchronize()
        elapsed = max(1e-9, time.monotonic() - progress.started_at)
        rate = generated_tokens / elapsed
        self.last_generation_stats = {
            "task": task_label,
            "input_tokens": input_length,
            "generated_tokens": generated_tokens,
            "token_cap": effective_max_new_tokens,
            "elapsed_seconds": elapsed,
            "tokens_per_second": rate,
        }
        print(
            "[KMST][PROGRESS] "
            f"task={task_label} percent=100.0% "
            f"actual_tokens={generated_tokens} token_cap={effective_max_new_tokens} "
            f"rate={rate:.2f}_tokens_per_sec "
            f"elapsed_seconds={elapsed:.0f} status=completed",
            flush=True,
        )
        return self.tokenizer.decode(output[0, input_length:], skip_special_tokens=True).strip()


def required_persona_sections() -> tuple[str, ...]:
    return (
        "페르소나 ID",
        "역할 정의",
        "법령 기반",
        "입력 계약",
        "수행업무",
        "출력 계약",
        "원문 인용 규칙",
        "오류 및 반려 조건",
        "금지행동",
        "인간 전문가 검토조건",
        "품질검증 체크리스트",
        "실제 실행용 System Prompt",
        "실제 실행용 User Prompt Template",
    )


def validate_persona_markdown(text: str, spec: PersonaSpec) -> list[str]:
    errors: list[str] = []
    if len(text) < 2500:
        errors.append("내용이 지나치게 짧음")
    if spec.persona_id not in text:
        errors.append("persona_id 누락")
    for section in required_persona_sections():
        if section not in text:
            errors.append(f"필수 섹션 누락: {section}")
    if spec.persona_id == "KMST-P03":
        for token in ("official_ratio", "analytic_contribution_score", "GOLDSET_CANDIDATE"):
            if token not in text:
                errors.append(f"핵심 경계 누락: {token}")
    if "OBD" in text and "제외" not in text and "배제" not in text:
        errors.append("OBD가 제외범위임을 명시하지 않음")
    return errors


def render_fallback_persona(spec: PersonaSpec, schema: dict[str, Any], source_chunks: Sequence[Chunk]) -> str:
    duties = "\n".join(f"{idx}. {item}" for idx, item in enumerate(spec.duties, 1))
    prohibitions = "\n".join(f"- {item}" for item in spec.prohibitions)
    references = "\n".join(
        f"- `{chunk.chunk_id}` — {chunk.file_name}, {chunk.article}, lines {chunk.start_line}-{chunk.end_line}"
        for chunk in source_chunks
    )
    schema_string = json.dumps(schema, ensure_ascii=False, indent=2)
    return f"""# {spec.korean_name}

## 1. 페르소나 ID

- ID: `{spec.persona_id}`
- 영문명: {spec.english_name}
- 유형: `{spec.persona_type}`
- 버전: 1.0.0
- 실행모델: {DEFAULT_MODEL_NAME}

## 2. 역할 정의

{spec.mission}

이 페르소나는 실제 해양안전심판원의 법적 권한을 행사하지 않으며 연구용 데이터 구축을 지원한다.

## 3. 법령 기반

국내 법률, 시행령, 시행규칙, 위임 행정규칙 순으로 적용한다. IMO CI Code는 `international_guidance`로만 사용한다. 실제 확인한 `[SOURCE chunk_id]`만 인용하며 조문을 추정하지 않는다.

관련 검색 근거:

{references}

## 4. 핵심 목표

핵심 질문: **{spec.core_question}**

## 5. 입력 계약

입력은 UTF-8 JSON으로 받는다. 사건 ID, 원문 문서명, 원문 또는 이전 페르소나 결과, 원문 위치정보를 포함해야 한다. 필수 입력이 없으면 `RETURN_FOR_CORRECTION`을 출력한다.

## 6. 수행업무

{duties}

## 7. 판단 및 분류 기준

- 사실, 진술, 증거, 재결기관 판단, 모델 추론을 분리한다.
- 모든 판단은 원문 위치 또는 이전 단계 ID로 추적 가능해야 한다.
- 불확실성은 HIGH/MEDIUM/LOW/INSUFFICIENT 또는 NOT_SCORABLE로 명시한다.
- 내부 사고과정 전체가 아니라 검증 가능한 근거 요약만 출력한다.

## 8. 출력 계약

Markdown 설명 없이 JSON 객체 하나만 출력한다. 출력은 `{spec.schema_file}`의 JSON Schema를 준수한다.

```json
{schema_string}
```

## 9. 원문 인용 규칙

- `source_document`, `article`, `source_excerpt`, `source_location`을 가능한 한 함께 제공한다.
- 확인되지 않은 조문번호를 만들지 않는다.
- 사고 당시 법령과 분석 기준일 법령을 혼동하지 않는다.
- IMO 자료는 국내 법령 근거 배열에 넣지 않는다.

## 10. 오류 및 반려 조건

- 필수 입력 누락
- 원문 위치 없는 핵심 판단
- 증거 ID 참조 오류
- 앞 단계 결과와 원문 사이의 중대한 불일치
- 국내 법령과 국제 보조지침의 혼합

오류 시 `handoff_status=RETURN_FOR_CORRECTION`, `return_to`, `error_code`, `reason`, `required_correction`을 출력한다.

## 11. 금지행동

{prohibitions}

공통으로 OBD 연계, Prediction Model 구현, 실시간 알람 생성은 현 단계에서 제외한다.

## 12. 다음 페르소나로의 인계조건

{spec.handoff}

## 13. 인간 전문가 검토조건

- 법령 충돌 또는 적용시점 불명확
- 중대한 증거 충돌
- 공식 원인제공비율의 대상·합계 불명확
- LOW/INSUFFICIENT/NOT_SCORABLE 판단
- GoldSet 후보 승인

## 14. 품질검증 체크리스트

- [ ] 원문 근거와 위치가 연결되었는가?
- [ ] 사실과 모델 추론이 구분되었는가?
- [ ] 국내 법령과 IMO 지침이 구분되었는가?
- [ ] 역할 범위를 벗어난 판단이 없는가?
- [ ] 출력 스키마를 준수하는가?
- [ ] 전문가 검토 필요사항을 표시했는가?

## 15. 실제 실행용 System Prompt

당신은 `{spec.persona_id}` {spec.korean_name}이다. {spec.mission} 실제 해양안전심판원의 권한을 행사하지 않는다. 제공된 원문과 `[SOURCE]` 근거만 사용한다. 사실·진술·증거·재결기관 판단·모델 추론을 분리하고, 근거가 없으면 미확인으로 표시한다. 국내 법령은 법률→시행령→시행규칙→행정규칙 순으로 적용하며 IMO CI Code는 국제 보조지침으로만 분리한다. {" ".join(spec.prohibitions)} 출력은 반드시 지정 JSON Schema에 맞는 JSON 객체 하나만 생성한다.

## 16. 실제 실행용 User Prompt Template

```text
[CASE_METADATA]
case_id:
document_type: FULL_DECISION | DECISION_SUMMARY
tribunal:
decision_date:
incident_date:
analysis_reference_date:
source_file:
page_information_available: true | false

[PREVIOUS_PERSONA_OUTPUT]
{{이전 페르소나 JSON 또는 null}}

[DECISION_TEXT]
{{재결서 또는 재결요약서 원문}}

[RETRIEVED_LEGAL_CONTEXT]
{{SOURCE ID가 포함된 법령 검색 결과}}

[TASK]
{spec.core_question}
```
"""


def immutable_policy_appendix(spec: PersonaSpec) -> str:
    """LLM이 수정할 수 없는 최소 안전정책을 최종 문서에 부착한다."""
    prohibitions = "\n".join(f"- {item}" for item in spec.prohibitions)
    return f"""## 고정 안전정책 부록

이 부록은 생성모델의 서술보다 우선한다.

- 출력 스키마: `{spec.schema_file}`
- 국내 규범 위계: 법률 → 시행령 → 시행규칙 → 위임 행정규칙 → 기타 행정규칙
- IMO CI Code: `international_guidance`로만 사용
- 확인되지 않은 조문·사실·증거 생성 금지
- 실제 재결·징계·민사상 과실·형사책임 결정 금지
- OBD 연계·Prediction Model·실시간 알람 구현 제외
- 전문가 검증 전 레이블 상태: `GOLDSET_CANDIDATE`

{prohibitions}
"""


def build_generation_messages(
    master_prompt: str,
    spec: PersonaSpec,
    schema: dict[str, Any],
    legal_context: str,
) -> list[dict[str, str]]:
    system = f"""당신은 대한민국 해양안전심판 법령과 LLM 프롬프트 엔지니어링 전문가다.
실제 사용 모델은 {DEFAULT_MODEL_NAME}이다. 첨부 마스터 프롬프트에 Qwen2.5-14B 표기가 남아 있어도 실제 구성은 {DEFAULT_MODEL_NAME} 기준으로 작성한다.
오직 제공된 법령 SOURCE만 근거로 사용한다. 조문을 발명하지 않는다. IMO CI Code는 international_guidance로만 다룬다.
출력은 {spec.korean_name}의 완성된 Markdown 파일 하나다. 서문, 사과, 코드펜스 바깥 설명을 붙이지 않는다.
"""
    user = f"""아래 마스터 프롬프트, 고정 역할 명세, JSON Schema, 검색된 법령 근거를 사용하여 페르소나 파일을 작성하라.

[고정 역할]
persona_id: {spec.persona_id}
name: {spec.korean_name}
english_name: {spec.english_name}
persona_type: {spec.persona_type}
mission: {spec.mission}
core_question: {spec.core_question}
duties: {json.dumps(spec.duties, ensure_ascii=False)}
prohibitions: {json.dumps(spec.prohibitions, ensure_ascii=False)}
handoff: {spec.handoff}

[필수 작성 섹션]
{chr(10).join(f'{i}. {name}' for i, name in enumerate(required_persona_sections(), 1))}

[고정 JSON Schema]
{json.dumps(schema, ensure_ascii=False, indent=2)}

[검색된 법령 근거]
{legal_context}

[사용자 마스터 프롬프트]
{master_prompt}

[절대 규칙]
1. 고정 역할·금지행동·JSON Schema는 변경하지 않는다.
2. official_ratio와 analytic_contribution_score를 혼합하지 않는다.
3. 전문가 검증 전 레이블은 GOLDSET_CANDIDATE다.
4. OBD, Prediction Model, 실시간 알람 구현은 제외한다.
5. 숨은 사고과정 대신 검증 가능한 근거와 판단기준을 작성한다.
6. 실제 실행용 System Prompt와 User Prompt Template을 반드시 포함한다.
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def repair_persona(
    engine: TransformersEngine,
    draft: str,
    errors: Sequence[str],
    spec: PersonaSpec,
    schema: dict[str, Any],
    max_new_tokens: int,
    progress_every_tokens: int,
    progress_every_seconds: float,
) -> str:
    messages = [
        {
            "role": "system",
            "content": "당신은 법령 기반 페르소나 문서 QA 편집자다. 출력은 수정된 Markdown 문서만 생성한다.",
        },
        {
            "role": "user",
            "content": f"""다음 {spec.persona_id} 초안의 검증 오류를 모두 수정하라.
고정 역할과 JSON Schema는 변경하지 말고, 누락된 섹션을 보완하라.

[검증 오류]
{json.dumps(list(errors), ensure_ascii=False, indent=2)}

[고정 JSON Schema]
{json.dumps(schema, ensure_ascii=False, indent=2)}

[초안]
{draft}
""",
        },
    ]
    return engine.generate(
        messages,
        max_new_tokens=max_new_tokens,
        task_label=f"{spec.persona_id}:REPAIR",
        progress_every_tokens=progress_every_tokens,
        progress_every_seconds=progress_every_seconds,
    )


def taxonomy() -> dict[str, Any]:
    entries = [
        ("HF_LOOKOUT_FAILURE", "경계 소홀", "HUMAN_FACTOR"),
        ("HF_DELAYED_DECISION", "의사결정 지연", "HUMAN_FACTOR"),
        ("HF_FATIGUE", "피로", "HUMAN_FACTOR"),
        ("HF_INADEQUATE_MANEUVER", "부적절한 조선", "HUMAN_FACTOR"),
        ("CF_COMMUNICATION_FAILURE", "의사소통 실패", "COMMUNICATION_FACTOR"),
        ("TF_ENGINE_FAILURE", "기관 고장", "TECHNICAL_FACTOR"),
        ("TF_STEERING_FAILURE", "조타장치 고장", "TECHNICAL_FACTOR"),
        ("MF_INADEQUATE_MAINTENANCE", "정비 미흡", "MAINTENANCE_FACTOR"),
        ("EF_RESTRICTED_VISIBILITY", "제한시계", "ENVIRONMENTAL_FACTOR"),
        ("EF_STRONG_CURRENT", "강한 조류", "ENVIRONMENTAL_FACTOR"),
        ("OF_INADEQUATE_TRAINING", "교육 미흡", "ORGANIZATIONAL_FACTOR"),
        ("OF_INADEQUATE_SAFETY_MANAGEMENT", "안전관리 미흡", "ORGANIZATIONAL_FACTOR"),
        ("PF_NAVIGATION_RULE_VIOLATION", "항법상 의무 위반", "PROCEDURAL_FACTOR"),
        ("DF_ALARM_NOT_ACTIVATED", "경보 미작동", "DEFENSE_SYSTEM_FAILURE"),
        ("AF_DELAYED_EMERGENCY_RESPONSE", "비상대응 지연", "CONSEQUENCE_AGGRAVATING_FACTOR"),
        ("UF_INSUFFICIENT_INFORMATION", "정보 부족", "UNKNOWN_FACTOR"),
    ]
    return {
        "version": "1.0.0",
        "status": "INITIAL_TAXONOMY_REQUIRES_EXPERT_REVIEW",
        "labels": [
            {
                "label_code": code,
                "label_name_ko": name,
                "cause_category": category,
                "definition": "전문가 검토를 거쳐 확정할 초기 정의",
                "inclusion_criteria": [],
                "exclusion_criteria": [],
                "related_legal_sources": [],
                "version": "1.0",
            }
            for code, name, category in entries
        ],
    }


SOURCE_NOTE = (
    "본 3-Type Persona는 「해양사고의 조사 및 심판에 관한 법률」, 같은 법 시행령·시행규칙 및 "
    "중앙해양안전심판원 관련 행정규칙의 조사·심판·재결 절차와 업무처리 지침을 참조하여 설계하였다. "
    "IMO 해양사고 조사협약(CI Code) 개요는 국제적 사고조사 원칙을 이해하기 위한 보조 지침으로만 활용하였다."
)


def render_readme(documents: Sequence[Document], engine_name: str, backup_dir: Path | None) -> str:
    corpus_list = "\n".join(f"- `{doc.file_name}` ({doc.document_type})" for doc in documents)
    return f"""# KMST 3-Type Persona Model

{SOURCE_NOTE}

## 목적

Qwen3-14B와 법령 검색 컨텍스트를 이용해 재결서의 사실·증거 구조화, 사고원인·법령 검증, 연구용 원인기여도·레이블링을 담당하는 세 페르소나를 생성한다.

## 생성 정보

- 도구 버전: {TOOL_VERSION}
- 실제 모델: {DEFAULT_MODEL_NAME}
- 생성 엔진: {engine_name}
- 생성시각(UTC): {datetime.now(timezone.utc).isoformat()}
- 기존 파일 백업: {str(backup_dir) if backup_dir else '없음'}

`prompt.txt`에는 Qwen2.5-14B와 Qwen3-14B 표기가 혼재하지만 실제 로딩 모델은 사용자 지정 경로의 Qwen3-14B다.

## 사전학습 용어

본 시스템의 사전학습은 모델 가중치 재학습이 아니라 법령 10개와 IMO 참고문서의 전체 로딩, 조문 단위 색인, 검색 기반 컨텍스트 주입을 의미한다.

## 실행 순서

`KMST-P01 → KMST-P02 → KMST-P03 → 인간 전문가 검토`

## 법적·연구적 경계

- `official_ratio`: 재결서에 명시된 공식 원인제공비율만 원문 그대로 추출
- `analytic_contribution_score`: 법적 효력이 없는 연구·학습용 분석지표
- 자동 생성 레이블: `GOLDSET_CANDIDATE`
- 전문가 검증 완료 레이블: `EXPERT_VALIDATED_GOLDSET`
- OBD 연계, Prediction Model, 실시간 알람은 현 단계에서 제외
- 본 시스템은 법률자문·재결·징계 자동화 시스템이 아님

## 로딩한 코퍼스

{corpus_list}
"""


def write_static_artifacts(
    output_dir: Path,
    documents: Sequence[Document],
    chunks: Sequence[Chunk],
    master_prompt: str,
    backup_dir: Path | None,
    engine_name: str,
) -> None:
    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "required_domestic_count": len(REQUIRED_DOMESTIC_FILES),
        "international_guidance_count": 1,
        "total_chunks": len(chunks),
        "documents": [doc.manifest_entry() for doc in documents],
    }
    safe_write_text(output_dir / "corpus_manifest.json", json_text(manifest))
    validation_lines = [
        "# Corpus Validation Report",
        "",
        "- 상태: PASS",
        f"- 필수 국내 문서: {len(REQUIRED_DOMESTIC_FILES)}/{len(REQUIRED_DOMESTIC_FILES)} COMPLETE",
        "- IMO 참고문서: 1/1 COMPLETE",
        f"- 총 문자 수: {sum(doc.char_count for doc in documents):,}",
        f"- 총 조문·문단 청크: {len(chunks):,}",
        "- 전체 문서 EOF 읽기: 완료",
        "- IMO 격리 정책: 적용",
        "",
        "## 문서별 결과",
        "",
    ]
    validation_lines.extend(
        f"- PASS `{doc.file_name}` — {doc.file_size:,} bytes, {doc.article_count} articles, SHA-256 `{doc.sha256}`"
        for doc in documents
    )
    safe_write_text(output_dir / "corpus_validation_report.md", "\n".join(validation_lines) + "\n")
    safe_write_text(
        output_dir / "legal_source_hierarchy.md",
        """# Legal Source Hierarchy

1. 해양사고의 조사 및 심판에 관한 법률
2. 같은 법 시행령
3. 같은 법 시행규칙
4. 위임 행정규칙
5. 기타 해양안전심판원 행정규칙
6. IMO CI Code 개요 — INTERNATIONAL_GUIDANCE 전용

상위 규범과 충돌하는 하위 규정 해석을 생성하지 않는다. 적용시점이 불명확하면 `legal_review_required=true`로 표시한다.
""",
    )
    safe_write_text(
        output_dir / "common_persona_policy.md",
        f"""# Common Persona Policy

{SOURCE_NOTE}

- 사실·진술·증거·재결기관 판단·모델 추론을 분리한다.
- 모든 핵심 항목은 원문 또는 이전 단계 ID로 추적 가능해야 한다.
- 조문을 추정하지 않는다.
- 개인정보를 최소화한다.
- 내부 사고과정이 아니라 검증 가능한 근거를 출력한다.
- 실제 재결, 처분, 징계, 민·형사 책임을 자동 결정하지 않는다.
- `official_ratio`와 `analytic_contribution_score`를 혼합하지 않는다.
- OBD·예측모델·실시간 알람은 제외한다.
""",
    )
    pipeline = f"""# Persona Pipeline Master Prompt

{SOURCE_NOTE}

## 실행모델

실제 모델은 `{DEFAULT_MODEL_NAME}`이다. 아래 사용자 마스터 프롬프트의 Qwen2.5 표기는 모델명 혼재 기록으로 보존하되 실행 구성은 Qwen3-14B를 따른다.

## 파이프라인

1. KMST-P01: 재결서 사실·증거 추출
2. KMST-P02: 사고원인·인과관계·법령 정합성 검증
3. KMST-P03: 원인기여도·레이블 생성·품질검증
4. 인간 전문가: GoldSet 후보 승인 또는 반려

## 입력 템플릿

```text
[CASE_METADATA]
case_id:
document_type: FULL_DECISION | DECISION_SUMMARY
tribunal:
decision_date:
incident_date:
analysis_reference_date:
source_file:
page_information_available: true | false

[DECISION_TEXT]
재결서 또는 재결요약서 원문

[OPTIONAL_METADATA]
known_vessels:
known_actors:
known_incident_type:
known_official_ratio:
notes:
```

## 원본 prompt.txt

{master_prompt}
"""
    safe_write_text(output_dir / "persona_pipeline_master_prompt.md", pipeline)
    safe_write_text(output_dir / "cause_label_taxonomy.json", json_text(taxonomy()))
    safe_write_text(
        output_dir / "contribution_scoring_policy.md",
        """# Contribution Scoring Policy

`analytic_contribution_score = (증거강도×0.30 + 인과근접성×0.25 + 반사실적 방지가능성×0.20 + 재결서 명시성×0.15 + 자료일치성×0.10) / 4`

각 요소는 0~4점이다. 결과 범위는 0~1이며 합계를 1로 강제하지 않는다. `INSUFFICIENT`, 원문 위치 누락, 증거 연결 부재, 중대한 미해결 충돌은 `NOT_SCORABLE`이다. 이 값은 공식 원인제공비율·민사상 과실비율·법적 책임이 아니다.
""",
    )
    safe_write_text(
        output_dir / "goldset_candidate_policy.md",
        """# GoldSet Candidate Policy

LLM 자동 레이블은 `GOLDSET_CANDIDATE`다. 해양안전·해사법 전문가 이중 검토, 불일치 조정, 출처 확인, 레이블 정의서 검토, 재현성 평가를 통과해야 `EXPERT_VALIDATED_GOLDSET`으로 승격한다.
""",
    )
    safe_write_text(
        output_dir / "data_leakage_prevention_policy.md",
        """# Data Leakage Prevention Policy

재결서에서 추출한 사고결과·최종원인·공식비율·재결·징계·사후 조사정보는 정답 레이블 생성에만 사용한다. 미래 예측모델 입력에는 알람 기준시점 이전에 이용 가능했던 정보만 사용한다. OBD 연계와 예측모델 구현은 현재 범위에서 제외한다.
""",
    )
    safe_write_text(output_dir / "README.md", render_readme(documents, engine_name, backup_dir))
    estimated_token_report = {
        "status": "ESTIMATED_DRY_RUN",
        "tokenizer": "NOT_LOADED",
        "note": "정확한 수치는 --engine transformers 실행 시 Qwen3 tokenizer로 덮어씁니다.",
        "prompt_characters": len(master_prompt),
        "corpus_characters": sum(document.char_count for document in documents),
        "total_characters": len(master_prompt) + sum(document.char_count for document in documents),
        "documents": [
            {
                "document_id": document.document_id,
                "file_name": document.file_name,
                "characters": document.char_count,
                "tokens": None,
            }
            for document in documents
        ],
    }
    safe_write_text(output_dir / "token_count_report.json", json_text(estimated_token_report))


def write_exact_token_report(
    output_dir: Path,
    engine: TransformersEngine,
    documents: Sequence[Document],
    master_prompt: str,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    corpus_tokens = 0
    log("Qwen3 토크나이저로 법령 코퍼스 토큰 수 계산")
    for document in documents:
        count = engine.count_tokens(document.text)
        corpus_tokens += count
        rows.append(
            {
                "document_id": document.document_id,
                "file_name": document.file_name,
                "document_type": document.document_type,
                "characters": document.char_count,
                "tokens": count,
            }
        )
        log(f"문서 토큰: {document.document_id} tokens={count:,} file={document.file_name}")
    prompt_tokens = engine.count_tokens(master_prompt)
    total_tokens = corpus_tokens + prompt_tokens
    report = {
        "status": "EXACT",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tokenizer_path": str(engine.model_path),
        "tokenizer_model": DEFAULT_MODEL_NAME,
        "prompt_tokens": prompt_tokens,
        "corpus_tokens": corpus_tokens,
        "total_source_tokens": total_tokens,
        "note": (
            "전체 소스 토큰 수이며 한 번에 모델에 입력되는 토큰 수가 아닙니다. "
            "페르소나별 입력은 BM25로 선택한 법령 청크만 포함합니다."
        ),
        "documents": rows,
    }
    safe_write_text(output_dir / "token_count_report.json", json_text(report))
    log(
        f"전체 토큰: prompt={prompt_tokens:,}, corpus={corpus_tokens:,}, "
        f"total_source={total_tokens:,}"
    )
    return report


def validate_json_schemas(output_dir: Path) -> list[str]:
    errors: list[str] = []
    try:
        from jsonschema.validators import validator_for
    except ImportError:
        validator_for = None
    for spec in PERSONAS:
        path = output_dir / spec.schema_file
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            if validator_for:
                validator = validator_for(schema)
                validator.check_schema(schema)
        except Exception as exc:  # noqa: BLE001 - 검증 보고에 전체 오류를 남긴다.
            errors.append(f"{spec.schema_file}: {exc}")
    return errors


def validate_outputs(output_dir: Path) -> tuple[str, list[str]]:
    failures: list[str] = []
    for file_name in REQUIRED_OUTPUT_FILES:
        if file_name == "persona_generation_validation_report.md":
            continue
        path = output_dir / file_name
        if not path.is_file():
            failures.append(f"파일 누락: {file_name}")
        elif path.stat().st_size == 0:
            failures.append(f"빈 파일: {file_name}")
    failures.extend(validate_json_schemas(output_dir))
    for spec in PERSONAS:
        path = output_dir / spec.output_file
        if path.is_file():
            failures.extend(
                f"{spec.output_file}: {error}"
                for error in validate_persona_markdown(path.read_text(encoding="utf-8"), spec)
            )
    return ("PASS" if not failures else "FAIL"), failures


def write_failure_report(output_dir: Path, reason: str, missing: Sequence[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Corpus Validation Report", "", "- 상태: FAIL", f"- 사유: {reason}", ""]
    if missing:
        lines.extend(["## 누락 파일", ""] + [f"- `{name}`" for name in missing])
    safe_write_text(output_dir / "corpus_validation_report.md", "\n".join(lines) + "\n")


def build(args: argparse.Namespace) -> int:
    prompt_path = args.prompt.expanduser().resolve()
    corpus_dir = args.corpus.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    if not prompt_path.is_file():
        raise BuildError(f"prompt.txt가 없습니다: {prompt_path}")
    master_prompt, prompt_encoding = read_text(prompt_path)
    if len(master_prompt.strip()) < 1000:
        raise BuildError("prompt.txt가 지나치게 짧습니다.")
    log(f"prompt.txt 로드: {len(master_prompt):,} chars, {prompt_encoding}")

    documents, missing = load_corpus(corpus_dir)
    if missing:
        write_failure_report(output_dir, "필수 문서 누락으로 생성 중단", missing)
        raise BuildError("필수 법령 문서가 누락되었습니다: " + ", ".join(missing))
    log(f"법령·지침 전체 로드: {len(documents)} documents")

    chunks = [chunk for document in documents for chunk in chunk_document(document)]
    if not chunks:
        raise BuildError("법령 코퍼스에서 색인 청크를 생성하지 못했습니다.")
    log(f"조문·문단 색인: {len(chunks):,} chunks")
    index = BM25Index(chunks)

    backup_dir = prepare_output_dir(output_dir, backup=not args.no_backup)
    write_static_artifacts(
        output_dir,
        documents,
        chunks,
        master_prompt,
        backup_dir,
        engine_name=args.engine,
    )
    for spec in PERSONAS:
        safe_write_text(output_dir / spec.schema_file, json_text(SCHEMAS[spec.persona_id]))

    engine: TransformersEngine | None = None
    if args.engine == "transformers":
        engine = TransformersEngine(
            model_path=args.model,
            dtype=args.dtype,
            load_in_4bit=args.load_in_4bit,
            force_gpu=args.force_gpu,
            attn_implementation=args.attn_implementation,
            seed=args.seed,
        )
        write_exact_token_report(output_dir, engine, documents, master_prompt)

    persona_generation: dict[str, Any] = {}
    for spec in PERSONAS:
        log(f"{spec.persona_id} {spec.korean_name} 생성")
        source_chunks = select_grounding_chunks(
            index,
            spec.retrieval_query,
            chunks,
            top_k=args.top_k,
            max_chars=args.max_legal_context_chars,
        )
        fallback = render_fallback_persona(spec, SCHEMAS[spec.persona_id], source_chunks)
        content = fallback
        method = "DETERMINISTIC_DRY_RUN"
        errors: list[str] = []
        if engine is not None:
            legal_context = render_legal_context(
                source_chunks, max_chars=args.max_legal_context_chars
            )
            messages = build_generation_messages(
                master_prompt, spec, SCHEMAS[spec.persona_id], legal_context
            )
            draft = engine.generate(
                messages,
                max_new_tokens=args.max_new_tokens,
                task_label=f"{spec.persona_id}:GENERATE",
                progress_every_tokens=args.progress_every_tokens,
                progress_every_seconds=args.progress_every_seconds,
            )
            errors = validate_persona_markdown(draft, spec)
            attempts = 0
            while errors and attempts < args.repair_attempts:
                log(f"{spec.persona_id} 자동 보정: {len(errors)} issues")
                draft = repair_persona(
                    engine,
                    draft,
                    errors,
                    spec,
                    SCHEMAS[spec.persona_id],
                    args.max_new_tokens,
                    args.progress_every_tokens,
                    args.progress_every_seconds,
                )
                errors = validate_persona_markdown(draft, spec)
                attempts += 1
            completed_personas = PERSONAS.index(spec) + 1
            remaining_personas = len(PERSONAS) - completed_personas
            stats = engine.last_generation_stats
            rate = float(stats.get("tokens_per_second", 0.0))
            if rate > 0 and remaining_personas > 0:
                conservative_remaining = remaining_personas * args.max_new_tokens / rate
                expected_finish = datetime.now().astimezone() + timedelta(
                    seconds=conservative_remaining
                )
                overall_percent = completed_personas / len(PERSONAS) * 100.0
                log(
                    "전체 예상 진행: "
                    f"personas={completed_personas}/{len(PERSONAS)}, "
                    f"overall_percent={overall_percent:.1f}%, "
                    f"measured_rate={rate:.2f}_tokens_per_sec, "
                    f"conservative_remaining_seconds={conservative_remaining:.0f}, "
                    f"estimated_finish={expected_finish.isoformat()}"
                )
            if errors:
                content = fallback + "\n\n## LLM 생성 실패 기록\n\n" + "\n".join(
                    f"- {error}" for error in errors
                )
                method = "DETERMINISTIC_FALLBACK_AFTER_LLM_VALIDATION_FAILURE"
            else:
                # 법적 안전장치를 LLM이 약화하지 못하도록 결정론적 부록을 추가한다.
                content = draft + "\n\n---\n\n" + immutable_policy_appendix(spec)
                method = "QWEN3_14B_PLUS_IMMUTABLE_POLICY_APPENDIX"
        safe_write_text(output_dir / spec.output_file, content.rstrip() + "\n")
        persona_generation[spec.persona_id] = {
            "method": method,
            "retrieved_source_chunks": [chunk.chunk_id for chunk in source_chunks],
            "retrieved_source_files": sorted({chunk.file_name for chunk in source_chunks}),
            "validation_errors_before_fallback": errors,
        }

    status, failures = validate_outputs(output_dir)
    report = {
        "generation_status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool_version": TOOL_VERSION,
        "actual_model": DEFAULT_MODEL_NAME if args.engine == "transformers" else "NOT_LOADED_DRY_RUN",
        "prompt_model_name_conflict_detected": (
            "Qwen2.5-14B" in master_prompt and "Qwen3-14B" in master_prompt
        ),
        "corpus_validation": "PASS",
        "persona_validation": "PASS" if not failures else "FAIL",
        "schema_validation": "PASS" if not validate_json_schemas(output_dir) else "FAIL",
        "legal_consistency_validation": "HUMAN_REVIEW_REQUIRED",
        "failed_checks": failures,
        "required_corrections": failures,
        "output_directory": str(output_dir),
        "backup_directory": str(backup_dir) if backup_dir else None,
        "persona_generation": persona_generation,
        "limitations": [
            "자동 생성 레이블은 GoldSet Candidate이며 전문가 검증 전 확정 GoldSet이 아님",
            "검색 기반 법령 주입이며 모델 가중치의 continued pre-training이 아님",
            "OBD 연계와 Prediction Model 구현은 범위에서 제외",
        ],
    }
    report_markdown = "# Persona Generation Validation Report\n\n```json\n" + json_text(report) + "```\n"
    safe_write_text(output_dir / "persona_generation_validation_report.md", report_markdown)
    log(f"생성 완료: {status} — {output_dir}")
    if failures:
        for failure in failures:
            log(f"FAIL: {failure}")
    return 0 if status == "PASS" else 3


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return build(args)
    except BuildError as exc:
        print(f"[KMST][ERROR] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[KMST][ERROR] 사용자에 의해 중단되었습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
