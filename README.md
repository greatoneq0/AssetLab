# AssetLab

자산배분 자료 · 백테스트 노트 · 실험용 툴 허브.

## 리밸런싱 백테스트

- **위치**: [tools/backtest/](tools/backtest/) (메인 페이지에서 "리밸런싱 백테스트" 버튼)
- **데이터**: 하루에 **한국장 종료 후 1회**, **미국장 종료 후 1회** 시점 가격을 사용해 계산합니다.
- **전략**
  - **정적 리밸런싱**: 주기(관측 횟수/매월 1일) 고정으로 목표 비중 복원
  - **동적 리밸런싱**: 밴드(비중 이탈 시), 모멘텀 비중
  - **혼합**: 정적 + 동적 전략을 동일 비중으로 합쳐 백테스트

데이터는 `data/prices.json`에서 로드합니다. 없으면 샘플 데이터로 실행됩니다.

## GitHub Actions로 가격 갱신

- **워크플로**: [.github/workflows/update_prices.yml](.github/workflows/update_prices.yml)
- **스케줄**: 평일 07:00 UTC(한국장 종료 후), 22:00 UTC(미국장 종료 후) 각 1회
- **수동 실행**: Actions 탭 → "Update prices (KR/US close)" → "Run workflow" → market 선택(both/kr/us)
- **데이터 소스**: Python 스크립트 [tools/update_prices.py](tools/update_prices.py)에서 **FinanceDataReader**로 종가 조회 후 `data/prices.json`, `data/meta.json` 갱신
  - 한국 주식은 **KRX 소스**로 조회해 안정성을 높였고, 미국은 심볼(SPY, TLT) 직접 조회.
  - 요청이 너무 많거나 빠르면 차단될 수 있어 **심볼당 2초 대기**와 최소 요청(하루 2회 × 3종목)으로 차단 위험을 줄였습니다.

실제 거래가 아닌 **정기 리밸런싱·변동성 구간 검토**용으로, 장 마감 후 받아온 값으로 전략을 백테스트할 수 있게 구성했습니다.

## 매일 아침 증시 브리핑 (텔레그램)

- **워크플로**: [.github/workflows/daily_market_news.yml](.github/workflows/daily_market_news.yml)
- **스케줄**: 매일 23:03 UTC(= **08:03 KST**). GitHub 클라우드에서 실행되므로 내 기기 상태와 무관하게 발송됩니다.
- **스크립트**: [tools/market_brief.py](tools/market_brief.py)
  - **항상**: Yahoo Finance에서 미국/아시아·유럽/한국 주요 지수 + 원/달러·유가·금의 종가·등락률을 조회해 한국어 요약을 텔레그램으로 발송(무료, API 키 불필요).
  - **선택**: 시크릿에 `ANTHROPIC_API_KEY`가 있으면 Claude + 웹검색으로 '왜 움직였는지' 뉴스 맥락까지 붙인 풍부한 브리핑으로 자동 업그레이드됩니다.
- **필요한 설정** (저장소 → Settings → Secrets and variables → Actions):
  - 시크릿 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (필수)
  - 시크릿 `ANTHROPIC_API_KEY` (선택) / 변수 `CLAUDE_MODEL` (선택, 기본 `claude-opus-4-8`)
- **수동 실행**: Actions 탭 → "Daily market news (Telegram)" → "Run workflow"

## GitHub Pages

- 저장소 설정 → Pages → Source: **main** 브랜치, **/ (root)**
- 배포 후: `https://<username>.github.io/<repo>/` 에서 메인, `.../tools/backtest/` 에서 백테스트

## 면책

이 사이트의 내용은 교육/연구 목적이며 투자 조언이 아닙니다. 모든 전략은 과거 데이터 기반이며, 실거래 적용 전 반드시 본인 검증이 필요합니다.
