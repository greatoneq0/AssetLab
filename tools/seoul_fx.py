"""Seoul overnight close from explicitly timestamped publisher closing reports.

Since 2026-07-06 the market runs continuously Mon 06:00 to Sat 06:00
(07:00 outside US DST). A daily boundary is NOT the 15:30 reference close.
See docs/market-brief-sources.md. No Yahoo, MAR, NDF or daytime fallback.
"""
from datetime import datetime, time, timedelta, date
import re
from zoneinfo import ZoneInfo
from market_sessions import valid_price

KST = ZoneInfo('Asia/Seoul')
NY = ZoneInfo('America/New_York')


def expected_session(now):
    if now.tzinfo is None:
        raise ValueError('Timezone-aware datetime required')
    day = now.astimezone(KST).date() - timedelta(days=1)
    for _ in range(10):
        if day < date(2026, 7, 6):
            return None  # Do not extrapolate the current system to historical sessions.
        if day.weekday() < 5 and (day.month, day.day) != (1, 1):
            # New York 17:00 is Seoul next-calendar-day 06:00/07:00.
            close = datetime.combine(day, time(17), NY).astimezone(KST)
            if (day.month, day.day) == (12, 31):
                return None  # Special year-end cutoff: require separate verified reporting.
            if close + timedelta(minutes=20) <= now:
                return day.isoformat(), close
        day -= timedelta(days=1)
    return None


def fetch_night_close(articles, now):
    expected = expected_session(now)
    if expected is None:
        return None
    trading_day, close = expected
    matches = []
    for article in articles:
        published = article['published'].astimezone(KST)
        if not close <= published <= min(now, close + timedelta(hours=12)):
            continue
        # Require the full publisher body, a same-day explicitly timed Seoul close,
        # and a final level immediately attached to the closing verb.
        body = article.get('body', '')
        for sentence in re.split(r'(?<=[.!?])\s+', body):
            if len(sentence) > 500:
                continue
            # Full-clause grammar: the timestamp, instrument and *completed*
            # close must belong to one clause. Never take the first number in a
            # sentence containing multiple daytime/overnight closing predicates.
            number = r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?'
            comparison = (rf'(?:전\s*거래일\s*주간거래\s*종가'
                          rf'\(오후\s*3시\s*30분\)보다\s*{number}원\s*'
                          rf'(?:내린|오른)\s*)?')
            clause = (rf'{close.day}일\s+(?:한국시간\s+)?오전\s*{close.hour}시\s*'
                      rf'(?:기준\s*)?서울\s*외환시장에서\s*원[·/ㆍ]?달러\s*환율은\s*'
                      rf'{comparison}(?P<price>{number})원에\s*(?:거래를\s*)?'
                      rf'마감(?:했다|하였다|됐습니다|했습니다)\.?')
            value = re.fullmatch(clause, sentence.strip())
            if value and valid_price(value['price'].replace(',', '')):
                matches.append({'price': float(value['price'].replace(',', '')),
                                'trading_date': trading_day, 'closed_at': close,
                                'source': article['source'], 'url': article['url']})
    if not matches or len({q['price'] for q in matches}) != 1:
        return None
    return matches[0]


def build_fx_section(articles, now):
    expected = expected_session(now)
    q = fetch_night_close(articles, now)
    if q:
        return (f'💱 원/달러: {q["price"]:,.2f}원 — 서울 야간 종가(일일 경계)\n'
                f'  거래일 {q["trading_date"]} / 마감 {q["closed_at"]:%Y-%m-%d %H:%M} KST\n'
                f'  {q["source"]} 마감 보도: {q["url"]}')
    details = (f'거래일 {expected[0]} / 마감 {expected[1]:%Y-%m-%d %H:%M} KST'
               if expected else '특별 거래일의 마감 기준 확인 필요')
    return f'💱 원/달러: 서울 야간 종가 확인 불가\n  {details}\n  해당 시각·가격이 명시된 마감 보도 미확보'
