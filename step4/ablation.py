"""IDENTITY_BLOCK 마커 기반 identity_on/identity_off 프롬프트 조립 및 diff 검증.

정규식으로 정체성 문구를 "추측"해서 지우는 방식은 금지되어 있다 (지시서 2-2).
대신 persona_0N.md에 명시적으로 심어둔 <!-- IDENTITY_BLOCK_START/END --> 마커만
기계적으로 포함/제외한다. identity_on과 identity_off는 같은 원본 텍스트에서
마커 블록의 유무만 다르므로, 그 외 어떤 변경도 있을 수 없다 — 그래도 diff를
생성해 보관하고(재현성 증빙) 실제로 그 외 변경이 없는지 다시 한번 검증한다.
"""
from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path

START = "<!-- IDENTITY_BLOCK_START -->"
END = "<!-- IDENTITY_BLOCK_END -->"

_BLOCK_RE = re.compile(
    re.escape(START) + r"\n?(.*?)\n?" + re.escape(END) + r"\n?",
    re.DOTALL,
)


class AblationIntegrityError(RuntimeError):
    pass


def render_variant(text: str, use_identity: bool) -> str:
    """identity_on -> 마커만 제거하고 내용은 유지. identity_off -> 마커+내용 통째로 제거."""
    if use_identity:
        return text.replace(START + "\n", "").replace(START, "").replace(END + "\n", "").replace(END, "")
    return _BLOCK_RE.sub("", text)


def build_and_verify_diff(
    persona_id: str,
    raw_text: str,
    diff_dir: Path,
    logger: logging.Logger,
) -> tuple[str, str]:
    if START not in raw_text or END not in raw_text:
        raise AblationIntegrityError(
            f"{persona_id}: IDENTITY_BLOCK 마커가 없습니다. persona_0N.md에 "
            f"{START} ... {END} 마커를 먼저 삽입해야 합니다."
        )
    if raw_text.count(START) != raw_text.count(END):
        raise AblationIntegrityError(f"{persona_id}: IDENTITY_BLOCK_START/END 개수가 일치하지 않습니다.")

    on_text = render_variant(raw_text, use_identity=True)
    off_text = render_variant(raw_text, use_identity=False)

    if on_text == off_text:
        raise AblationIntegrityError(
            f"{persona_id}: identity_on/off 결과가 동일합니다 — 마커 안쪽에 실제 정체성 문구가 없는지 확인하세요."
        )

    # 재검증: on_text에서 identity 블록에 해당하는 문구를 다시 제거하면 off_text와 완전히 같아야 한다.
    # (마커 밖 어딘가가 실수로 달라졌다면 여기서 불일치가 난다.)
    on_block_match = _BLOCK_RE.search(raw_text)
    identity_sentence = on_block_match.group(1).strip() if on_block_match else ""
    reconstructed_off = on_text.replace(identity_sentence, "", 1)
    reconstructed_off = re.sub(r"\n{3,}", "\n\n", reconstructed_off)
    normalized_off = re.sub(r"\n{3,}", "\n\n", off_text)
    if reconstructed_off.strip() != normalized_off.strip():
        raise AblationIntegrityError(
            f"{persona_id}: identity_on에서 정체성 문장만 제거한 결과가 identity_off와 다릅니다 — "
            "정체성 블록 외의 내용이 identity_off 생성 과정에서 변경된 것으로 보입니다."
        )

    diff_lines = list(
        difflib.unified_diff(
            on_text.splitlines(keepends=True),
            off_text.splitlines(keepends=True),
            fromfile=f"{persona_id}.identity_on",
            tofile=f"{persona_id}.identity_off",
        )
    )
    diff_text = "".join(diff_lines)

    diff_dir.mkdir(parents=True, exist_ok=True)
    diff_path = diff_dir / f"{persona_id}.diff"
    diff_path.write_text(diff_text, encoding="utf-8")
    logger.info("%s: ablation diff 생성 완료 (%s, %d줄)", persona_id, diff_path, len(diff_lines))

    # diff에 등장하는 삭제/추가 라인이 정체성 문장 텍스트에서만 유래했는지 최종 확인.
    changed_content_lines = [
        ln[1:].strip()
        for ln in diff_lines
        if (ln.startswith("-") or ln.startswith("+")) and not ln.startswith(("---", "+++"))
    ]
    identity_lines = {ln.strip() for ln in identity_sentence.splitlines() if ln.strip()}
    stray = [ln for ln in changed_content_lines if ln and ln not in identity_lines]
    if stray:
        raise AblationIntegrityError(
            f"{persona_id}: diff에 정체성 블록 외의 변경이 감지되었습니다: {stray}"
        )

    logger.info("%s: ablation diff 검증 통과 (정체성 블록 외 변경 없음)", persona_id)
    return on_text, off_text
