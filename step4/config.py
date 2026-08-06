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
