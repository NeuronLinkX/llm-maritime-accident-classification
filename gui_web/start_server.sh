#!/usr/bin/env bash
# report.php를 9000번 포트에서 서비스하는 스크립트

set -euo pipefail

PORT="${REPORT_PORT:-9000}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "PHP 내장 서버 시작: http://localhost:$PORT (디렉토리: $DIR)"
cd "$DIR" || exit 1
exec php -S "localhost:$PORT"
