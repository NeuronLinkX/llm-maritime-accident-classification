# LLM 기반 해양사고 원인 분류체계 구축 연구

한국해양안전심판원(KMST) 재결요약서(`.hwp`/`.hwpx`/PDF)를 입력받아 텍스트를 추출하고, SBERT 임베딩 유사도 정량화·K-Means 군집화를 거쳐 LLM 기반 사고원인 구조화 라벨링까지 이어지는 4단계 파이프라인입니다.

```
STEP 1  전처리                 문서 → 문단 추출 → 문장 정리 → 4대 항목 파싱 → JSON
STEP 2  SBERT 유사도 정량화     5개 한국어 SBERT 모델 벤치마크, 문서 쌍 코사인 유사도
STEP 3  K-Means 군집화          Elbow+Silhouette 자동 K 탐색, 군집별 키워드/워드클라우드
STEP 4  LLM 구조화 라벨링       OpenAI API / DGX Spark 로컬 LLM 두 경로 병행 (실험 단계)
```

STEP 1~4 결과는 통합 웹 리포트(`gui_web/report.php`, 정적 스냅샷은 `gui_web/report.html`) 한 페이지에서 확인할 수 있습니다.

## 문서 안내

- **설치·실행 방법**: [GUIDE.md](GUIDE.md) : 환경 구축부터 각 STEP 실행, 문제 해결까지 전체 절차
- **설계 근거·baseline**: [Layer.md](Layer.md) : 각 STEP에서 Python/C++을 나눈 이유, 선행 연구 대비 baseline

## 빠른 시작

```bash
git clone <this repo>
cd step_1

# 설정 파일 준비 (API 키는 본인 것으로 채워야 함)
cp config/config.php.example config/config.php

# STEP 1~4 각 단계 실행은 GUIDE.md 참고

# 통합 리포트 + 로컬 LLM 서버를 한 번에 띄우기
./start_servers.sh
```

## 데이터 안내

이 저장소에는 원본 재결요약서(`step_1_process/data/`)와 그 추출 결과(`step_1_process/data_output/`, `step_2_process/from_step1/`)가 포함되어 있지 않습니다.
실제 사고 조사 문서의 원문 및 개인정보를 포함할 수 있어 재배포하지 않습니다.
직접 KMST에서 재결요약서를 구해 `step_1_process/data/`에 넣고 STEP 1부터 실행하면 동일한 결과를 재현할 수 있습니다.

## 라이선스

TBD
