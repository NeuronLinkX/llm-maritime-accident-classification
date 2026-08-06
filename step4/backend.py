"""vLLM 백엔드 어댑터. temperature 등 생성 파라미터는 전부 config에서 읽고 하드코딩하지 않는다."""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass
class GenerationResult:
    raw_text: str
    clean_text: str
    think_block_stripped: bool
    prompt_tokens: int
    completion_tokens: int
    latency_sec: float


def strip_think_block(text: str) -> tuple[str, bool]:
    if "<think>" not in text:
        return text, False
    cleaned = _THINK_RE.sub("", text)
    if cleaned == text:
        # <think>는 있는데 닫는 태그가 없는 등 정상적으로 잘리지 않은 경우도 스트립으로 처리
        cleaned = text.split("<think>", 1)[0]
    return cleaned.strip(), True


class VLLMBackend:
    def __init__(self, model_cfg: dict, generation_cfg: dict, logger: logging.Logger):
        self.model_cfg = model_cfg
        self.generation_cfg = generation_cfg
        self.logger = logger
        self._llm = None
        self._tokenizer = None

    def load(self) -> None:
        if self.model_cfg.get("local_files_only"):
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        from transformers import AutoTokenizer
        from vllm import LLM

        model_path = self.model_cfg["path"]
        self.logger.info("모델 로드 시작: %s (backend=vllm)", model_path)
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_path, local_files_only=self.model_cfg.get("local_files_only", True)
        )
        self._llm = LLM(
            model=model_path,
            dtype=self.model_cfg.get("dtype", "bfloat16"),
            max_model_len=self.model_cfg.get("max_model_len", 32768),
            gpu_memory_utilization=self.model_cfg.get("gpu_memory_utilization", 0.85),
            # 결정성 확인에서 byte_identical_rate=0.0이 나온 원인 중 하나 — prefix caching은
            # 호출 순서/타이밍에 따라 캐시 적중 여부가 달라져 동일 입력에도 부동소수점 연산
            # 순서가 바뀔 수 있다. 군집 5개뿐이라 캐싱 이득보다 재현성이 더 중요해 끈다.
            enable_prefix_caching=False,
        )
        self.logger.info("모델 로드 완료")

    def _build_sampling_params(self):
        from vllm import SamplingParams

        gen = self.generation_cfg
        return SamplingParams(
            temperature=gen["temperature"],
            top_p=gen["top_p"],
            top_k=gen["top_k"],
            seed=None,
            max_tokens=gen["max_new_tokens"],
            repetition_penalty=gen["repetition_penalty"],
            # frequency_penalty: 이미 나온 횟수에 비례해 누적 페널티를 준다 — "EVIDENCE_343,
            # EVIDENCE_344, EVIDENCE_345..."처럼 같은 패턴을 계속 늘려가며 반복하는 경우
            # repetition_penalty(유무만 봄)보다 훨씬 직접적으로 억제한다.
            frequency_penalty=gen.get("frequency_penalty", 0.0),
        )

    def _render_prompt(self, system_prompt: str, user_prompt: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=self.model_cfg.get("enable_thinking", False),
        )

    def generate_batch(
        self, pairs: list[tuple[str, str]]
    ) -> list[GenerationResult]:
        """pairs: [(system_prompt, user_prompt), ...].

        군집 단위(최대 5개)라 vLLM 연속배치의 처리량 이득보다 재현성이 더 중요하다 —
        결정성 확인에서 byte_identical_rate=0.0이 나온 원인이 배치 구성에 따라 달라지는
        부동소수점 연산 순서였다. 한 번에 하나씩만 llm.generate()에 넣어 배치 구성
        자체가 항상 동일(크기 1)하도록 만든다.
        """
        if self._llm is None:
            raise RuntimeError("backend.load()를 먼저 호출해야 합니다.")

        sampling_params = self._build_sampling_params()
        results: list[GenerationResult] = []
        for sp, up in pairs:
            prompt = self._render_prompt(sp, up)
            start = time.time()
            outputs = self._llm.generate([prompt], sampling_params)
            elapsed = time.time() - start

            out = outputs[0]
            raw_text = out.outputs[0].text
            clean_text, stripped = strip_think_block(raw_text)
            prompt_tokens = len(out.prompt_token_ids) if out.prompt_token_ids else 0
            completion_tokens = len(out.outputs[0].token_ids) if out.outputs[0].token_ids else 0
            results.append(
                GenerationResult(
                    raw_text=raw_text,
                    clean_text=clean_text,
                    think_block_stripped=stripped,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_sec=elapsed,
                )
            )
        return results

    def count_tokens(self, text: str) -> int:
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_cfg["path"], local_files_only=self.model_cfg.get("local_files_only", True)
            )
        return len(self._tokenizer.encode(text))
