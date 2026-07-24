#!/usr/bin/env python3
"""5개 SBERT 모델로 from_step1/step1_dataset.jsonl의 embedding_text를
인코딩해서 C++ 벤치마크 도구(cpp/benchmark.cpp)가 바로 읽을 수 있는
형태로 저장한다.

- 임베딩 자체(신경망 추론)는 Python(sentence-transformers)에서 수행한다
  — 각 모델이 공식적으로 배포/검증된 실행 경로이기 때문이다.
- 유사도 계산·벤치마크 집계는 여기서 하지 않는다 — 그건 C++ 몫이다.

출력 (embeddings/):
  meta.csv          index,id,category,filename (모델과 무관, 한 번만 생성)
  <모델별칭>.bin     float32 원시 바이너리, N행 x 768열, 행 우선(row-major).
                     인코딩 시 L2 정규화를 미리 해두므로, C++ 쪽에서는
                     내적(dot product)만 하면 그게 곧 코사인 유사도다.
  manifest.json      참고용 메타(모델 목록/차원/건수) — C++은 이 파일을
                     읽지 않는다(파일 크기로 스스로 검증한다).

실행:
    source sbert_env/bin/activate
    python3 embed.py
"""
import json
import time
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parent
DATASET_PATH = ROOT / "from_step1" / "step1_dataset.jsonl"
MODELS_CACHE = ROOT / "models"
EMB_DIR = ROOT / "embeddings"

MODELS = [
    ("jhgan/ko-sroberta-nli", "ko-sroberta-nli"),
    ("jhgan/ko-sroberta-sts", "ko-sroberta-sts"),
    ("jhgan/ko-sroberta-multitask", "ko-sroberta-multitask"),
    ("snunlp/KR-SBERT-V40K-klueNLI-augSTS", "kr-sbert-v40k-kluenli-augsts"),
    ("snunlp/KR-SBERT-Medium-klueNLI-klueSTS", "kr-sbert-medium-kluenli-kluests"),
]


def load_records():
    records = []
    with open(DATASET_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if not d.get("embedding_text", "").strip():
                continue  # 안전장치 — 이미 STEP1에서 걸러졌어야 하지만 혹시 몰라 재확인
            records.append(d)
    return records


def write_meta(records):
    EMB_DIR.mkdir(exist_ok=True)
    meta_path = EMB_DIR / "meta.csv"
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write("index,id,category,filename\n")
        for i, r in enumerate(records):
            # id/category/filename엔 쉼표가 들어갈 수 있어(파일명에 콤마 포함
            # 사례 다수) 큰따옴표로 감싸고, 내부 큰따옴표는 두 번 써서 이스케이프한다.
            def esc(s):
                return '"' + str(s).replace('"', '""') + '"'
            f.write(f"{i},{esc(r['id'])},{esc(r['category'])},{esc(r['filename'])}\n")
    return meta_path


def main():
    records = load_records()
    texts = [r["embedding_text"] for r in records]
    n = len(records)
    print(f"레코드 {n}건 로드 ({DATASET_PATH})")

    meta_path = write_meta(records)
    print(f"메타데이터 저장: {meta_path}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"디바이스: {device}")

    manifest = {"count": n, "dim": None, "models": []}

    for hf_name, short_name in MODELS:
        print(f"\n--- {hf_name} ({short_name}) ---")
        t0 = time.time()
        model = SentenceTransformer(hf_name, cache_folder=str(MODELS_CACHE), device=device)
        dim = model.get_sentence_embedding_dimension()
        if manifest["dim"] is None:
            manifest["dim"] = dim
        elif manifest["dim"] != dim:
            print(f"  [WARN] 이 모델의 차원({dim})이 다른 모델과 다릅니다 "
                  f"(기존 {manifest['dim']}) — C++ 쪽은 768 고정이라 문제될 수 있습니다.")

        emb = model.encode(
            texts,
            batch_size=64,
            show_progress_bar=True,
            convert_to_numpy=True,
            normalize_embeddings=True,  # 미리 정규화 → C++에서는 내적=코사인 유사도
        ).astype(np.float32)

        out_path = EMB_DIR / f"{short_name}.bin"
        emb.tofile(out_path)
        elapsed = time.time() - t0
        print(f"  저장: {out_path} (shape={emb.shape}, {elapsed:.1f}초)")

        manifest["models"].append({
            "hf_name": hf_name, "short_name": short_name,
            "file": out_path.name, "dim": dim, "elapsed_sec": round(elapsed, 1),
        })

        del model
        if device == "cuda":
            torch.cuda.empty_cache()

    with open(EMB_DIR / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\n완료. 결과 위치: {EMB_DIR}")


if __name__ == "__main__":
    main()
