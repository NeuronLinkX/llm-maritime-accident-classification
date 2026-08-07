"""config/config.json 로드 및 검증."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


REQUIRED_TOP_KEYS = [
    "experiment",
    "model",
    "generation",
    "determinism_check",
    "paths",
    "cluster",
    "validation",
]


def infer_model_family(model_cfg: dict) -> str:
    """model.family가 명시돼 있으면 그대로 쓰고, 없으면 model.id/model.path에서 추정한다.

    "qwen3" — Qwen3 하이브리드 사고모드 관련 처리(/no_think 접미사, apply_chat_template의
    enable_thinking 인자)와 반복루프 국소 재시도(validation.repetition_retry)가 이 값일
    때만 활성화된다. Qwen3의 greedy 디코딩 자기강화 반복루프는 실측으로 확인된 Qwen3
    고유의 현상이고, Qwen2.5-14B-Instruct는 같은 입력·같은 기본 파라미터로 5/5 성공해
    이 우회 로직이 필요 없음을 확인했다(2026-08-07). 다른 모델을 추가할 때는 그 모델의
    실측 특성에 맞춰 이 함수가 반환하는 값과 해당 분기를 새로 정의할 것 — Qwen3 대응
    로직을 기본값으로 깔고 가지 않는다.
    """
    explicit = model_cfg.get("family")
    if explicit:
        return explicit.lower()
    probe = f"{model_cfg.get('id', '')} {model_cfg.get('path', '')}".lower()
    if "qwen3" in probe:
        return "qwen3"
    if "qwen2.5" in probe or "qwen2-5" in probe:
        return "qwen2.5"
    return "other"


class Config:
    def __init__(self, data: dict[str, Any], path: Path, sha256: str):
        self.data = data
        self.path = path
        self.sha256 = sha256

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, *keys, default=None):
        node = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def output_root(self) -> Path:
        root = Path(self.data["paths"]["output_root"])
        if not root.is_absolute():
            root = Path.cwd() / root
        return root

    @property
    def condition_names(self) -> list[str]:
        return [c["name"] for c in self.data["experiment"]["conditions"]]

    @property
    def chain_order(self) -> list[str]:
        return self.data["experiment"]["chain_order"]

    @property
    def model_family(self) -> str:
        return infer_model_family(self.data["model"])


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config 파일을 찾을 수 없습니다: {path}")
    raw_bytes = path.read_bytes()
    data = json.loads(raw_bytes.decode("utf-8"))

    missing = [k for k in REQUIRED_TOP_KEYS if k not in data]
    if missing:
        raise ValueError(f"config.json에 필수 키가 없습니다: {missing}")

    conditions = data["experiment"].get("conditions")
    if not conditions or len(conditions) != 2:
        raise ValueError(
            "experiment.conditions는 identity_on/identity_off 2개여야 합니다 "
            f"(받은 값: {conditions})"
        )
    names = {c["name"] for c in conditions}
    if names != {"identity_on", "identity_off"}:
        raise ValueError(f"conditions 이름이 예상과 다릅니다: {names}")

    chain_order = data["experiment"].get("chain_order")
    if not chain_order:
        raise ValueError("experiment.chain_order가 필요합니다 (예: persona_01/02/03 순서)")

    comparison_mode = data["experiment"].get("comparison_mode", "identity_marker")
    if comparison_mode not in ("identity_marker", "document_pair"):
        raise ValueError(
            f"experiment.comparison_mode는 'identity_marker' 또는 'document_pair'여야 합니다 "
            f"(받은 값: {comparison_mode})"
        )
    if comparison_mode == "document_pair" and not data["paths"].get("no_persona_dir"):
        raise ValueError(
            "experiment.comparison_mode가 'document_pair'이면 paths.no_persona_dir가 필요합니다."
        )

    gen = data["generation"]
    for key in ("temperature", "top_p", "top_k", "max_new_tokens", "repetition_penalty"):
        if key not in gen:
            raise ValueError(f"generation.{key}가 config.json에 없습니다")
    if gen["temperature"] != 0.0:
        raise ValueError(
            "이 파이프라인은 v2(greedy) 전용입니다. generation.temperature는 0.0이어야 합니다 "
            f"(받은 값: {gen['temperature']})"
        )

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    return Config(data=data, path=path, sha256=sha256)
