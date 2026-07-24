<?php
/**
 * STEP3 단독 페이지는 report.php(STEP1~3 통합 리포트)로 흡수되었다.
 * 예전 북마크/링크가 깨지지 않도록 이 파일은 리다이렉트만 한다.
 */
declare(strict_types=1);
header("Location: report.php#step3-kselect", true, 302);
echo '<!doctype html><meta charset="utf-8"><p>이동했습니다: <a href="report.php#step3-kselect">report.php#step3-kselect</a></p>';
