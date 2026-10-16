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

def ledger_fixture():
    from copy import deepcopy
    run = dict(id=123, workflow_id=7, path='.github/workflows/daily_market_news.yml',
               repository={'id': 9}, head_repository={'id': 9}, head_branch='main',
               head_sha='abc', event='schedule', run_attempt=1,
               run_started_at='2026-10-15T23:00:00Z')
    artifact = dict(id=1, name=names('sent')[0], created_at='2026-10-15T23:02:00Z',
                    workflow_run=dict(id=123, repository_id=9, head_repository_id=9,
                                      head_branch='main', head_sha='abc'))
    api = {'': {'id':9,'default_branch':'main'},
           '/actions/workflows/daily_market_news.yml': {'id':7,'path':run['path']},
           '/actions/runs/123': deepcopy(run), '/actions/runs/123/attempts/1': deepcopy(run)}
    return artifact, api


def read_ledger(items, api):
    return artifact_names(lambda page: {'artifacts': items}, api.__getitem__, DAY)


def test_listing_paginates_and_fails_closed_on_api_errors():
    artifact, api = ledger_fixture()
    pages={1:{'artifacts':[{'name':str(i)} for i in range(100)]},
           2:{'artifacts':[artifact]}}
    assert delivery_decision(artifact_names(pages.__getitem__,api.__getitem__,DAY),DAY)[0]=='skip'
    with pytest.raises(OSError):
        artifact_names(Mock(side_effect=OSError('offline')),api.__getitem__,DAY)
    del api['/actions/runs/123']
    with pytest.raises(KeyError): read_ledger([artifact],api)


@pytest.mark.parametrize('event,branch', [('schedule','main'), ('workflow_dispatch','main'),
                                         ('workflow_dispatch','codex/fix-market-close-dates')])
def test_trusted_schedule_and_authorized_manual_branches(event,branch):
    artifact,api=ledger_fixture()
    artifact['workflow_run']['head_branch']=branch
    for path in ['/actions/runs/123','/actions/runs/123/attempts/1']:
        api[path].update(event=event,head_branch=branch)
    assert read_ledger([artifact],api)==[artifact['name']]


@pytest.mark.parametrize('change', [dict(event='pull_request'),dict(event='pull_request_target'),
    dict(event='push'),dict(head_branch='attacker'),dict(workflow_id=88),
    dict(path='.github/workflows/ci.yml'),dict(head_repository={'id':99}),
    dict(repository={'id':99}),dict(head_sha='different'),dict(id=999)])
def test_untrusted_run_cannot_suppress_delivery(change):
    artifact,api=ledger_fixture()
    api['/actions/runs/123'].update(change)
    assert delivery_decision(read_ledger([artifact],api),DAY)[0]=='send'


@pytest.mark.parametrize('change',[dict(id=456),dict(repository_id=99),
                                  dict(head_repository_id=99),dict(head_sha='other')])
def test_artifact_origin_must_match_encoded_run_and_repository(change):
    artifact,api=ledger_fixture()
    artifact['workflow_run'].update(change)
    assert read_ledger([artifact],api)==[]


def test_missing_origin_is_ignored():
    artifact,api=ledger_fixture();del artifact['workflow_run']
    assert read_ledger([artifact],api)==[]


def test_forged_rejection_cannot_clear_legitimate_started_attempt():
    from copy import deepcopy
    artifact,api=ledger_fixture();artifact['name']=names('started')[0]
    forged=deepcopy(artifact);forged['name']=names('rejected')[0]
    forged['workflow_run']['id']=456  # PR uploader, name impersonates run 123
    assert delivery_decision(read_ledger([artifact,forged],api),DAY)[0]=='blocked'


def test_rerun_cannot_impersonate_prior_attempt_but_real_records_survive():
    from copy import deepcopy
    artifact,api=ledger_fixture()
    second=deepcopy(api['/actions/runs/123'])
    second.update(run_attempt=2,run_started_at='2026-10-15T23:10:00Z')
    api['/actions/runs/123']=second
    api['/actions/runs/123/attempts/2']=second
    assert read_ledger([artifact],api)==[artifact['name']]
    artifact['created_at']='2026-10-15T23:11:00Z'
    assert read_ledger([artifact],api)==[]
    artifact['name']=artifact['name'].replace('-123-1-', '-123-2-')
    assert read_ledger([artifact],api)==[artifact['name']]
    artifact['name']=artifact['name'].replace('-123-2-', '-123-3-')
    assert read_ledger([artifact],api)==[]


def test_attempt_metadata_must_match_and_timestamp_failures_fail_closed():
    artifact,api=ledger_fixture()
    api['/actions/runs/123/attempts/1']['run_attempt']=2
    assert read_ledger([artifact],api)==[]
    api['/actions/runs/123/attempts/1']['run_attempt']=1
    artifact['created_at']='invalid'
    with pytest.raises(ValueError): read_ledger([artifact],api)


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
