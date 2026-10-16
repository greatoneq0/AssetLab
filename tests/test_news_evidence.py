"""Short browser-verified excerpts in reconstructed HTML, NOT downloaded pages.
Raw live publisher HTML is tested separately by verify_market_news.py in CI.
"""
import datetime as dt
from pathlib import Path
import sys
import logging
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import market_news as news

NOW = dt.datetime.fromisoformat('2026-10-16T08:00:00+09:00')
KR_CLOSE = '15일 코스피는 7.57%(454.59포인트) 하락한 5554.22에 장을 마감했다.'
KR_HEAD = '물가·AI공포 겹악재… 5600 무너진 코스피'
Q = {'date':'2026-10-15','price':5554.22,'change_pct':-7.57}


def article(body=KR_CLOSE):
    return dict(title=KR_HEAD, body=body, description='', source='매일경제',
                url='https://www.mk.co.kr/news/stock/12177086',
                published=dt.datetime.fromisoformat('2026-10-15T17:46:00+09:00'))


def test_actual_oct15_close_without_same_sentence_cause():
    a = article(news.article_body(f'<div class="news_cnt_detail_wrap"><p>{KR_CLOSE}</p></div>'.encode()))
    assert news.reason_excerpt(a, '^KS11', Q, NOW) is None # original failure reproduced
    excerpt, reason = news.news_excerpt(a, '^KS11', Q, NOW)
    assert reason == 'accepted_market_facts'
    assert '5,554.22 (-7.57%) 마감' in excerpt
    assert '기사 제목(본문 요약 미확보)' in excerpt and '원인 보도' not in excerpt
    assert KR_HEAD in news.build_news_section([a], NOW, {'^KS11': Q})


@pytest.mark.parametrize('body,quote,reason', [
    ('', Q, 'body_missing_or_headline_only'),
    (KR_HEAD, Q, 'body_missing_or_headline_only'),
    (KR_CLOSE.replace('15일','14일'), Q, 'event_date_missing_or_mismatch'),
    (KR_CLOSE.replace('15일','지난 15일'), Q, 'forecast_history_or_intraday'),
    (KR_CLOSE.replace('마감했다','마감할 전망이다'), Q, 'instrument_or_completed_close_missing'),
    (KR_CLOSE.replace('하락한','장중 하락한'), Q, 'forecast_history_or_intraday'),
    (KR_CLOSE.replace('5554.22','5554.32'), Q, 'close_level_mismatch'),
    (KR_CLOSE.replace('7.57%','7.58%'), {**Q,'change_pct':-7.55}, 'change_percent_mismatch'),
    (KR_CLOSE, {**Q,'change_pct':7.57}, 'direction_mismatch'),
    (KR_CLOSE, None, 'quote_missing'),
])
def test_evidence_rejection_reasons(body,quote,reason):
    assert news.market_evidence(article(body), '^KS11', quote, NOW) == (None, reason)


def test_synthetic_multi_sentence_multi_index_report():
    # Same grammar as the two Oct16 US reports; synthetic values, not a raw fixture.
    body = ('미국 뉴욕증시가 15일(현지시간) 상승했다. 국채금리 하락이 증시를 끌어올렸다. '
            '이날 뉴욕증권거래소에서 다우존스30 산업평균지수는 1.00% 오른 5만2000.00에, '
            '스탠더드앤드푸어스(S&P) 500지수는 0.50% 상승한 7700.00에, '
            '나스닥 종합지수는 0.20% 오른 2만6000.00에 거래를 마쳤다.')
    a = article(body);a['published'] = NOW-dt.timedelta(hours=1)
    for symbol, price, change in [('^DJI',52000,1),('^GSPC',7700,.5),('^IXIC',26000,.2)]:
        q = {'date':'2026-10-15','price':price,'change_pct':change}
        assert news.market_evidence(a,symbol,q,NOW)[0]
        assert not news.market_evidence(a,symbol,{**q,'price':price+1},NOW)[0]
    a['body']=body.replace('국채금리 하락이', '14일 국채금리 하락이')
    assert not news.market_evidence(a,'^IXIC',{'date':'2026-10-15','price':26000,'change_pct':.2},NOW)[0]


def test_collection_logs_fetch_and_filter_separately(monkeypatch,caplog):
    monkeypatch.setattr(news,'FEEDS',[('매일경제','feed')])
    feed = ('<rss><channel><item><title>코스피 마감</title><link>'+article()['url']+
            '</link><pubDate>Thu, 15 Oct 2026 08:46:00 GMT</pubDate></item></channel></rss>').encode()
    def get(url):
        if url=='feed': return feed
        raise OSError('offline')
    with caplog.at_level(logging.INFO):
        assert news.collect_articles(get,NOW,{'^KS11':Q}) == []
    assert 'body_fetch_or_decode' in caplog.text
    assert 'body_missing_or_headline_only' in caplog.text
    assert 'items=1 eligible=1' in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO):
        result=news.collect_articles(lambda u:feed if u=='feed' else f'<div id="article-body">{KR_CLOSE}</div>'.encode(),NOW,{'^KS11':Q})
    assert len(result)==1 and 'accepted_market_facts' in caplog.text


def test_encoding_is_strict_and_supports_korean_legacy():
    html='<div id="article-body">코스피 마감</div>'
    assert news.article_body(html.encode('cp949'))=='코스피 마감'
    with pytest.raises((UnicodeDecodeError, ValueError)):
        news.article_body(b'<div id="article-body">\xff</div>')


def test_next_index_sentence_can_share_explicit_session_but_not_another_date():
    body=('미국 뉴욕증시가 15일(현지시간) 상승했다. 금리가 내렸다. 투자심리가 회복됐다. '
          '이날 다우는 1.00% 오른 52000에 거래를 마쳤다. '
          '나스닥은 0.20% 오른 26000에 마감했다.')
    a=article(body);a['published']=NOW-dt.timedelta(hours=1)
    q={'date':'2026-10-15','price':26000,'change_pct':.2}
    assert news.market_evidence(a,'^IXIC',q,NOW)[0]
    a['body']=body.replace('이날 다우', '14일 다우')
    assert not news.market_evidence(a,'^IXIC',q,NOW)[0]


def test_actual_mk_rss_colon_timezone_is_not_discarded():
    raw=('<rss><channel><item><title>코스피 마감</title><link>'+article()['url']+
         '</link><pubDate>Thu, 15 Oct 2026 17:46:00 +09:00</pubDate></item></channel></rss>').encode()
    result=news.parse_feed(raw,'매일경제',NOW)
    assert len(result)==1 and result[0]['published'].isoformat()=='2026-10-15T17:46:00+09:00'
    assert not news.parse_feed(raw.replace(b' +09:00',b''),'매일경제',NOW)


def test_missing_news_shows_verified_prices_without_claiming_a_cause():
    output=news.build_news_section([],NOW,{'^KS11':Q})
    assert '시세 동향' in output and '5,554.22' in output
    assert '기사 본문 근거 미확보' in output and '주요 보도' not in output.splitlines()[1]


def test_only_display_rounding_is_allowed_not_a_cent_or_basis_point_conflict():
    q={**Q,'price':5554.22021484375,'change_pct':-7.565388809384138}
    assert news.market_evidence(article(),'^KS11',q,NOW)[0]
    assert not news.market_evidence(article(KR_CLOSE.replace('5554.22','5554.23')),'^KS11',q,NOW)[0]
    assert not news.market_evidence(article(KR_CLOSE.replace('7.57%','7.58%')),'^KS11',q,NOW)[0]


def test_official_infomax_kst_date_is_source_scoped():
    raw=('<rss><channel><item><title>뉴욕유가 마감</title><link>https://news.einfomax.co.kr/news/articleView.html?idxno=1</link>'
         '<pubDate>2026-10-16 06:00:00</pubDate></item></channel></rss>').encode()
    assert news.parse_feed(raw,'연합인포맥스',NOW)[0]['published'].utcoffset()==dt.timedelta(hours=9)
    assert news.parse_feed(raw,'매일경제',NOW)==[]


def test_newer_commodity_news_does_not_relabel_older_price_session():
    a=article('국제유가는 원유 재고 감소 영향으로 상승했다. '
              '15일(현지시간) WTI는 3.00% 오른 98.00달러에 거래를 마쳤다.')
    a['title']='뉴욕유가 마감';a['published']=NOW-dt.timedelta(hours=2)
    quotes={'CL=F':{'price':95.09,'change_pct':1.01,'date':'2026-10-14'}}
    result,reason=news.session_report(a,'환율·원자재',{'2026-10-15'},NOW)
    assert result['date']=='2026-10-15' and reason=='accepted_session_body_news'
    text=news.build_news_section([a],NOW,quotes)
    assert '기사 세션 2026-10-15 · 상단 시세와 별도 세션' in text
    assert '원유 재고 감소' in text
    assert quotes['CL=F']['date']=='2026-10-14'
    assert not news.market_evidence(a,'CL=F',quotes['CL=F'],NOW)[0]


@pytest.mark.parametrize('change',[
    ('15일','14일'), ('거래를 마쳤다','거래를 마칠 전망이다'),
    ('15일','지난 15일'), ('15일','장중 15일'),
])
def test_session_news_rejects_history_forecast_and_wrong_day(change):
    a=article(('국제유가는 원유 재고 감소 영향으로 상승했다. '
               '15일(현지시간) WTI는 3.00% 오른 98.00달러에 거래를 마쳤다.').replace(*change))
    a['published']=NOW-dt.timedelta(hours=2)
    assert not news.session_report(a,'환율·원자재',{'2026-10-15'},NOW)[0]


def test_body_summary_not_replaced_by_headline_and_cross_month_not_reused():
    a=article(KR_CLOSE);a['title']='금리 우려가 증시를 압박했다'
    assert news.body_news(a,'한국','2026-10-15') is None
    a['body']='9월 15일 코스피는 금리 우려로 하락했다. '+KR_CLOSE
    assert news.body_news(a,'한국','2026-10-15') is None


def test_realistic_subtitle_and_paragraph_boundaries_preserve_body_news():
    html=('<div class="news_cnt_detail_wrap"><h2>시황 요약 제목과 별개 부제</h2>'
          '<p>미국 반도체주 약세 영향으로 코스피 투자심리가 위축됐다.</p>'
          f'<p>{KR_CLOSE}</p></div>')
    a=article(news.article_body(html.encode()))
    assert '\n' in a['body']
    assert '반도체주 약세' in news.news_excerpt(a,'^KS11',Q,NOW)[0]
    assert news.news_excerpt(a,'^KS11',Q,NOW)[1]=='accepted_body_news'
