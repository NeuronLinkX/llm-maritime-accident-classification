<?php
// gui_web/의 새 기본 진입점. 예전에는 report.php(STEP1~3 실시간 PHP 리포트)가 이 자리를
// 맡았지만, 리포트 시스템 전체(report*.php, lib_*.php, api_*.php)를 걷어내고 단일 정적
// 파일 simulation.html(서버 없이도 그대로 열리는 STEP1~4 통합 시뮬레이션)로 대체했다.
// 이 파일은 그 새 진입점으로 안내만 한다.
header("Location: simulation.html");
exit;
