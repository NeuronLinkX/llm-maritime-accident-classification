#!/usr/bin/env bash
# Apple Silicon Mac용 Python 환경과 기본 4-bit LLM을 준비한다.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
MODEL_ID="mlx-community/Qwen2.5-3B-Instruct-4bit"
MODEL_DIR="$SCRIPT_DIR/step_4_process/models/Qwen2.5-3B-Instruct-4bit"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "[setup_macos] Apple Silicon macOS 전용 설치 스크립트입니다." >&2
  exit 1
fi

if [ -z "$PYTHON_BIN" ]; then
  for candidate in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
    if [ -x "$candidate" ]; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "[setup_macos] Python 3.12가 필요합니다. 'brew install python@3.12' 후 다시 실행하세요." >&2
  exit 1
fi

if [ ! -x "$SCRIPT_DIR/.venv-mac/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$SCRIPT_DIR/.venv-mac"
fi

"$SCRIPT_DIR/.venv-mac/bin/python" -m pip install --upgrade pip
"$SCRIPT_DIR/.venv-mac/bin/pip" install -r "$SCRIPT_DIR/step_4_process/requirements-macos.txt"

echo "[setup_macos] MLX 4-bit 모델 다운로드: $MODEL_ID"
"$SCRIPT_DIR/.venv-mac/bin/python" - "$MODEL_ID" "$MODEL_DIR" <<'PY'
import sys
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id=sys.argv[1],
    local_dir=sys.argv[2],
    ignore_patterns=["*.gguf", "original/*"],
)
PY

echo "[setup_macos] 준비 완료. ./start_servers.sh 로 실행하세요."
