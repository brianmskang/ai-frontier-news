# 📝 AI News Aggregator - Future Roadmap

## 1. 🕒 자동화 스케줄링 (Daily Routine)
- [ ] **오전 8:30:** 수집 스크립트(`scraper.py`) 자동 실행.
- [ ] **오전 9:00:** 리포트 정리 및 배포 완료.
- [ ] **Pass 조건 처리:**
    - 만약 새로운 기사가 하나도 없다면?
    - 파일명: `[Pass] YYYY-MM-DD_HH-MM_AI_NEWS_DAILY.md`
    - 본문 내용: "현재 새로운 업데이트가 없어 이번 리포트는 생략합니다."

## 2. 🌐 소스 추가 및 최적화
- [ ] **OpenAI / Anthropic / Google** 외 추가 소스 발굴 (Meta AI, Microsoft Research 등).
- [ ] 사이트별 스크래핑 로직(Selector)을 별도 설정 파일(`selectors.json`)로 분리하여 관리.
- [ ] 스크래핑 속도 향상 (비동기 병렬 처리 최적화).

## 3. 🤖 자동화 인프라 구축
- [ ] **GitHub Actions:** 매일 아침 자동으로 실행되도록 워크플로우(`.github/workflows/daily_news.yml`) 설정.
- [ ] **로컬 Cron:** 맥북 로컬 환경에서 `crontab` 설정 가이드 작성.

## 4. 💾 데이터베이스 및 스키마 설계
- [ ] `articles` 테이블 확장:
    - `category`: 기사 카테고리 (Research, Product, Engineering...)
    - `tags`: 주요 키워드 태그
    - `is_read`: 사용자 읽음 여부
- [ ] DB 마이그레이션 스크립트 작성.

## 5. 🛡️ 에러 핸들링 및 알림
- [ ] 사이트 구조 변경으로 스크래핑 실패 시, 관리자에게 알림(Slack/Discord/Email) 전송.
- [ ] Gemini API 할당량 초과(429 Error) 시 지수 백오프(Exponential Backoff) 재시도 로직 강화.

## 6. ✅ 테스트 및 품질 관리
- [ ] **단위 테스트:** 각 소스별 파싱 로직 테스트 코드 작성 (`tests/test_parsers.py`).
- [ ] **통합 테스트:** 전체 파이프라인(수집->요약->저장) 테스트.
