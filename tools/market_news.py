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
import logging

LOG = logging.getLogger(__name__)
from seoul_fx import fetch_night_close
from market_sessions import session_target

# Publisher-owned RSS endpoints, listed at https://www.mk.co.kr/rss.
FEEDS = [
    ('매일경제', 'https://www.mk.co.kr/rss/50200011/'),
    ('매일경제', 'https://www.mk.co.kr/rss/30100041/'),
    ('매일경제', 'https://www.mk.co.kr/rss/30300018/'),
    ('한국경제', 'https://www.hankyung.com/feed/finance'),
    # Official list: https://news.einfomax.co.kr/rssIndex.html
    ('연합인포맥스', 'https://news.einfomax.co.kr/rss/S1N23.xml'),
    ('연합인포맥스', 'https://news.einfomax.co.kr/rss/S1N21.xml'),
    ('연합인포맥스', 'https://news.einfomax.co.kr/rss/S1N16.xml'),
    ('연합인포맥스', 'https://news.einfomax.co.kr/rss/S1N9.xml'),
]
HOSTS = {'mk.co.kr', 'www.mk.co.kr', 'stock.mk.co.kr', 'www.hankyung.com', 'news.einfomax.co.kr'}
GROUPS = {
    '미국': r'뉴욕증시|미국\s*증시|나스닥|S&P\s*500|다우(?:존스)?',
    '한국': r'코스피|코스닥|국내\s*증시',
    '아시아·유럽': r'유럽\s*증시|亞증시|아시아\s*증시|닛케이|항셍|일본\s*증시|중국\s*증시|DAX|FTSE',
    '환율·원자재': r'원[·/ㆍ]?달러|환율|국제유가|뉴욕유가|뉴욕금|금값|WTI',
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
            # MK publishes +09:00; email.utils treats that RFC variant as naive.
            stamp = re.sub(r'([+-]\d{2}):(\d{2})$', r'\1\2', item.findtext('pubDate', '').strip())
            url = item.findtext('link', '').strip()
            if (source == '연합인포맥스' and urlparse(url).hostname == 'news.einfomax.co.kr'
                    and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', stamp)):
                # This publisher's official Korean feed uses its KST wall time.
                published = datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('Asia/Seoul'))
            else:
                published = parsedate_to_datetime(stamp)
            if published.tzinfo is None or not safe_link(url):
                LOG.info('feed_item rejected=timezone_or_link url=%s published=%s', url, item.findtext('pubDate', ''))
                continue
            if not now - timedelta(hours=72) <= published <= now:
                LOG.info('feed_item rejected=publication_window url=%s published=%s', url, published.isoformat())
                continue
            result.append({'title': plain(item.findtext('title')), 'url': url,
                           'published': published, 'source': source,
                           'description': plain(item.findtext('description')), 'body': ''})
        except (ValueError, TypeError, OverflowError):
            LOG.info('feed_item rejected=invalid_date published=%s link=%s', item.findtext('pubDate', ''), item.findtext('link', ''))
            continue
    return result


def body_text(raw):
    """Keep paragraph boundaries; subtitles must not swallow the lead sentence."""
    soup = BeautifulSoup(raw, 'html.parser')
    for node in soup.select('script, style, figure, figcaption'):
        node.decompose()
    for br in soup.find_all('br'):
        br.replace_with('\n')
    for block in soup.find_all(['p', 'h2', 'h3']):
        block.insert_before('\n')
        block.insert_after('\n')
    return '\n'.join(' '.join(line.split()) for line in soup.get_text(' ').splitlines() if line.strip())


def sentences(text):
    return re.split(r'(?<=[.!?])\s+|\n+', text)


def article_body(raw):
    # Decode strictly: never silently accept replacement characters as evidence.
    if isinstance(raw, bytes):
        try:
            raw = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raw = raw.decode('cp949')
    if '\ufffd' in raw:
        raise ValueError('replacement characters in article')
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
                        return body_text(candidate['articleBody'])
        except (ValueError, TypeError):
            continue
    # Publisher article containers only. Do not treat the entire page as article text.
    node = soup.select_one('.news_cnt_detail_wrap, #articletxt, #article-body, #article-view-content-div')
    return body_text(str(node)) if node else ''


# Symbol-specific evidence. Broad phrases such as "world stocks" cannot prove
# the direction of an individual index, so only explicit instruments qualify.
INSTRUMENTS = {
    '^GSPC': ('미국', r'(?:스탠더드앤드푸어스\()?S&P\)?\s*500', 'America/New_York'),
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
LABELS = {'^GSPC':'S&P500', '^IXIC':'나스닥', '^DJI':'다우', '^KS11':'코스피', '^KQ11':'코스닥',
                 '^N225':'닛케이225', '^HSI':'항셍', '^GDAXI':'DAX', '^FTSE':'FTSE100', 'CL=F':'WTI', 'GC=F':'금선물'}

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


# A separate, less brittle path verifies market facts, not a causal claim.
EVENT_DATE = re.compile(r'(?:(?P<year>\d{4})년\s*)?(?:(?P<month>\d{1,2})월\s*)?(?P<day>\d{1,2})일')
CLOSE = r'(?:마감(?:했다|하였다|했습니다)|(?:거래를|장을)\s*마쳤다)'
UNSAFE_EVENT = r'지난|전날|어제|내일|다음|전망|예상|추정|가능|가정|만약|장중|하지\s*않|못했다'
NUMBER = r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?'


def matching_date(text, article, symbol, target):
    dates = list(EVENT_DATE.finditer(text))
    if len(dates) != 1:
        return False
    event = dates[0]
    published = article['published'].astimezone(ZoneInfo(INSTRUMENTS[symbol][2])).date()
    if not event['month'] and (published.year, published.month) != (target.year, target.month):
        return False
    if not event['year'] and published.year != target.year:
        return False
    try:
        return date(int(event['year'] or target.year), int(event['month'] or target.month), int(event['day'])) == target
    except ValueError:
        return False


def market_evidence(article, symbol, quote, now):
    """Match date + individual instrument + closing level + rounded change.

    '이날' can refer to an explicit dated market lead at most five sentences
    earlier, with no intervening different date. Multi-index close sentences
    are split at instrument boundaries before checking numbers/direction.
    Returns a concise factual summary, never stitches a cause onto the close.
    """
    if symbol not in INSTRUMENTS or not quote:
        return None, 'quote_missing'
    if not publication_matches(article, quote, symbol, now):
        return None, 'publication_window'
    body = article.get('body', '')
    if not body or re.sub(r'\W', '', body) == re.sub(r'\W', '', article['title']):
        return None, 'body_missing_or_headline_only'
    try:
        target = date.fromisoformat(quote['date'])
        price, change = float(quote['price']), float(quote['change_pct'])
        if not all(math.isfinite(n) for n in (price, change)) or price <= 0:
            return None, 'quote_invalid'
    except (KeyError, ValueError, TypeError):
        return None, 'quote_invalid'
    sentences = re.split(r'(?<=[.!?])\s+|\n+', body)
    reason = 'instrument_or_completed_close_missing'
    for i, sentence in enumerate(sentences):
        match = re.search(INSTRUMENTS[symbol][1], sentence)
        if not match or not re.search(CLOSE + r'\.?$', sentence):
            continue
        if re.search(UNSAFE_EVENT, sentence):
            reason = 'forecast_history_or_intraday'
            continue
        dated = matching_date(sentence, article, symbol, target)
        continuation = (i > 0 and re.search(CLOSE + r'\.?$', sentences[i-1])
                        and re.search(GROUPS[INSTRUMENTS[symbol][0]], sentences[i-1]))
        if not EVENT_DATE.search(sentence) and (sentence.startswith('이날') or continuation):
            # Do not carry a date across a different dated event or a retrospective.
            for lead in reversed(sentences[max(0, i-5):i]):
                if EVENT_DATE.search(lead):
                    dated = (not re.search(UNSAFE_EVENT, lead)
                             and bool(re.search(GROUPS[INSTRUMENTS[symbol][0]], lead))
                             and matching_date(lead, article, symbol, target))
                    break
        if not dated:
            reason = 'event_date_missing_or_mismatch'
            continue
        clause = sentence[match.end():]
        ends = [m.start() for other, spec in INSTRUMENTS.items() if other != symbol
                for m in [re.search(spec[1], clause)] if m]
        if ends:
            clause = clause[:min(ends)]
        # Require the final level attached to 에/로, not just a change amount.
        levels = []
        for m in re.finditer(rf'(?:(?P<man>\d+)만)?(?P<n>{NUMBER})(?:포인트|달러)?(?:에|로)', clause):
            levels.append(int(m['man'] or 0) * 10000 + float(m['n'].replace(',', '')))
        percentages = [float(n) for n in re.findall(r'(\d+(?:\.\d+)?)%', clause)]
        directions = set(1 if m in {'상승', '급등', '반등', '오른'} else -1 if m in {'하락', '급락', '내린'} else 0
                         for m in re.findall(r'상승|급등|반등|오른|하락|급락|내린|보합', clause))
        if not levels or any(round(n, 2) != round(price, 2) for n in levels):
            reason = 'close_level_mismatch'
            continue
        if not percentages or any(round(n, 2) != round(abs(change), 2) for n in percentages):
            reason = 'change_percent_mismatch'
            continue
        if directions != {(change > 0) - (change < 0)}:
            reason = 'direction_mismatch'
            continue
        label = LABELS[symbol]
        return f'{label} {price:,.2f} ({change:+.2f}%) 마감', 'accepted_market_facts'
    return None, reason


def body_news(article, group, target):
    """A short attributed body excerpt, kept separate from price causality.

    Only the lead of a session-verified report is eligible. No headline is used
    as a substitute for body analysis. Forecasts/history/another event date are
    omitted; inability to find a safe excerpt is explicit in diagnostics.
    """
    body = re.sub(r'^.*?기자\s*=\s*', '', article.get('body', ''), count=1)
    economic = r'금리|반도체|소비주|물가|인플레|재고|원유|달러|엔화|실적|투자|매도|매수|무역|중국|유가|금값'
    for sentence in sentences(body)[:10]:
        if not 15 <= len(sentence) <= 190 or len(sentence.split()) > 25:
            continue
        if re.search(UNSAFE_EVENT, sentence):
            continue
        dates = list(EVENT_DATE.finditer(sentence))
        if dates and not matching_date(sentence, article, next(sym for sym, spec in INSTRUMENTS.items() if spec[0] == group), date.fromisoformat(target)):
            continue
        if re.search(CAUSE + r'|감소|증가|급감|줄었|늘었|발표|완화|공포|경계심|방아쇠', sentence) and re.search(economic, sentence):
            # Avoid unrelated topics: either a market noun or the group's own
            # instrument must appear in the short statement.
            if re.search(GROUPS[group] + r'|증시|주가|시장|투자심리|거래|원유|유가|금리|재고', sentence):
                return sentence
    return None


def report_targets(now, quotes):
    """News session dates are independent of conservative futures quote dates."""
    us_day, _ = session_target('US', now, morning=True)
    kr_day, _ = session_target('KR', now, morning=True)
    return {'미국': {us_day}, '한국': {kr_day}, '환율·원자재': {us_day},
            '아시아·유럽': {q['date'] for sym, q in quotes.items() if q and sym in INSTRUMENTS
                            and INSTRUMENTS[sym][0] == '아시아·유럽'}}


def session_report(article, group, targets, now):
    """Verify a dated completed report; do NOT assert a cause of a different quote.

    A report may cover a newer commodity session than the conservative Yahoo
    snapshot. Its own session is displayed explicitly and never overwrites prices.
    """
    body = re.sub(r'^.*?기자\s*=\s*', '', article.get('body', ''), count=1)
    if not body or not re.search(GROUPS[group], body):
        return None, 'session_body_missing_or_topic'
    sentences = re.split(r'(?<=[.!?])\s+|\n+', body)
    symbols = [sym for sym, spec in INSTRUMENTS.items() if spec[0] == group]
    for target in sorted(targets, reverse=True):
        for symbol in symbols:
            if not publication_matches(article, {'date':target}, symbol, now):
                continue
            for i, anchor in enumerate(sentences[:12]):
                if (re.search(UNSAFE_EVENT, anchor)
                        or not matching_date(anchor, article, symbol, date.fromisoformat(target))):
                    continue
                # A dated sentence plus up to two following sentences constitutes
                # the close report. Another explicit date breaks the association.
                for j in range(i, min(i+3, len(sentences))):
                    sentence=sentences[j]
                    if j > i and EVENT_DATE.search(sentence):
                        break
                    if re.search(UNSAFE_EVENT, sentence):
                        continue
                    if re.search(GROUPS[group], sentence) and re.search(CLOSE, sentence):
                        summary=body_news(article,group,target)
                        if summary:
                            return {'date':target,'summary':summary}, 'accepted_session_body_news'
                        return None, 'session_body_summary_missing'
    return None, 'session_date_or_completed_event_missing'


def news_excerpt(article, symbol, quote, now):
    cause = reason_excerpt(article, symbol, quote, now)
    if cause:
        return f'원인 보도: “{cause}”', 'accepted_cause'
    facts, reason = market_evidence(article, symbol, quote, now)
    if facts:
        # Title is attributed as news, explicitly separate from verified facts.
        title = article['title']
        summary = body_news(article, INSTRUMENTS[symbol][0], quote['date'])
        if summary:
            return f'마감 동향: {facts}\n  주요 보도(본문): “{summary}”', 'accepted_body_news'
        headline = f'\n  기사 제목(본문 요약 미확보): {title}' if len(title) <= 100 else ''
        return f'마감 동향: {facts}{headline}', reason
    return None, reason


def collect_articles(get, now, quotes=None):
    quotes = quotes or {}
    targets = report_targets(now, quotes)
    articles = {}
    for source, url in FEEDS:
        try:
            raw = get(url)
            parsed = parse_feed(raw, source, now)
            LOG.info('feed url=%s items=%d eligible=%d', url, len(ET.fromstring(raw).findall('.//item')), len(parsed))
            for article in parsed:
                articles.setdefault(article['url'], article)
        except (OSError, ValueError, ET.ParseError) as exc:
            LOG.warning('feed url=%s rejected=fetch_or_parse error=%s', url, type(exc).__name__)
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
        report_groups = [group for group in GROUPS if targets[group] and counts[group] < 3
                         and re.search(GROUPS[group], article['title'] + ' ' + article['description'])]
        if not candidates and not fx_candidate and not report_groups:
            LOG.info('article url=%s rejected=no_quote_window_topic_or_quota', article['url'])
            continue
        try:
            article['body'] = article_body(get(article['url']))
            LOG.info('article url=%s body_chars=%d', article['url'], len(article['body']))
        except (OSError, ValueError) as exc:
            LOG.warning('article url=%s rejected=body_fetch_or_decode error=%s', article['url'], type(exc).__name__)
        groups = set()
        for sym in candidates:
            excerpt, reason = news_excerpt(article, sym, quotes[sym], now)
            LOG.info('article url=%s symbol=%s result=%s', article['url'], sym, reason)
            if excerpt:
                groups.add(INSTRUMENTS[sym][0])
        for group in report_groups:
            report, reason = session_report(article, group, targets[group], now)
            LOG.info('article url=%s group=%s result=%s', article['url'], group, reason)
            if report:
                groups.add(group)
        if groups or (fx_candidate and fetch_night_close([article], now)):
            selected.append(article)
            for group in groups:
                counts[group] += 1
    LOG.info('collection unique=%d selected=%d groups=%s', len(articles), len(selected), counts)
    return selected


def build_news_section(articles, now, quotes=None, budget=2200):
    quotes = quotes or {}
    lines = ['📰 시장 움직임의 배경 — 검증된 마감 동향과 주요 보도 (발행시각 KST)']
    targets = report_targets(now, quotes)
    for group in GROUPS:
        options = []
        for article in articles:
            for symbol, quote in quotes.items():
                if symbol not in INSTRUMENTS or INSTRUMENTS[symbol][0] != group:
                    continue
                excerpt, reason = news_excerpt(article, symbol, quote, now)
                if excerpt:
                    rank = 0 if reason in {'accepted_cause', 'accepted_body_news'} else 2
                    options.append((rank, article, excerpt, quote['date']))
            report, _ = session_report(article, group, targets[group], now)
            if report:
                dates = {q['date'] for sym,q in quotes.items() if q and sym in INSTRUMENTS
                         and INSTRUMENTS[sym][0] == group}
                distinction = ' · 상단 시세와 별도 세션' if report['date'] not in dates else ''
                excerpt = f'주요 보도(본문, 기사 세션 {report["date"]}{distinction}): “{report["summary"]}”'
                options.append((1, article, excerpt, report['date']))
        best = min(options, key=lambda item: item[0]) if options else None
        found = best[1:] if best else None
        if not found:
            # The verified quote snapshot remains useful even if publishers fail.
            # Clearly label this as prices, never as sourced causal news.
            trends = []
            for sym, q in quotes.items():
                if sym not in INSTRUMENTS or INSTRUMENTS[sym][0] != group or not q:
                    continue
                try:
                    price, change = float(q['price']), float(q['change_pct'])
                    if price > 0 and math.isfinite(price) and math.isfinite(change):
                        name = LABELS[sym]
                        trends.append(f"{name} {price:,.2f} ({change:+.2f}%, {q['date']})")
                except (KeyError, TypeError, ValueError):
                    continue
            if trends:
                line = f'• {group}: 시세 동향 — ' + '; '.join(trends[:2]) + '\n  해당 세션 기사 본문 근거 미확보'
                if len(('\n'.join(lines) + line).encode('utf-16-le')) // 2 <= budget - 150:
                    lines.append(line)
                else:
                    lines.append(f'• {group}: 기사 본문 미확보 (시세는 상단 참조)')
            else:
                lines.append(f'• {group}: 시세와 일치하는 원인 기사 미확인')
            continue
        article, excerpt, target = found
        stamp = article['published'].astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M')
        line = f'• {group} ({target}): {excerpt}\n  {article["source"]} · {stamp}\n  {article["url"]}'
        if len(('\n'.join(lines) + line).encode('utf-16-le')) // 2 > budget - 150:
            lines.append(f'• {group}: 기사 표시 분량 초과')
        else:
            lines.append(line)
    return '\n'.join(lines)
