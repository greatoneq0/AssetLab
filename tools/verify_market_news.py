#!/usr/bin/env python3
"""Read-only public-network probe. Never imports/calls Telegram delivery.

Known article prices are reference inputs, NOT an independent live quote check.
The separate live snapshot checks Yahoo/RSS availability and end-to-end selection.
Exit 2 when any reference page cannot be fetched/parsed/accepted; fixtures cannot
turn this check green. Raw HTML stays in CI artifacts, not repository history.
"""
import argparse
from datetime import datetime
import json
import logging
import re
from pathlib import Path
from bs4 import BeautifulSoup
from market_news import article_body, news_excerpt, build_news_section, collect_articles, GROUPS, INSTRUMENTS, report_targets, session_report
from market_brief import http_get, fetch_quote, SYMBOLS
from market_sessions import utc_now

REFERENCES = [
    ('https://www.mk.co.kr/news/stock/12177086', '매일경제', '^KS11', 5554.22, -7.57),
    ('https://www.hankyung.com/article/2026101652966', '한국경제', '^IXIC', 26713.03, .08),
    ('https://www.mk.co.kr/news/world/12177389', '매일경제', '^DJI', 52177.69, 1.08),
]


def metadata(raw):
    soup = BeautifulSoup(raw.decode('utf-8-sig'), 'html.parser')
    published = soup.select_one('meta[property="article:published_time"]')
    title = soup.select_one('meta[property="og:title"]')
    stamp = published.get('content') if published else None
    if not stamp:
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                nodes = json.loads(script.string or '')
                nodes = nodes if isinstance(nodes, list) else [nodes]
                for node in nodes:
                    if isinstance(node, dict):
                        for item in [node] + node.get('@graph', []):
                            if isinstance(item, dict) and item.get('datePublished'):
                                stamp = item['datePublished']
            except (ValueError, TypeError):
                pass
    if not stamp or not title:
        raise ValueError('publisher title/publication metadata missing')
    published = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    if published.tzinfo is None:
        raise ValueError('publisher publication timezone missing')
    return title['content'], published


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report-dir', default='/tmp/market-news-live')
    args = parser.parse_args()
    dest = Path(args.report_dir);dest.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(dest/'diagnostics.log')])
    reference_now = datetime.fromisoformat('2026-10-16T08:00:00+09:00')
    results, accepted, reference_quotes = [], [], {}
    for index, (url, source, symbol, price, change) in enumerate(REFERENCES):
        result = {'url':url, 'reference_symbol':symbol}
        try:
            raw = http_get(url)
            (dest/f'article-{index}.html').write_bytes(raw)
            title, published = metadata(raw)
            a = dict(url=url,source=source,title=title,published=published,
                     body=article_body(raw),description='')
            quote = dict(date='2026-10-15',price=price,change_pct=change)
            text, reason = news_excerpt(a,symbol,quote,reference_now)
            result.update(body_chars=len(a['body']), result=reason, accepted=bool(text))
            if text:
                accepted.append(a);reference_quotes[symbol]=quote
        except (OSError, ValueError) as exc:
            result.update(accepted=False, result='fetch_decode_or_metadata_failed', error=type(exc).__name__)
        results.append(result)
        print(json.dumps(result,ensure_ascii=False))
    example = build_news_section(accepted,reference_now,reference_quotes)
    (dest/'reference-output.txt').write_text('REFERENCE PRICES FROM REPORTS — NOT LIVE QUOTE VERIFICATION\n'+example)
    print(example)
    now = utc_now()
    quotes = {symbol:fetch_quote(symbol,now) for _,symbol,_ in SYMBOLS}
    print('LIVE_QUOTES ' + json.dumps(quotes, ensure_ascii=False))
    def record_get(url):
        raw = http_get(url)
        import hashlib
        suffix = '.xml' if '/rss/' in url or '/feed/' in url else '.html'
        (dest/('request-'+hashlib.sha256(url.encode()).hexdigest()[:12]+suffix)).write_bytes(raw)
        if 'einfomax.co.kr/news/' in url:
            body = article_body(raw)
            logging.info('probe_body url=%s dates=%s close_phrases=%s', url,
                         re.findall(r'.{0,15}\d{1,2}일.{0,45}', body)[:3],
                         re.findall(r'.{0,35}(?:마감|마쳤|끝났).{0,12}', body)[:3])
        return raw
    articles = collect_articles(record_get,now,quotes)
    live_output = build_news_section(articles,now,quotes)
    (dest/'live-output.txt').write_text(live_output)
    print('LIVE OUTPUT (independent Yahoo snapshot + RSS)\n' + live_output)
    targets = report_targets(now, quotes)
    coverage = {group: [] for group in GROUPS}
    for a in articles:
        for group in GROUPS:
            report, reason = session_report(a, group, targets[group], now)
            matching = [sym for sym,q in quotes.items() if q and sym in INSTRUMENTS
                        and INSTRUMENTS[sym][0] == group
                        and news_excerpt(a,sym,q,now)[1] in {'accepted_cause','accepted_body_news'}]
            if report or matching:
                coverage[group].append({'url':a['url'],'session':report['date'] if report else quotes[matching[0]]['date'],
                                        'kind':reason if report else 'accepted_body_news'})
    print('BODY_NEWS_COVERAGE ' + json.dumps(coverage,ensure_ascii=False))
    report = dict(checked_at=now.isoformat(),references=results,live_quotes=quotes,
                  body_news_coverage=coverage, all_groups_body_news=all(coverage.values()),
                  live_selected_urls=[a['url'] for a in articles],
                  live_collection_verified=bool(articles) and any(quotes.values()))
    (dest/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    # Distinguish reference parser integration from current feed/quote availability.
    return 0 if all(r['accepted'] for r in results) and report['live_collection_verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
