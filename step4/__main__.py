"""python -m step4 --config config/config.json [--dry-run] [--limit N]"""
from __future__ import annotations

import argparse
import sys

from step4 import pipeline
from step4.config import load_config
from step4.lock import AlreadyRunningError, single_instance_lock
from step4.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="step4")
    parser.add_argument("--config", required=True, help="config.json 경로")
    parser.add_argument("--dry-run", action="store_true", help="실제 생성 없이 실행 계획만 출력")
    parser.add_argument("--limit", type=int, default=None, help="스모크 테스트용 문서 수 제한")
    parser.add_argument(
        "--skip-determinism",
        action="store_true",
        help="결정성 검사를 다시 돌리지 않고 기존 metrics/determinism_check.json을 재사용 "
        "(persona_01 프롬프트가 바뀌지 않은 부분 재실행에 사용)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    logger = setup_logging(config.output_root)
    logger.info("config 로드 완료: %s (sha256=%s)", config.path, config.sha256)

    if args.dry_run:
        # dry-run은 GPU를 쓰지 않으므로 락이 필요 없다.
        pipeline.dry_run(config, logger)
        return 0

    try:
        with single_instance_lock(logger=logger):
            report_path = pipeline.real_run(
                config, logger, limit=args.limit, skip_determinism=args.skip_determinism
            )
    except AlreadyRunningError as exc:
        logger.error("%s", exc)
        print(f"\n[중단] {exc}", file=sys.stderr)
        return 1

    logger.info("완료. 리포트: %s", report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
