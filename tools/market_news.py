"""Key-free publisher RSS and short attributed excerpts, without generated causes."""
from datetime import datetime, timedelta, date
from email.utils import parsedate_to_datetime
from html import unescape
import json
import re
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import math
from seoul_fx import fetch_night_close

# Publisher-owned RSS endpoints, listed at https://www.mk.co.kr/rss.
FEEDS = [
    ('매일경제', 'https://www.mk.co.kr/rss/50200011/'),
    ('매일경제', 'https://www.mk.co.kr/rss/30100041/'),
    ('매일경제', 'https://www.mk.co.kr/rss/30300018/'),
    ('한국경제', 'https://www.hankyung.com/feed/finance'),
]
HOSTS = {'mk.co.kr', 'www.mk.co.kr', 'stock.mk.co.kr', 'www.hankyung.com'}
GROUPS = {
    '미국': r'뉴욕증시|미국\s*증시|나스닥|S&P\s*500|다우지수',
    '한국': r'코스피|코스닥|국내\s*증시',
    '아시아·유럽': r'유럽\s*증시|닛케이|항셍|일본\s*증시|중국\s*증시|DAX|FTSE',
    '환율·원자재': r'원[·/ㆍ]?달러|환율|국제유가|금값|WTI',
}
CAUSE = r'영향|여파|때문|힘입|부담|우려|기대|호재|악재|견인|끌어|압박|탓|따라'


def plain(text):
    return ' '.join(BeautifulSoup(unescape(text or ''), 'html.parser').get_text(' ').split())


def safe_link(url):
    parsed = urlparse(url)
    return len(url) <= 500 and parsed.scheme == 'https' and parsed.hostname in HOSTS and not parsed.username


def parse_feed(raw, source, now):
    result = []
    root = ET.fromstring(raw)
    for item in root.findall('.//item'):
        try:
            published = parsedate_to_datetime(item.findtext('pubDate', ''))
            url = item.findtext('link', '').strip()
            if published.tzinfo is None or not safe_link(url):
                continue
            if not now - timedelta(hours=72) <= published <= now:
                continue
            result.append({'title': plain(item.findtext('title')), 'url': url,
                           'published': published, 'source': source,
                           'description': plain(item.findtext('description')), 'body': ''})
        except (ValueError, TypeError, OverflowError):
            continue
    return result


def article_body(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    # Prefer the publisher's structured article body, never navigation/search snippets.
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            nodes = json.loads(script.string or '')
            nodes = nodes if isinstance(nodes, list) else [nodes]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                for candidate in [node] + node.get('@graph', []):
                    if isinstance(candidate, dict) and candidate.get('articleBody'):
                        return plain(candidate['articleBody'])
        except (ValueError, TypeError):
            continue
    # Publisher article containers only. Do not treat the entire page as article text.
    node = soup.select_one('.news_cnt_detail_wrap, #articletxt, #article-body')
    return plain(str(node)) if node else ''


# Symbol-specific evidence. Broad phrases such as "world stocks" cannot prove
# the direction of an individual index, so only explicit instruments qualify.
INSTRUMENTS = {
    '^GSPC': ('미국', r'S&P\s*500', 'America/New_York'),
    '^IXIC': ('미국', r'나스닥', 'America/New_York'),
    '^DJI': ('미국', r'다우(?:존스)?', 'America/New_York'),
    '^KS11': ('한국', r'코스피', 'Asia/Seoul'),
    '^KQ11': ('한국', r'코스닥', 'Asia/Seoul'),
    '^N225': ('아시아·유럽', r'닛케이(?:225)?', 'Asia/Tokyo'),
    '^HSI': ('아시아·유럽', r'항셍', 'Asia/Hong_Kong'),
    '^GDAXI': ('아시아·유럽', r'DAX', 'Europe/Berlin'),
    '^FTSE': ('아시아·유럽', r'FTSE\s*100', 'Europe/London'),
    'CL=F': ('환율·원자재', r'WTI', 'America/New_York'),
    'GC=F': ('환율·원자재', r'금(?:값|선물)', 'America/New_York'),
}
UNVERIFIED = r'지난|전날|어제|내일|다음|전망|예상|추정|가능|가정|만약|장중'
DIRECTION = {'상승': 1, '급등': 1, '반등': 1, '하락': -1, '급락': -1, '보합': 0}


def publication_matches(article, quote, symbol, now):
    try:
        tz = ZoneInfo(quote.get('timezone') or INSTRUMENTS[symbol][2])
        start = datetime.fromisoformat(quote['date']).replace(tzinfo=tz)
        return start <= article['published'] <= min(now, start + timedelta(days=1, hours=9))
    except (KeyError, ValueError, TypeError):
        return False


def reason_excerpt(article, symbol, quote=None, now=None):
    # No quote => no verified session or direction => no asserted cause.
    if symbol not in INSTRUMENTS or not quote or now is None:
        return None
    if not publication_matches(article, quote, symbol, now):
        return None
    try:
        target = date.fromisoformat(quote['date'])
        change = float(quote['change_pct'])
        if not math.isfinite(change):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    text = article.get('body') or article.get('description', '')
    title = re.sub(r'\W', '', article['title'])
    normalized = re.sub(r'\W', '', text)
    if not normalized or (title and not normalized.replace(title, '')):
        return None  # RSS descriptions repeating a headline are not body evidence.
    pattern = INSTRUMENTS[symbol][1]
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        if (len(sentence) > 240 or len(sentence.split()) > 25
                or re.search(UNVERIFIED, sentence) or not re.search(CAUSE, sentence)):
            continue
        # An explicit event date and exact instrument must start the same sentence.
        event = re.match(r'(?:(?P<year>\d{4})년\s*)?(?:(?P<month>\d{1,2})월\s*)?'
                         r'(?P<day>\d{1,2})일(?:\s*\(현지시간\))?\s+' + pattern
                         + r'(?:\s*지수)?(?:는|은|가|이)\s+', sentence)
        if not event:
            continue
        # A bare day number is resolved only within the verified session month;
        # cross-month publication requires an explicit month (and year if needed).
        published = article['published'].astimezone(ZoneInfo(INSTRUMENTS[symbol][2])).date()
        if not event['month'] and (published.year, published.month) != (target.year, target.month):
            continue
        if not event['year'] and published.year != target.year:
            continue
        try:
            reported = date(int(event['year'] or target.year), int(event['month'] or target.month), int(event['day']))
        except ValueError:
            continue
        if reported != target:
            continue
        # Ambiguous multi-instrument/multi-clause sentences are not summarized.
        tail = sentence[event.end():]
        if re.search(r'\d+일|\d+월|\d{4}년', tail):
            continue
        if any(re.search(other[1], tail) for other in INSTRUMENTS.values()):
            continue
        closes = list(re.finditer(r'(상승|급등|반등|하락|급락|보합)\s*(?:마감했다|마감하였다|마감했습니다)\.?$', tail))
        if len(closes) != 1 or re.search(r'마감|했지만|했고|반면|그러나|지만', tail[:closes[0].start()]):
            continue
        direction = (change > 0) - (change < 0)
        if DIRECTION[closes[0].group(1)] == direction:
            return sentence
    return None


def collect_articles(get, now, quotes=None):
    quotes = quotes or {}
    articles = {}
    for source, url in FEEDS:
        try:
            for article in parse_feed(get(url), source, now):
                articles.setdefault(article['url'], article)
        except (OSError, ValueError, ET.ParseError):
            continue
    ordered = sorted(articles.values(), key=lambda a: a['published'], reverse=True)
    selected = []
    counts = {group: 0 for group in GROUPS}
    for article in ordered:
        # Time filtering happens before fetching; the three-article quota is
        # consumed only AFTER event date, instrument, direction and completion pass.
        candidates = [sym for sym, q in quotes.items() if q and sym in INSTRUMENTS
                      and counts[INSTRUMENTS[sym][0]] < 3
                      and publication_matches(article, q, sym, now)
                      and re.search(GROUPS[INSTRUMENTS[sym][0]], article['title'] + ' ' + article['description'])]
        fx_candidate = bool(re.search(r'환율.*(?:6시|7시|06:00|07:00|야간)|(?:6시|7시|야간).*환율', article['title']))
        if not candidates and not fx_candidate:
            continue
        try:
            article['body'] = article_body(get(article['url']))
        except (OSError, ValueError):
            pass
        groups = {INSTRUMENTS[sym][0] for sym in candidates if reason_excerpt(article, sym, quotes[sym], now)}
        if groups or (fx_candidate and fetch_night_close([article], now)):
            selected.append(article)
            for group in groups:
                counts[group] += 1
    return selected


def build_news_section(articles, now, quotes=None, budget=2200):
    quotes = quotes or {}
    lines = ['📰 시장 움직임의 배경 — 시세 기준일·방향 확인 기사 (발행시각 KST)']
    used = set()
    for group in GROUPS:
        found = None
        for article in articles:
            if article['url'] in used:
                continue
            for symbol, quote in quotes.items():
                if symbol not in INSTRUMENTS or INSTRUMENTS[symbol][0] != group:
                    continue
                excerpt = reason_excerpt(article, symbol, quote, now)
                if excerpt:
                    found = (article, excerpt, quote['date'])
                    break
            if found:
                break
        if not found:
            lines.append(f'• {group}: 시세와 일치하는 원인 기사 미확인')
            continue
        article, excerpt, target = found
        used.add(article['url'])
        stamp = article['published'].astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M')
        line = f'• {group} ({target}): “{excerpt}”\n  {article["source"]} · {stamp}\n  {article["url"]}'
        if len(('\n'.join(lines) + line).encode('utf-16-le')) // 2 > budget - 150:
            lines.append(f'• {group}: 기사 표시 분량 초과')
        else:
            lines.append(line)
    return '\n'.join(lines)
