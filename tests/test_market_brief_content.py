import datetime as dt
import json
from pathlib import Path
import sys
from unittest.mock import Mock
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import market_brief as brief
import market_news as news
import seoul_fx as fx


def instant(s):
    return dt.datetime.fromisoformat(s)


NOW = instant('2026-10-15T08:03:00+09:00')
US_QUOTES = {'^IXIC': {'date': '2026-10-14', 'change_pct': 1.0}}
NEWS_GOOD = '14일(현지시간) 나스닥은 실적 개선 기대에 힘입어 상승 마감했다.'


def article(body='', published='2026-10-15T06:30:00+09:00', **extra):
    return dict(title='원·달러 환율 야간 마감', description='', body=body,
                published=instant(published), source='매일경제',
                url='https://www.mk.co.kr/news/economy/12345', **extra)


@pytest.mark.parametrize('now,day,close', [
    ('2026-10-15T08:03:00+09:00', '2026-10-14', '2026-10-15T06:00:00+09:00'),
    ('2026-10-15T17:00:00+09:00', '2026-10-14', '2026-10-15T06:00:00+09:00'),
    ('2026-10-15T06:19:00+09:00', '2026-10-13', '2026-10-14T06:00:00+09:00'),
    ('2026-10-12T08:03:00+09:00', '2026-10-09', '2026-10-10T06:00:00+09:00'), # KR stock holiday is FX session
    ('2026-10-18T08:03:00+09:00', '2026-10-16', '2026-10-17T06:00:00+09:00'),
    ('2026-11-03T08:03:00+09:00', '2026-11-02', '2026-11-03T07:00:00+09:00'),
])
def test_fx_session_dates(now, day, close):
    assert fx.expected_session(instant(now)) == (day, instant(close))


def test_fx_does_not_guess_unsupported_sessions():
    assert fx.expected_session(instant('2026-07-06T08:00:00+09:00')) is None
    assert fx.expected_session(instant('2027-01-02T08:00:00+09:00')) is None


def test_real_published_night_close_sentence():
    # Source: https://stock.mk.co.kr/news/view/1116606 (2026-07-07).
    body = ('7일 한국시간 오전 6시 기준 서울 외환시장에서 원·달러 환율은 전 거래일 '
            '주간거래 종가(오후 3시30분)보다 0.3원 내린 1530.0원에 거래를 마감했다.')
    a = article(body, '2026-07-07T07:00:00+09:00')
    q = fx.fetch_night_close([a], instant('2026-07-07T08:03:00+09:00'))
    assert q['price'] == 1530.0
    assert q['trading_date'] == '2026-07-06'
    assert q['closed_at'].isoformat() == '2026-07-07T06:00:00+09:00'
    assert '1530' not in fx.build_fx_section([a], NOW) # stale article not reused


GOOD = '15일 오전 6시 기준 서울 외환시장에서 원·달러 환율은 1,350.0원에 거래를 마감했다.'


@pytest.mark.parametrize('body,published', [
    (GOOD.replace('6시', '2시'), '2026-10-15T06:30:00+09:00'),
    (GOOD.replace('오전 6시', '오후 3시30분'), '2026-10-15T06:30:00+09:00'),
    (GOOD.replace('서울 외환시장', '역외 NDF 시장'), '2026-10-15T06:30:00+09:00'),
    (GOOD.replace('15일', '14일'), '2026-10-15T06:30:00+09:00'),
    (GOOD.replace('마감했다', '거래됐다'), '2026-10-15T06:30:00+09:00'),
    (GOOD, '2026-10-15T05:30:00+09:00'),
    (GOOD, '2026-10-15T09:00:00+09:00'), # future relative to NOW
    ('', '2026-10-15T06:30:00+09:00'),
])
def test_fx_rejects_unconfirmed_or_wrong_basis(body, published):
    assert fx.fetch_night_close([article(body, published)], NOW) is None


def test_fx_conflicting_reports_and_no_fallback():
    assert fx.fetch_night_close([article(GOOD), article(GOOD.replace('1,350.0','1,351.0'))],NOW) is None
    text = fx.build_fx_section([], NOW)
    assert '확인 불가' in text and '거래일 2026-10-14 / 마감 2026-10-15 06:00 KST' in text


def test_fx_success_has_both_dates_and_link():
    text = fx.build_fx_section([article(GOOD)], NOW)
    assert '1,350.00원' in text and '2026-10-14' in text and '2026-10-15 06:00' in text
    assert 'https://www.mk.co.kr/' in text and '%' not in text


def feed(pub='Wed, 14 Oct 2026 21:30:00 GMT', link='https://www.mk.co.kr/news/stock/123'):
    return f'''<rss><channel><item><title>뉴욕증시 상승</title><link>{link}</link>
    <pubDate>{pub}</pubDate><description><![CDATA[<b>뉴욕증시는 실적 개선 기대에 힘입어 상승했다.</b>]]></description></item></channel></rss>'''.encode()


def test_rss_dates_html_and_links():
    parsed = news.parse_feed(feed(),'매일경제',NOW)
    assert len(parsed)==1 and '<b>' not in parsed[0]['description']
    for pub in ['invalid', 'Wed, 14 Oct 2026 21:30:00', 'Wed, 01 Oct 2026 21:30:00 GMT', 'Fri, 16 Oct 2026 21:30:00 GMT']:
        assert news.parse_feed(feed(pub=pub),'매일경제',NOW)==[]
    for link in ['http://www.mk.co.kr/a','https://evil.example/a','https://www.mk.co.kr.evil.example/a']:
        assert news.parse_feed(feed(link=link),'매일경제',NOW)==[]


def test_article_extraction_ignores_navigation():
    assert news.article_body(b'<nav>fake cause</nav>')==''
    raw='<nav>fake cause</nav><script type="application/ld+json">'+json.dumps({'@type':'NewsArticle','articleBody':'확인된 본문'})+'</script>'
    assert news.article_body(raw.encode())=='확인된 본문'


def test_news_extracts_only_attributed_causes_and_date():
    a=article(NEWS_GOOD)
    result=news.build_news_section([a],NOW,US_QUOTES)
    assert a['body'] in result and a['url'] in result and '2026-10-15 06:30' in result
    assert '한국: 시세와 일치하는 원인 기사 미확인' in result
    a['body']='뉴욕증시는 상승했다.'
    assert '미국: 시세와 일치하는 원인 기사 미확인' in news.build_news_section([a],NOW,US_QUOTES)
    # Do not stitch an unrelated causal sentence onto a price sentence.
    a['body']='뉴욕증시는 상승했다. 비가 온 영향으로 야구 경기가 취소됐다.'
    assert news.reason_excerpt(a,'^IXIC',US_QUOTES['^IXIC'],NOW) is None


def test_news_excludes_today_intraday_kr_reason_for_yesterday_close():
    a=article('코스피는 금리 인상 우려로 하락했다.', '2026-10-15T14:00:00+09:00')
    assert '한국: 시세와 일치하는 원인 기사 미확인' in news.build_news_section([a],instant('2026-10-15T17:00:00+09:00'), {'^KS11': {'date':'2026-10-14','change_pct':-1}})


def test_news_partial_failure_and_deduplication(monkeypatch):
    monkeypatch.setattr(news,'FEEDS',[('매일경제','bad'),('매일경제','good'),('매일경제','duplicate')])
    def get(url):
        if url=='bad': raise OSError('offline')
        if url.startswith('https:'): return ('<div id="article-body">'+NEWS_GOOD+'</div>').encode()
        return feed().replace('뉴욕증시'.encode(),'나스닥'.encode())
    found=news.collect_articles(get,NOW,US_QUOTES)
    assert len(found)==1 and found[0]['body']==NEWS_GOOD
    assert news.collect_articles(lambda _:b'broken XML',NOW)==[]


def test_common_date_and_exceptions(monkeypatch):
    monkeypatch.setattr(brief,'SYMBOLS',[('코스피','^KS11',2),('코스닥','^KQ11',2),('항셍','^HSI',2),('금','GC=F',2)])
    monkeypatch.setattr(brief,'fetch_quote',lambda sym,*a:{'price':100,'change_pct':1,'date':'2026-10-13' if sym=='^HSI' else '2026-10-14'})
    output=brief.build_numbers_section(NOW)
    assert output.count('2026-10-14')==1
    assert '2026-10-13 현지 종가' in output
    assert 'Yahoo 선물 일봉 종가' in output
    assert all(sym!='KRW=X' for _,sym,_ in brief.SYMBOLS)


def test_message_key_free_and_send_free(monkeypatch):
    monkeypatch.setattr(brief,'fetch_quote',lambda *a:None)
    monkeypatch.setattr(brief,'collect_articles',lambda *a:[article(GOOD)])
    send=Mock();monkeypatch.setattr(brief,'send_telegram',send)
    text=brief.build_message('10/15(목)',NOW)
    assert '원/달러: 1,350.00원' in text and 'API' not in text
    assert len(text.encode('utf-16-le'))//2 <4096
    send.assert_not_called()


def test_winter_fx_accepts_seven_not_six():
    now = instant('2026-11-03T08:03:00+09:00')
    a = article('3일 오전 7시 기준 서울 외환시장에서 원·달러 환율은 1,350.0원에 거래를 마감했다.',
                '2026-11-03T07:30:00+09:00')
    assert fx.fetch_night_close([a],now)['closed_at'].hour==7
    a['body']=a['body'].replace('7시','6시')
    assert fx.fetch_night_close([a],now) is None


def test_complete_message_stays_within_telegram_limit(monkeypatch):
    articles=[]
    for i, subject in enumerate(['뉴욕증시','코스피','유럽증시','국제유가']):
        a=article(f'{subject}는 실적 개선 기대에 힘입어 상승했다.')
        a['url']='https://www.mk.co.kr/news/'+str(i)+'a'*470
        articles.append(a)
    monkeypatch.setattr(brief,'fetch_quote',lambda *a:{'price':1000,'change_pct':1,'date':'2026-10-14'})
    monkeypatch.setattr(brief,'collect_articles',lambda *a:articles)
    text=brief.build_message('10/15(목)',NOW)
    assert len(text.encode('utf-16-le'))//2 < 4096
    for a in articles:
        if a['body'] in text:
            assert a['url'] in text # never clip a quote's attribution/link


@pytest.mark.parametrize('body', [
    '15일 오전 6시 기준 서울 외환시장에서 원·달러 환율은 주간 1350.0원에 마감했고 야간 1355.0원에 마감했다.',
    '15일 오전 6시 기준 서울 외환시장에서 원·달러 환율은 1350.0원에 마감할 것으로 예상됐다.',
])
def test_fx_ambiguous_or_forecast_is_not_actual_close(body):
    assert fx.fetch_night_close([article(body)], NOW) is None


@pytest.mark.parametrize('body', [
    '지난주 뉴욕증시는 금리 인하 기대에 힘입어 상승했다.',
    '내일 뉴욕증시는 실적 개선 기대에 힘입어 상승할 전망이다',
])
def test_news_history_and_forecast_rejected(body):
    assert news.reason_excerpt(article(body), news.GROUPS['미국']) is None


@pytest.mark.parametrize('body', [
    NEWS_GOOD.replace('14일', '13일'),
    NEWS_GOOD.replace('14일', '15일'),
    NEWS_GOOD.replace('상승 마감했다', '하락 마감했다'),
    NEWS_GOOD.replace('상승 마감했다', '상승 마감할 전망이다'),
    NEWS_GOOD.replace('상승 마감했다', '상승 마감하지 않았다'),
    NEWS_GOOD.replace('상승 마감했다', '장중 상승했다'),
    NEWS_GOOD.replace('14일(현지시간)', '지난주'),
    NEWS_GOOD.replace('14일(현지시간)', '내일'),
    NEWS_GOOD.replace('나스닥', '다우'),
    NEWS_GOOD.replace('실적 개선 기대에', '13일의 실적 개선 기대에'),
    NEWS_GOOD.replace('실적 개선 기대에', '지난주 실적 개선 기대에'),
    NEWS_GOOD.replace('상승 마감했다', '상승 마감했고 다우는 하락 마감했다'),
])
def test_news_requires_actual_matching_event(body):
    assert news.reason_excerpt(article(body), '^IXIC', US_QUOTES['^IXIC'], NOW) is None


def test_news_requires_quote_and_rejects_title_repeat():
    a=article(NEWS_GOOD)
    assert news.reason_excerpt(a, '^IXIC', None, NOW) is None
    a['title']=NEWS_GOOD
    a['body']=''
    a['description']=NEWS_GOOD+' '+NEWS_GOOD
    assert news.reason_excerpt(a, '^IXIC', US_QUOTES['^IXIC'], NOW) is None


@pytest.mark.parametrize('symbol,name,published', [
    ('^N225','닛케이','2026-10-13T16:00:00+09:00'),
    ('^HSI','항셍','2026-10-13T18:00:00+09:00'),
    ('^GDAXI','DAX','2026-10-14T02:00:00+09:00'),
    ('^FTSE','FTSE100','2026-10-14T02:00:00+09:00'),
    ('CL=F','WTI','2026-10-14T06:00:00+09:00'),
    ('GC=F','금선물','2026-10-14T06:00:00+09:00'),
])
def test_non_us_news_uses_its_own_quote_date(symbol,name,published):
    # US is on Oct 14; the actual instrument quote is on Oct 13.
    quotes={**US_QUOTES, symbol:{'date':'2026-10-13','change_pct':-1.0}}
    a=article(f'13일(현지시간) {name}은 금리 인상 우려로 하락 마감했다.',published)
    text=news.build_news_section([a],NOW,quotes)
    assert a['body'] in text and '(2026-10-13)' in text
    quotes[symbol]['date']='2026-10-14'
    assert a['body'] not in news.build_news_section([a],NOW,quotes)


def test_quote_date_explicit_across_month():
    now=instant('2026-11-01T08:00:00+09:00')
    q={'date':'2026-10-30','change_pct':1}
    a=article('30일 코스피는 실적 개선 기대에 힘입어 상승 마감했다.','2026-11-01T06:00:00+09:00')
    assert news.reason_excerpt(a,'^KS11',q,now) is None


def test_three_article_quota_is_after_event_validation(monkeypatch):
    monkeypatch.setattr(news,'FEEDS',[('매일경제','feed')])
    entries=[]
    bodies={}
    for i in range(5):
        url=f'https://www.mk.co.kr/news/stock/{i}'
        entries.append(f'<item><title>나스닥 마감 {i}</title><link>{url}</link><pubDate>Wed, 14 Oct 2026 21:30:00 GMT</pubDate></item>')
        bodies[url]= NEWS_GOOD.replace('14일','13일') if i<4 else NEWS_GOOD
    def get(url):
        if url=='feed': return ('<rss><channel>'+''.join(entries)+'</channel></rss>').encode()
        return ('<div id="article-body">'+bodies[url]+'</div>').encode()
    found=news.collect_articles(get,NOW,US_QUOTES)
    assert len(found)==1 and found[0]['url'].endswith('/4')


def test_quotes_fetched_once_and_shared_with_news(monkeypatch):
    monkeypatch.setattr(brief,'SYMBOLS',[('나스닥','^IXIC',2)])
    get=Mock(return_value={'date':'2026-10-14','change_pct':1,'price':100})
    monkeypatch.setattr(brief,'fetch_quote',get)
    collect=Mock(return_value=[article(NEWS_GOOD)])
    monkeypatch.setattr(brief,'collect_articles',collect)
    text=brief.build_message('10/15(목)',NOW)
    get.assert_called_once_with('^IXIC',NOW)
    assert collect.call_args.args[2]['^IXIC'] == get.return_value
    assert NEWS_GOOD in text
