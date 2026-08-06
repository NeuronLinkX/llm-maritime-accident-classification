"""persona_model/ 자산 탐색 및 검증. 누락 시 즉시 실패(silent skip 금지)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path


PERSONA_MD_RE = re.compile(r"^(persona_\d+)_.*\.md$")


@dataclass
class PersonaAsset:
    persona_id: str  # e.g. "persona_01"
    md_path: Path
    schema_path: Path


def discover_persona_assets(persona_dir: str | Path, logger: logging.Logger) -> list[PersonaAsset]:
    persona_dir = Path(persona_dir)
    if not persona_dir.is_dir():
        raise FileNotFoundError(f"persona_dir가 존재하지 않습니다: {persona_dir}")

    top_level = sorted(p.name for p in persona_dir.iterdir())
    logger.info("persona_dir 상위 자산 목록 (%d개): %s", len(top_level), top_level)

    data_dir = persona_dir / "data"
    if data_dir.is_dir():
        corpus_files = sorted(p.name for p in data_dir.iterdir())
        logger.info("법령 코퍼스 자산 목록 (%d개): %s", len(corpus_files), corpus_files)
    else:
        logger.warning("법령 코퍼스 디렉터리(persona_model/data)가 없습니다.")

    md_candidates = sorted(
        p for p in persona_dir.glob("persona_*.md") if PERSONA_MD_RE.match(p.name)
    )
    if not md_candidates:
        raise FileNotFoundError(f"persona_*.md 자산을 찾지 못했습니다: {persona_dir}")

    assets: list[PersonaAsset] = []
    for md_path in md_candidates:
        m = PERSONA_MD_RE.match(md_path.name)
        persona_id = m.group(1)
        schema_matches = sorted(persona_dir.glob(f"{persona_id}_output_schema.json"))
        if not schema_matches:
            raise FileNotFoundError(
                f"{md_path.name}에 대응하는 {persona_id}_output_schema.json 스키마가 없습니다."
            )
        assets.append(PersonaAsset(persona_id=persona_id, md_path=md_path, schema_path=schema_matches[0]))

    logger.info(
        "발견된 페르소나 자산: %s", [(a.persona_id, a.md_path.name, a.schema_path.name) for a in assets]
    )
    if len(assets) != 3:
        logger.warning(
            "페르소나 파일이 3개가 아니라 %d개 발견되었습니다 — 발견된 개수대로 진행합니다.",
            len(assets),
        )

    generic_schema = persona_dir / "output_schema.json"
    if generic_schema.exists():
        logger.info("공용 출력 스키마 발견: %s", generic_schema)
    else:
        logger.info("공용 출력 스키마(output_schema.json)는 없음 — 페르소나별 스키마만 사용합니다.")

    return sorted(assets, key=lambda a: a.persona_id)
