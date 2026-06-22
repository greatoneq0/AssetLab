#!/usr/bin/env python3
"""세계 증시 일일 브리핑 → 텔레그램 발송.

동작:
  1) 항상: Yahoo Finance에서 주요 지수/환율/원자재 종가·등락률을 조회해 한국어 요약을 만든다.
  2) 선택: 환경변수 ANTHROPIC_API_KEY가 있으면 Claude + 웹검색으로 '왜 움직였는지' 뉴스 맥락을
     덧붙인 더 풍부한 브리핑을 만든다(없으면 1번의 숫자 요약만 보낸다).
  3) 텔레그램 봇으로 발송한다.

필요한 환경변수(시크릿):
  TELEGRAM_BOT_TOKEN  (필수)
  TELEGRAM_CHAT_ID    (필수)
  ANTHROPIC_API_KEY   (선택 — 있으면 뉴스 맥락 요약 활성화)
  CLAUDE_MODEL        (선택 — 기본 claude-opus-4-8, 비용을 줄이려면 claude-sonnet-4-6)
"""

import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; AssetLab-MarketBrief/1.0)"

# (표시이름, 야후심볼, 소수자리)
SYMBOLS = [
    ("S&P500",   "^GSPC", 2),
    ("나스닥",    "^IXIC", 2),
    ("다우",      "^DJI",  2),
    ("닛케이225", "^N225", 2),
    ("항셍",      "^HSI",  2),
    ("DAX",       "^GDAXI", 2),
    ("FTSE100",   "^FTSE", 2),
    ("코스피",    "^KS11", 2),
    ("코스닥",    "^KQ11", 2),
    ("원/달러",   "KRW=X", 2),
    ("WTI유가",   "CL=F",  2),
    ("금",        "GC=F",  2),
]


def http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_quote(symbol: str):
    """Yahoo chart API에서 현재가/전일종가를 가져온다. 실패 시 None."""
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol)
        + "?range=5d&interval=1d"
    )
    try:
        data = json.loads(http_get(url))
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None or prev in (None, 0):
            return None
        change_pct = (price - prev) / prev * 100.0
        return {"price": price, "prev": prev, "change_pct": change_pct}
    except (urllib.error.URLError, KeyError, IndexError, ValueError, TypeError):
        return None


def fmt_num(x: float, decimals: int) -> str:
    return f"{x:,.{decimals}f}"


def build_numbers_section() -> str:
    lines = []
    for name, sym, dec in SYMBOLS:
        q = fetch_quote(sym)
        if not q:
            lines.append(f"• {name}: 조회 실패")
            continue
        arrow = "🔺" if q["change_pct"] > 0 else ("🔻" if q["change_pct"] < 0 else "▪️")
        sign = "+" if q["change_pct"] >= 0 else ""
        lines.append(
            f"• {name}: {fmt_num(q['price'], dec)} "
            f"({arrow}{sign}{q['change_pct']:.2f}%)"
        )
    return "\n".join(lines)


def build_message(today_kst: str) -> str:
    numbers = build_numbers_section()
    header = f"📊 {today_kst} 세계 증시 브리핑"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return (
            f"{header}\n\n"
            f"🌐 주요 지수·환율·원자재\n{numbers}\n\n"
            f"※ 숫자 요약입니다. 뉴스 맥락(왜 움직였는지) 요약을 받으려면 "
            f"리포 시크릿에 ANTHROPIC_API_KEY를 추가하세요.\n"
            f"※ 투자 참고용이며 매매 판단은 본인 책임입니다."
        )

    # --- 선택 경로: Claude + 웹검색으로 뉴스 맥락 요약 ---
    try:
        return build_narrative_with_claude(numbers, today_kst, api_key)
    except Exception as e:  # 실패하면 숫자 요약으로 폴백
        sys.stderr.write(f"[warn] Claude 요약 실패, 숫자 요약으로 폴백: {e}\n")
        return (
            f"{header}\n\n🌐 주요 지수·환율·원자재\n{numbers}\n\n"
            f"※ 뉴스 요약 생성에 실패해 숫자만 보냅니다.\n"
            f"※ 투자 참고용이며 매매 판단은 본인 책임입니다."
        )


def build_narrative_with_claude(numbers: str, today_kst: str, api_key: str) -> str:
    import anthropic  # 워크플로에서 pip install anthropic

    model = os.environ.get("CLAUDE_MODEL") or "claude-opus-4-8"
    client = anthropic.Anthropic(api_key=api_key)

    prompt = (
        f"오늘은 {today_kst}(KST)입니다. 아래는 방금 조회한 주요 지수·환율·원자재의 "
        f"종가와 등락률입니다.\n\n{numbers}\n\n"
        "웹 검색으로 전일~당일 아침의 세계 증시 뉴스를 확인해, 한국어로 모바일에서 읽기 좋은 "
        "텔레그램 브리핑을 작성하세요. 요구사항:\n"
        f"- 첫 줄: '📊 {today_kst} 세계 증시 브리핑'\n"
        "- 섹션: 🇺🇸 미국 / 🌏 아시아·유럽 / 🇰🇷 한국 / 💱 환율·원자재\n"
        "- 각 섹션 2~4줄, 위 수치를 활용하고 '왜 움직였는지' 핵심 이슈를 사실 위주로.\n"
        "- 과장 없이 간결하게. 전체 3500자 이내.\n"
        "- 마지막 줄: '※ 투자 참고용이며 매매 판단은 본인 책임입니다.'\n"
        "- 출력은 텔레그램에 그대로 보낼 본문 텍스트만. 마크다운 헤더(#)나 출처 각주는 넣지 마세요."
    )

    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search"}]

    resp = client.messages.create(
        model=model, max_tokens=4000, tools=tools, messages=messages
    )
    # 서버측 웹검색 루프가 길면 pause_turn으로 멈출 수 있음 → 이어서 재요청
    guard = 0
    while resp.stop_reason == "pause_turn" and guard < 5:
        guard += 1
        messages.append({"role": "assistant", "content": resp.content})
        resp = client.messages.create(
            model=model, max_tokens=4000, tools=tools, messages=messages
        )

    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    if not text:
        raise RuntimeError("빈 응답")
    return text


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {result}")


def kst_today() -> str:
    # GitHub Actions는 UTC. KST(+9)로 변환해 날짜 문자열 생성.
    now_kst = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    wd = ["월", "화", "수", "목", "금", "토", "일"][now_kst.weekday()]
    return f"{now_kst.month}/{now_kst.day}({wd})"


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.stderr.write("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 시크릿이 필요합니다.\n")
        return 1

    today = kst_today()
    message = build_message(today)
    send_telegram(token, chat_id, message)
    print(f"발송 완료: {today} ({len(message)}자)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
