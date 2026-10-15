import datetime as dt
import json
import sys
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import market_brief as brief
import update_prices as update
from market_sessions import session_target


def instant(value):
    return dt.datetime.fromisoformat(value)


@pytest.mark.parametrize('now,expected', [
    ('2026-10-14T23:03:00+00:00', '2026-10-14'),
    ('2026-10-15T01:12:00+00:00', '2026-10-14'),
    ('2026-10-15T08:00:00+00:00', '2026-10-14'),
    ('2026-10-17T23:03:00+00:00', '2026-10-16'),
    ('2026-10-11T23:03:00+00:00', '2026-10-08'), # Hangul day + weekend
    ('2026-02-18T23:03:00+00:00', '2026-02-13'), # Lunar New Year
    ('2026-10-14T14:59:00+00:00', '2026-10-13'),
    ('2026-10-14T15:00:00+00:00', '2026-10-14'),
])
def test_morning_kr_session(now, expected):
    assert session_target('KR', instant(now), morning=True)[0] == expected


def test_update_close_boundary_and_us_dst():
    assert session_target('KR', instant('2026-10-15T06:29:00+00:00'))[0] == '2026-10-14'
    assert session_target('KR', instant('2026-10-15T07:00:00+00:00'))[0] == '2026-10-15'
    assert session_target('US', instant('2026-10-15T00:10:00+00:00'))[0] == '2026-10-14'
    assert session_target('US', instant('2026-01-15T20:59:00+00:00'))[0] == '2026-01-14'


def chart(days, closes, tz='Asia/Seoul'):
    stamps = [int(dt.datetime.fromisoformat(d+'T09:00:00').replace(tzinfo=ZoneInfo(tz)).timestamp()) for d in days]
    return json.dumps({'chart': {'result': [{'meta': {'exchangeTimezoneName': tz,
        'regularMarketPrice': 99999, 'chartPreviousClose': 1}, 'timestamp': stamps,
        'indicators': {'quote': [{'close': closes}]}}]}}).encode()


def test_kr_ignores_live_price_and_range_baseline(monkeypatch):
    monkeypatch.setattr(brief, 'http_get', lambda _: chart(['2026-10-13','2026-10-14','2026-10-15'], [100,110,999]))
    q = brief.fetch_quote('^KS11', instant('2026-10-15T01:12:00+00:00'))
    assert q['price'] == 110 and q['change_pct'] == 10
    assert q['date'] == '2026-10-14' and q['previous_date'] == '2026-10-13'


@pytest.mark.parametrize('values', [[100,None], [100,float('nan')], [100,float('inf')], [100,0]])
def test_missing_confirmed_close_not_stale_fallback(monkeypatch, values):
    monkeypatch.setattr(brief,'http_get',lambda _: chart(['2026-10-13','2026-10-14'],values))
    assert brief.fetch_quote('^KQ11', instant('2026-10-15T00:00:00+00:00')) is None


def test_us_and_kr_have_independent_dates(monkeypatch):
    def get(url):
        return chart(['2026-10-08','2026-10-09'],[100,101], 'America/New_York') if 'GSPC' in url else chart(['2026-10-07','2026-10-08'],[200,202])
    monkeypatch.setattr(brief,'http_get',get)
    now=instant('2026-10-11T23:03:00+00:00')
    assert brief.fetch_quote('^KS11',now)['date']=='2026-10-08'
    assert brief.fetch_quote('^GSPC',now)['date']=='2026-10-09'


@pytest.mark.parametrize('value', [float('nan'),float('inf'),0,None])
def test_update_invalid_close(monkeypatch,value):
    monkeypatch.setattr(update.fdr,'DataReader',lambda *a: pd.DataFrame({'Close':[value]},index=pd.to_datetime(['2026-10-14'])))
    assert update.fetch_close('NAVER:069500','KR',instant('2026-10-15T00:00:00+00:00'),retries=1) is None


def test_lowercase_market_and_dated_merge(monkeypatch):
    monkeypatch.setattr(update.time,'sleep',lambda _:None)
    monkeypatch.setattr(update,'fetch_close',lambda symbol,market,now: ('2026-10-08',200) if market=='KR' else ('2026-10-09',100))
    assert len(update.fetch_prices('kr'))==1
    assert update.fetch_prices('us')[0]['kr']=={}
    data={'series':[{'date':'2026-10-09','kr':{},'us':{'OTHER':42}}]}
    for row in update.fetch_prices('both'): update.merge_row(data,row)
    assert [r['date'] for r in data['series']]==['2026-10-08','2026-10-09']
    assert data['series'][1]['us']['OTHER']==42
    assert 'KODEX200' not in data['series'][1]['us']
    assert update.ASSETS[0]['symbol']=='NAVER:069500'


def test_failed_fetch_preserves_cache(monkeypatch,tmp_path):
    p=tmp_path/'prices.json';m=tmp_path/'meta.json'
    p.write_text('{"series": []}');m.write_text('{"updatedAt":"old"}')
    monkeypatch.setattr(update,'PRICES_PATH',p);monkeypatch.setattr(update,'META_PATH',m)
    monkeypatch.setattr(update,'fetch_prices',lambda _:[])
    with pytest.raises(SystemExit): update.main()
    assert p.read_text()=='{"series": []}' and m.read_text()=='{"updatedAt":"old"}'


def test_render_dates_and_dry_run_never_sends(monkeypatch,capsys):
    monkeypatch.setattr(brief,'SYMBOLS',[('코스피','^KS11',2)])
    monkeypatch.setattr(brief,'fetch_quote',lambda *a:{'price':110,'change_pct':10,'date':'2026-10-14'})
    monkeypatch.setattr(brief, 'collect_articles', lambda *a: [])
    monkeypatch.setattr(brief, 'utc_now', lambda: instant('2026-10-15T00:00:00+00:00'))
    monkeypatch.setattr(sys,'argv',['market_brief.py','--dry-run'])
    send=Mock();monkeypatch.setattr(brief,'send_telegram',send)
    assert brief.main()==0
    assert '기준일 2026-10-14' in capsys.readouterr().out
    send.assert_not_called()


def test_update_rejects_live_and_stale_rows(monkeypatch):
    frame=pd.DataFrame({'Close':[100,110,999]},index=pd.to_datetime(['2026-10-13','2026-10-14','2026-10-15']))
    monkeypatch.setattr(update.fdr,'DataReader',lambda *a:frame)
    now=instant('2026-10-15T00:10:00+00:00')
    assert update.fetch_close('SPY','US',now,retries=1)==('2026-10-14',110)
    frame.drop(pd.Timestamp('2026-10-14'),inplace=True)
    assert update.fetch_close('SPY','US',now,retries=1) is None


def test_unchanged_cache_preserves_timestamp(monkeypatch,tmp_path):
    row={'date':'2026-10-14','kr':{'KODEX200':200},'us':{'KODEX200':200}}
    p=tmp_path/'prices.json';m=tmp_path/'meta.json'
    p.write_text(json.dumps({'series':[row]}));m.write_text('{"updatedAt":"old","priceAsOf":{"KODEX200":"2026-10-14"}}')
    original_meta = m.read_bytes()
    monkeypatch.setattr(update,'PRICES_PATH',p);monkeypatch.setattr(update,'META_PATH',m)
    monkeypatch.setattr(update,'fetch_prices',lambda _:[row])
    update.main()
    assert m.read_bytes() == original_meta


def test_partial_update_metadata_not_relabel_other_market(monkeypatch,tmp_path):
    p=tmp_path/'prices.json';m=tmp_path/'meta.json'
    p.write_text('{"series":[]}');m.write_text('{"priceAsOf":{"SPY":"2026-10-13"}}')
    monkeypatch.setattr(update,'PRICES_PATH',p);monkeypatch.setattr(update,'META_PATH',m)
    monkeypatch.setattr(update,'fetch_prices',lambda _:[{'date':'2026-10-14','kr':{'KODEX200':200},'us':{'KODEX200':200}}])
    update.main()
    assert json.loads(m.read_text())['priceAsOf']=={'SPY':'2026-10-13','KODEX200':'2026-10-14'}


@pytest.mark.parametrize('prior_as_of', [None, '2026-10-13'])
def test_unchanged_verified_close_repairs_as_of(monkeypatch, tmp_path, prior_as_of):
    """A cached new-session bar still needs independently verified metadata."""
    rows = [
        {'date': day, 'kr': {'KODEX200': 200}, 'us': {'KODEX200': 200}}
        for day in ('2026-10-13', '2026-10-14')
    ]
    p = tmp_path / 'prices.json'
    m = tmp_path / 'meta.json'
    p.write_text(json.dumps({'series': rows}))
    meta = {'updatedAt': 'original-write-time', 'priceAsOf': {'SPY': '2026-10-13'}}
    if prior_as_of is not None:
        meta['priceAsOf']['KODEX200'] = prior_as_of
    m.write_text(json.dumps(meta))
    original_prices = p.read_bytes()
    monkeypatch.setattr(update, 'PRICES_PATH', p)
    monkeypatch.setattr(update, 'META_PATH', m)
    monkeypatch.setenv('MARKET', 'kr')
    monkeypatch.setattr(update, 'session_target', lambda *a: ('2026-10-14', '2026-10-13'))
    monkeypatch.setattr(update.time, 'sleep', lambda _: None)
    monkeypatch.setattr(update.fdr, 'DataReader', lambda *a: pd.DataFrame(
        {'Close': [200, 200]}, index=pd.to_datetime(['2026-10-13', '2026-10-14'])))
    update.main()
    assert p.read_bytes() == original_prices
    assert json.loads(m.read_text()) == {
        'updatedAt': 'original-write-time',
        'priceAsOf': {'SPY': '2026-10-13', 'KODEX200': '2026-10-14'},
    }


def test_legacy_prices_without_meta_file(monkeypatch, tmp_path):
    p = tmp_path / 'prices.json'
    m = tmp_path / 'meta.json'
    row = {'date': '2026-10-14', 'kr': {'KODEX200': 200}, 'us': {'KODEX200': 200}}
    p.write_text(json.dumps({'series': [row]}))
    monkeypatch.setattr(update, 'PRICES_PATH', p)
    monkeypatch.setattr(update, 'META_PATH', m)
    monkeypatch.setattr(update, 'fetch_prices', lambda _: [row])
    update.main()
    meta = json.loads(m.read_text())
    assert meta['priceAsOf'] == {'KODEX200': '2026-10-14'}
    assert meta['updatedAt'] is None


@pytest.mark.parametrize('response', ['stale', 'invalid', 'failure'])
def test_unverified_response_cannot_refresh_metadata(monkeypatch, tmp_path, response):
    p = tmp_path / 'prices.json'
    m = tmp_path / 'meta.json'
    p.write_text('{"series":[]}')
    m.write_text('{"updatedAt":"old","priceAsOf":{"KODEX200":"2026-10-13"}}')
    before = (p.read_bytes(), m.read_bytes())
    monkeypatch.setattr(update, 'PRICES_PATH', p)
    monkeypatch.setattr(update, 'META_PATH', m)
    monkeypatch.setenv('MARKET', 'kr')
    monkeypatch.setattr(update, 'session_target', lambda *a: ('2026-10-14', '2026-10-13'))
    monkeypatch.setattr(update.time, 'sleep', lambda _: None)
    def reader(*args):
        if response == 'failure':
            raise ConnectionError('provider unavailable')
        return pd.DataFrame({'Close': [200 if response == 'stale' else float('nan')]},
                            index=pd.to_datetime(['2026-10-13' if response == 'stale' else '2026-10-14']))
    monkeypatch.setattr(update.fdr, 'DataReader', reader)
    with pytest.raises(SystemExit):
        update.main()
    assert (p.read_bytes(), m.read_bytes()) == before


def test_same_price_new_session_updates_series_and_timestamp(monkeypatch, tmp_path):
    p = tmp_path / 'prices.json'
    m = tmp_path / 'meta.json'
    p.write_text(json.dumps({'series': [
        {'date': '2026-10-13', 'kr': {'KODEX200': 200}, 'us': {'KODEX200': 200}}]}))
    m.write_text('{"updatedAt":"old","priceAsOf":{"KODEX200":"2026-10-13"}}')
    monkeypatch.setattr(update, 'PRICES_PATH', p)
    monkeypatch.setattr(update, 'META_PATH', m)
    monkeypatch.setattr(update, 'fetch_prices', lambda _: [
        {'date': '2026-10-14', 'kr': {'KODEX200': 200}, 'us': {'KODEX200': 200}}])
    monkeypatch.setattr(update.time, 'strftime', lambda *a: 'new-write-time')
    update.main()
    assert [r['date'] for r in json.loads(p.read_text())['series']] == ['2026-10-13', '2026-10-14']
    assert json.loads(m.read_text()) == {
        'updatedAt': 'new-write-time', 'priceAsOf': {'KODEX200': '2026-10-14'}}


@pytest.mark.parametrize('market', ['kr', 'both'])
def test_requested_korean_failure_is_not_green_or_cache_refresh(monkeypatch,tmp_path,market):
    p=tmp_path/'prices.json';m=tmp_path/'meta.json'
    p.write_text('{"series":[]}');m.write_text('{"updatedAt":"old","priceAsOf":{"KODEX200":"2026-10-13"}}')
    before=(p.read_bytes(),m.read_bytes())
    monkeypatch.setattr(update,'PRICES_PATH',p);monkeypatch.setattr(update,'META_PATH',m)
    monkeypatch.setenv('MARKET',market)
    monkeypatch.setattr(update,'session_target',lambda *a:('2026-10-14','2026-10-13'))
    monkeypatch.setattr(update.time,'sleep',lambda _:None)
    symbols=[]
    def reader(symbol,*args):
        symbols.append(symbol)
        if symbol=='NAVER:069500': raise ValueError('unsupported')
        return pd.DataFrame({'Close':[100]},index=pd.to_datetime(['2026-10-14']))
    monkeypatch.setattr(update.fdr,'DataReader',reader)
    with pytest.raises(SystemExit,match='KODEX200'):
        update.main()
    assert (p.read_bytes(),m.read_bytes())==before
    assert 'NAVER:069500' in symbols and 'KRX:069500' not in symbols


def test_us_only_run_does_not_require_korean_close(monkeypatch):
    monkeypatch.setattr(update.time,'sleep',lambda _:None)
    fetched=[]
    def fetch(symbol,market,now):
        fetched.append(symbol)
        return ('2026-10-14',100)
    monkeypatch.setattr(update,'fetch_close',fetch)
    assert update.fetch_prices('us')[0]['us']=={'SPY':100,'TLT':100}
    assert fetched==['SPY','TLT']
