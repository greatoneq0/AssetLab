"""KST weekend and midnight regressions; all delivery/API calls are mocked."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re
import sys
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import deliver_brief
import delivery_state

KST = ZoneInfo('Asia/Seoul')


def freeze(monkeypatch, module, timestamp):
    instant = datetime.fromisoformat(timestamp)
    class Clock:
        @staticmethod
        def now(tz):
            return instant.astimezone(tz)
    monkeypatch.setattr(module, 'datetime', Clock)
    return instant.astimezone(KST).date().isoformat()


def test_cron_keeps_eight_am_monday_through_saturday():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/daily_market_news.yml').read_text()
    cron = re.search(r'cron: "([^"]+)"', workflow)[1]
    assert cron == '0 23 * * 0-5'
    # Cron 0 = Sunday, unlike Python weekday(): enumerate an entire UTC week.
    utc_sunday = datetime(2026, 10, 18, 23, tzinfo=timezone.utc)
    local = [(utc_sunday + timedelta(days=i)).astimezone(KST) for i in range(6)]
    assert [t.weekday() for t in local] == list(range(6))
    assert all(t.hour == 8 and t.minute == 0 for t in local)
    # Preview remains default and cannot enter the delivery gate.
    assert 'default: preview' in workflow
    assert "if: github.event_name == 'schedule' || inputs.mode == 'send'" in workflow


@pytest.mark.parametrize('timestamp,event,allowed', [
    ('2026-10-16T23:00:00+00:00', 'schedule', True),  # Saturday 08:00
    ('2026-10-17T14:59:59+00:00', 'schedule', True),  # Saturday last second
    ('2026-10-17T15:00:00+00:00', 'schedule', False), # Sunday midnight
    ('2026-10-17T23:00:00+00:00', 'schedule', False), # former Sunday schedule
    ('2026-10-18T14:59:59+00:00', 'schedule', False), # Sunday last second
    ('2026-10-18T15:00:00+00:00', 'schedule', True),  # Monday midnight
    ('2026-10-18T23:00:00+00:00', 'schedule', True),  # Monday 08:00
    ('2026-10-17T23:00:00+00:00', 'workflow_dispatch', True),
])
def test_transport_rechecks_actual_kst_day(monkeypatch, tmp_path, timestamp, event, allowed):
    day = freeze(monkeypatch, deliver_brief, timestamp)
    message = tmp_path / 'message'; message.write_text('test only')
    state = tmp_path / 'state'; output = tmp_path / 'output'
    monkeypatch.setenv('GITHUB_EVENT_NAME', event)
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'fake')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'fake')
    monkeypatch.setattr(sys, 'argv', ['deliver_brief', '--day', day, '--message', str(message), '--state-dir', str(state)])
    sender = Mock(return_value='sent'); monkeypatch.setattr(deliver_brief, 'deliver', sender)
    assert deliver_brief.main() == (0 if allowed else 1)
    assert sender.call_count == int(allowed)
    status = 'sent' if allowed else 'rejected'
    assert json.loads((state / 'outcome.json').read_text())['status'] == status
    assert output.read_text() == f'status={status}\n'


@pytest.mark.parametrize('claimed_day', ['2026-10-17', '2026-10-18'])
def test_sunday_gate_skips_before_api_or_started_marker(monkeypatch, tmp_path, claimed_day):
    freeze(monkeypatch, delivery_state, '2026-10-17T15:00:00+00:00')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'schedule')
    output = tmp_path / 'output'; monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setattr(sys, 'argv', ['delivery_state', '--day', claimed_day])
    api = Mock(side_effect=AssertionError('Sunday must not read ledger'))
    monkeypatch.setattr(delivery_state, 'artifact_names', api)
    assert delivery_state.main() == 0
    assert output.read_text() == 'decision=skip\n'
    api.assert_not_called()


@pytest.mark.parametrize('event', ['schedule', 'workflow_dispatch'])
def test_rollover_still_rejects_stale_day(monkeypatch, tmp_path, event):
    freeze(monkeypatch, deliver_brief, '2026-10-18T15:00:00+00:00')
    message = tmp_path / 'message'; message.write_text('test only')
    monkeypatch.setenv('GITHUB_EVENT_NAME', event)
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'output'))
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'fake')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'fake')
    monkeypatch.setattr(sys, 'argv', ['deliver_brief', '--day', '2026-10-18', '--message', str(message), '--state-dir', str(tmp_path / 'state')])
    sender = Mock(); monkeypatch.setattr(deliver_brief, 'deliver', sender)
    assert deliver_brief.main() == 1
    sender.assert_not_called()


@pytest.mark.parametrize('states,decision', [([], 'send'), (['sent'], 'skip'), (['started'], 'blocked'), (['unknown'], 'blocked')])
def test_manual_sunday_keeps_ledger_safety(monkeypatch, tmp_path, states, decision):
    day = freeze(monkeypatch, delivery_state, '2026-10-17T23:00:00+00:00')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'greatoneq0/AssetLab')
    output = tmp_path / 'output'; monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setattr(sys, 'argv', ['delivery_state', '--day', day, '--manual'])
    ledger = Mock(return_value=[f'telegram-v2-{day}-123-1-{s}' for s in states])
    monkeypatch.setattr(delivery_state, 'artifact_names', ledger)
    assert delivery_state.main() == (1 if decision == 'blocked' else 0)
    ledger.assert_called_once()
    assert output.read_text() == f'decision={decision}\n'
