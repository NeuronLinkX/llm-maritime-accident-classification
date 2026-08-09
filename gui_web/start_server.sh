#!/bin/bash
# simulation.html(단일 파일 STEP1~4 시뮬레이션)을 9000번 포트에서 서비스하는 스크립트.
# index.php가 루트 요청을 simulation.html로 넘겨준다.

PORT=9000
DIR="/home/jiwoo/Desktop/workspace/SBERT/llm_based_root_cause_classification_system/gui_web"

# 이미 9000번 포트를 점유 중인 프로세스가 있으면 종료
EXISTING_PID=$(lsof -ti tcp:$PORT)
if [ -n "$EXISTING_PID" ]; then
    echo "포트 $PORT 사용 중인 프로세스($EXISTING_PID) 종료 중..."
    kill -9 $EXISTING_PID
    sleep 1
fi

echo "PHP 내장 서버 시작: http://0.0.0.0:$PORT (디렉토리: $DIR)"
cd "$DIR" || exit 1
php -S 0.0.0.0:$PORT
