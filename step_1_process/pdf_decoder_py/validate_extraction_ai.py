#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")


def iter_json(paths):
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            yield from sorted(path.glob("*.json"))
        else:
            yield path


def payload_from(path):
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    keywords = data.get("keyword_sentences") or {}
    return {
        "file": str(path),
        "format": data.get("format"),
        "extraction_mode": data.get("extraction_mode"),
        "pdf_layout_type": data.get("pdf_layout_type"),
        "keyword_sentences": {key: keywords.get(key, []) for key in ["사건 개요", "일시", "장소", "사고 경위"]},
        "source_excerpt": (data.get("normalized_text") or "")[:7000],
    }


def response_text(data):
    if data.get("output_text"):
        return data["output_text"]
    parts = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if "text" in content:
                parts.append(content["text"])
    return "\n".join(parts)


def parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def validate(payload, model, timeout):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    prompt = (
        "너는 한국 해양심판원 재결요약서 전처리 결과를 검수하는 감사자다. "
        "사건 개요, 일시, 장소, 사고 경위가 원문 발췌와 맞는지 엄격히 판정하라. "
        "JSON 객체 하나만 반환하라: "
        '{"status":"pass|suspect|fail","score":0-100,'
        '"issues":[{"field":"사건 개요|일시|장소|사고 경위|overall","severity":"low|medium|high","message":"..."}],'
        '"corrected_hint":{"사건 개요":"","일시":"","장소":"","사고 경위":""}}\n\n'
        + json.dumps(payload, ensure_ascii=False)
    )
    body = {"model": model, "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}], "temperature": 0}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return parse_json(response_text(json.loads(resp.read().decode("utf-8"))))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("-o", "--output", default="test_output/ai_validation.jsonl")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("w", encoding="utf-8") as out:
        for path in iter_json(args.inputs):
            if args.limit and count >= args.limit:
                break
            try:
                record = {"file": str(path), "ok": True, "verdict": validate(payload_from(path), args.model, args.timeout)}
            except urllib.error.HTTPError as exc:
                record = {"file": str(path), "ok": False, "error": exc.read().decode("utf-8", "replace")}
            except Exception as exc:
                record = {"file": str(path), "ok": False, "error": str(exc)}
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            count += 1
            print(f"[{count}] {path} -> {record.get('verdict', {}).get('status', 'error')}", file=sys.stderr)
    print(str(out_path))


if __name__ == "__main__":
    main()
