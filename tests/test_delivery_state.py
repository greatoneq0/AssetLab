import io
import json
from pathlib import Path
import sys
from unittest.mock import Mock
import urllib.error
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from delivery_state import delivery_decision, artifact_names
from deliver_brief import deliver
import market_brief as brief

DAY='2026-10-16'
def names(*states):
    return [f'telegram-v2-{DAY}-123-1-{s}' for s in states]

@pytest.mark.parametrize('states,decision', [
    ([], 'send'), (['started','sent'],'skip'), (['started','rejected'],'send'),
    (['started'],'blocked'), (['started','unknown'],'blocked'),
    (['sent'],'skip'), (['rejected'],'send'),
])
def test_success_failure_and_uncertainty_are_distinct(states,decision):
    assert delivery_decision(names(*states),DAY)[0]==decision

def test_approved_manual_test_is_not_unconditionally_blocked():
    assert delivery_decision(names('sent'),DAY,manual=True,approved_retry=True)[0]=='send'
    assert delivery_decision(names('unknown'),DAY,manual=True,approved_retry=True)[0]=='send'
    assert delivery_decision(names('sent'),DAY,manual=True)[0]=='skip'
    with pytest.raises(ValueError):
        delivery_decision([],DAY,approved_retry=True)

def test_other_days_and_unrelated_artifacts_do_not_block():
    assert delivery_decision(names('sent'), '2026-10-17')[0]=='send'
    assert delivery_decision(['market-brief-preview-123-1'],DAY)[0]=='send'

def test_listing_paginates_and_fails_closed_on_api_errors():
    pages={1:{'artifacts':[{'name':str(i)} for i in range(100)]},
           2:{'artifacts':[{'name':names('sent')[0]}]}}
    assert delivery_decision(artifact_names(pages.__getitem__),DAY)[0]=='skip'
    with pytest.raises(OSError):
        artifact_names(Mock(side_effect=OSError('offline')))

@pytest.mark.parametrize('error,status',[(None,'sent'),(brief.TelegramRejected(),'rejected'),
                                       (TimeoutError(),'unknown'),(ValueError(),'unknown')])
def test_transport_outcome_and_no_automatic_retry(error,status):
    sender=Mock(side_effect=error)
    assert deliver('preview','fake-token','fake-chat',send=sender)==status
    sender.assert_called_once()

@pytest.mark.parametrize('code,payload,exception',[(400,{'ok':False},brief.TelegramRejected),
    (500,{'ok':False},RuntimeError),(400,{},RuntimeError)])
def test_http_rejection_requires_explicit_telegram_response(monkeypatch,code,payload,exception):
    err=urllib.error.HTTPError('https://example.invalid',code,'error',{},io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr(brief.urllib.request,'urlopen',Mock(side_effect=err))
    with pytest.raises(exception): brief.send_telegram('fake-token','fake-chat','preview')


def test_missing_config_is_rejected_without_transport(monkeypatch,tmp_path):
    import deliver_brief
    from datetime import datetime
    from zoneinfo import ZoneInfo
    message=tmp_path/'message.txt';message.write_text('preview only')
    output=tmp_path/'output';state=tmp_path/'state'
    monkeypatch.setenv('GITHUB_OUTPUT',str(output))
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN',raising=False)
    monkeypatch.delenv('TELEGRAM_CHAT_ID',raising=False)
    monkeypatch.setattr(sys,'argv',['deliver_brief','--message',str(message),'--state-dir',str(state),
                                  '--day',datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()])
    transport=Mock();monkeypatch.setattr(deliver_brief,'deliver',transport)
    assert deliver_brief.main()==1
    transport.assert_not_called()
    assert json.loads((state/'outcome.json').read_text())['status']=='rejected'
    assert 'sent' not in output.read_text()
