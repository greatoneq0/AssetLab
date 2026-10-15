#!/usr/bin/env python3
"""Dated market closes and attributed publisher news → Telegram.

Only TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required for delivery.
News uses public publisher RSS; --dry-run renders without sending.
"""

import datetime
from zoneinfo import ZoneInfo
from market_sessions import session_target, valid_price, utc_now
from market_news import collect_articles, build_news_section
from seoul_fx import build_fx_section
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
    ("WTI유가",   "CL=F",  2),
    ("금",        "GC=F",  2),
]


def http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, application/rss+xml, text/html, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_quote(symbol: str, now=None):
    """Use dated daily closes, never regularMarketPrice or range-start close."""
    now = now or utc_now()
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol) + "?range=3mo&interval=1d"
    )
    try:
        result = json.loads(http_get(url))["chart"]["result"][0]
        tz = ZoneInfo(result["meta"]["exchangeTimezoneName"])
        bars = {}
        closes = result["indicators"]["quote"][0]["close"]
        for stamp, price in zip(result["timestamp"], closes):
            if stamp is not None and valid_price(price):
                day = datetime.datetime.fromtimestamp(stamp, tz).date().isoformat()
                bars[day] = float(price)
        market = "KR" if symbol in {"^KS11", "^KQ11"} else (
            "US" if symbol in {"^GSPC", "^IXIC", "^DJI"} else None)
        if market:
            target, previous = session_target(market, now, morning=True)
            if target not in bars or previous not in bars:
                return None  # Never silently substitute a stale cached bar.
        else:
            # Other exchanges/instruments: conservatively exclude today's live bar.
            cutoff = now.astimezone(tz).date().isoformat()
            days = sorted(day for day in bars if day < cutoff)
            if len(days) < 2:
                return None
            previous, target = days[-2:]
        price, prev = bars[target], bars[previous]
        return {"price": price, "prev": prev, "change_pct": (price-prev)/prev*100,
                "date": target, "previous_date": previous}
    except (urllib.error.URLError, KeyError, IndexError, ValueError, TypeError):
        return None


def fmt_num(x: float, decimals: int) -> str:
    return f"{x:,.{decimals}f}"


def build_numbers_section(now=None) -> str:
    now = now or utc_now()
    common_date, _ = session_target("KR", now, morning=True)
    lines = [f"🌐 기준일 {common_date} · 각 시장 현지 종가 (예외는 별도 표기)"]
    for name, sym, dec in SYMBOLS:
        q = fetch_quote(sym, now)
        if not q:
            lines.append(f"• {name}: 확정 종가 조회 실패")
            continue
        arrow = "🔺" if q["change_pct"] > 0 else ("🔻" if q["change_pct"] < 0 else "▪️")
        sign = "+" if q["change_pct"] >= 0 else ""
        exception = f" · {q['date']} 현지 종가" if q['date'] != common_date else ""
        if sym in {"CL=F", "GC=F"}:
            exception += " · Yahoo 선물 일봉 종가"
        lines.append(
            f"• {name}: {fmt_num(q['price'], dec)} "
            f"({arrow}{sign}{q['change_pct']:.2f}%){exception}"
        )
    return "\n".join(lines)


def build_message(today_kst: str, now=None) -> str:
    now = now or utc_now()
    numbers = build_numbers_section(now)
    articles = collect_articles(http_get, now)
    fx = build_fx_section(articles, now)
    prefix = f"📊 {today_kst} 세계 증시 브리핑\n\n{numbers}\n\n{fx}\n\n"
    news = build_news_section(articles, now, budget=3900 - len(prefix.encode('utf-16-le')) // 2)
    return prefix + news + "\n\n※ 투자 참고용이며 매매 판단은 본인 책임입니다."


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    if not result.get("ok"):
        raise RuntimeError("텔레그램 전송 실패")


def kst_today() -> str:
    # GitHub Actions는 UTC. KST(+9)로 변환해 날짜 문자열 생성.
    now_kst = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    wd = ["월", "화", "수", "목", "금", "토", "일"][now_kst.weekday()]
    return f"{now_kst.month}/{now_kst.day}({wd})"


def main() -> int:
    if "--dry-run" in sys.argv:
        print(build_message(kst_today()))
        return 0
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
