"""Key-free publisher RSS and short attributed excerpts, without generated causes."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import json
import re
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from market_sessions import session_target

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
MOVE = r'상승|하락|급등|급락|강세|약세|반등|내렸|올랐|마감'
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


def collect_articles(get, now):
    articles = {}
    for source, url in FEEDS:
        try:
            for article in parse_feed(get(url), source, now):
                articles.setdefault(article['url'], article)
        except (OSError, ValueError, ET.ParseError):
            continue
    ordered = sorted(articles.values(), key=lambda a: a['published'], reverse=True)
    # Fetch a bounded set, reserving candidates for every market and overnight FX.
    selected = []
    for pattern in list(GROUPS.values()) + [r'환율.*(?:6시|7시|06:00|07:00|야간)|(?:6시|7시|야간).*환율']:
        candidates = [a for a in ordered if re.search(pattern, a['title'])][:3]
        for article in candidates:
            if article not in selected:
                selected.append(article)
    for article in selected:
        try:
            article['body'] = article_body(get(article['url']))
        except (OSError, ValueError):
            pass
    return selected


def reason_excerpt(article, pattern):
    # Keep a source sentence intact; do not infer causation from prices or join clauses.
    text = article['body'] or article['description']
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        if (re.search(pattern, sentence) and re.search(MOVE, sentence)
                and re.search(CAUSE, sentence) and len(sentence) <= 240
                and len(sentence.split()) <= 25):
            return sentence
    return None


def build_news_section(articles, now, budget=2200):
    lines = ['📰 시장 움직임의 배경 — 기사 발췌 (발행시각 KST)']
    used = set()
    for group, pattern in GROUPS.items():
        found = None
        for article in articles:
            market = 'KR' if group == '한국' else 'US'
            target, _ = session_target(market, now, morning=True)
            tz = ZoneInfo('Asia/Seoul' if market == 'KR' else 'America/New_York')
            start = datetime.fromisoformat(target).replace(tzinfo=tz)
            end = min(now, start + timedelta(days=1, hours=9))
            if not start <= article['published'] <= end:
                continue
            if article['url'] in used:
                continue
            excerpt = reason_excerpt(article, pattern)
            if excerpt:
                found = (article, excerpt)
                break
        if not found:
            lines.append(f'• {group}: 확인 가능한 원인 기사 없음')
            continue
        article, excerpt = found
        used.add(article['url'])
        stamp = article['published'].astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M')
        line = f'• {group}: “{excerpt}”\n  {article["source"]} · {stamp}\n  {article["url"]}'
        if len(('\n'.join(lines) + line).encode('utf-16-le')) // 2 > budget - 150:
            lines.append(f'• {group}: 기사 표시 분량 초과')
        else:
            lines.append(line)
    return '\n'.join(lines)
