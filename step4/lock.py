"""step4 중복 실행 방지 락.

이 세션에서 반복해서 겪은 문제(같은 GPU에 vLLM 인스턴스 두 개가 동시에 뜨면서 CUDA OOM)의
근본 원인은 "이미 실행 중인지 체크 없이 또 실행"이었다. fcntl.flock은 프로세스가
죽으면(정상 종료든 kill -9든) OS가 자동으로 잠금을 풀어주므로, 죽은 프로세스가 남긴
lock 파일 때문에 다음 실행이 영구히 막히는 문제가 없다.
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOCK_PATH = Path(__file__).resolve().parent.parent / "step_4_process" / ".step4_run.lock"


class AlreadyRunningError(RuntimeError):
    pass


@contextlib.contextmanager
def single_instance_lock(lock_path: Path | str = DEFAULT_LOCK_PATH, logger: logging.Logger | None = None):
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            existing = os.read(fd, 4096).decode("utf-8", errors="replace").strip()
            raise AlreadyRunningError(
                "이미 step4가 실행 중입니다 (동시에 두 번 실행하면 같은 GPU를 두 vLLM 인스턴스가 "
                f"동시에 잡으려다 CUDA OOM이 납니다). 락 파일: {lock_path}\n기존 실행 정보: {existing or '(알수없음)'}\n"
                "먼저 그 실행이 끝나기를 기다리거나, 확실히 죽은 프로세스라면 위 락 파일을 지우고 다시 시도하세요."
            ) from None

        os.ftruncate(fd, 0)
        info = f"pid={os.getpid()} started_at={datetime.now(timezone.utc).isoformat()}\n"
        os.write(fd, info.encode("utf-8"))
        os.fsync(fd)
        if logger:
            logger.info("단일 실행 락 획득: %s (%s)", lock_path, info.strip())
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
