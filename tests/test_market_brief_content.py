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
    a=article('뉴욕증시는 실적 개선 기대에 힘입어 상승했다.')
    result=news.build_news_section([a],NOW)
    assert a['body'] in result and a['url'] in result and '2026-10-15 06:30' in result
    assert '한국: 확인 가능한 원인 기사 없음' in result
    a['body']='뉴욕증시는 상승했다.'
    assert '미국: 확인 가능한 원인 기사 없음' in news.build_news_section([a],NOW)
    # Do not stitch an unrelated causal sentence onto a price sentence.
    a['body']='뉴욕증시는 상승했다. 비가 온 영향으로 야구 경기가 취소됐다.'
    assert news.reason_excerpt(a,news.GROUPS['미국']) is None


def test_news_excludes_today_intraday_kr_reason_for_yesterday_close():
    a=article('코스피는 금리 인상 우려로 하락했다.', '2026-10-15T14:00:00+09:00')
    assert '한국: 확인 가능한 원인 기사 없음' in news.build_news_section([a],instant('2026-10-15T17:00:00+09:00'))


def test_news_partial_failure_and_deduplication(monkeypatch):
    monkeypatch.setattr(news,'FEEDS',[('매일경제','bad'),('매일경제','good'),('매일경제','duplicate')])
    def get(url):
        if url=='bad': raise OSError('offline')
        if url.startswith('https:'): return b'<div id="article-body">published article</div>'
        return feed()
    found=news.collect_articles(get,NOW)
    assert len(found)==1 and found[0]['body']=='published article'
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
