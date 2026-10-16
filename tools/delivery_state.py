"""Read-only GitHub artifact ledger, serialized by workflow concurrency.

A started attempt without a terminal result is UNKNOWN, never SENT. Explicit
Telegram rejections may be retried. Unknown/sent attempts need an explicit manual
override after the operator checks receipt. No cache claim marks failure as sent.
"""
import argparse
from datetime import datetime
import json
import os
import re
import sys
import urllib.request
from zoneinfo import ZoneInfo

PREFIX = 'telegram-v2-'
WORKFLOW_PATH = '.github/workflows/daily_market_news.yml'
# This PR branch is explicitly authorized for manual delivery tests.
MANUAL_BRANCHES = {'codex/fix-market-close-dates'}
NAME_PATTERN = re.compile(r'telegram-v2-(\d{4}-\d{2}-\d{2})-(\d+)-(\d+)-(started|sent|rejected|unknown)')


def delivery_decision(names, day, *, manual=False, approved_retry=False):
    if approved_retry and not manual:
        raise ValueError('Retry override is only valid for a manual run')
    attempts = {}
    for name in names:
        match = re.fullmatch(r'telegram-v2-(\d{4}-\d{2}-\d{2})-(\d+)-(\d+)-(started|sent|rejected|unknown)', name)
        if match and match[1] == day:
            attempts.setdefault((match[2], match[3]), set()).add(match[4])
    if approved_retry:
        return 'send', 'explicit_manual_retry_after_receipt_check'
    if any('sent' in states for states in attempts.values()):
        return 'skip', 'already_sent'
    if any('unknown' in states or ('started' in states and 'rejected' not in states)
           for states in attempts.values()):
        return 'blocked', 'previous_delivery_unknown_check_receipt'
    return 'send', 'no_success_or_unknown_attempt'  # includes explicit rejection


def artifact_names(get_page, get_api, day):
    """Return only records with authenticated GitHub run/attempt provenance.

    API errors propagate: an incomplete ledger must never authorize delivery.
    Artifact timestamps bind reruns because workflow_run omits run_attempt.
    """
    repository = get_api('')
    workflow = get_api('/actions/workflows/daily_market_news.yml')
    if workflow['path'] != WORKFLOW_PATH:
        raise RuntimeError('Unexpected delivery workflow path')
    repo_id = repository['id']
    default_branch = repository['default_branch']
    cache = {}

    def fetch(path):
        if path not in cache:
            cache[path] = get_api(path)
        return cache[path]

    def trusted(run, run_id):
        branch = run.get('head_branch')
        event = run.get('event')
        return (run.get('id') == run_id and
                run.get('workflow_id') == workflow['id'] and
                run.get('path', '').split('@', 1)[0] == WORKFLOW_PATH and
                run.get('repository', {}).get('id') == repo_id and
                run.get('head_repository', {}).get('id') == repo_id and
                ((event == 'schedule' and branch == default_branch) or
                 (event == 'workflow_dispatch' and branch in MANUAL_BRANCHES | {default_branch})))

    def timestamp(value):
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError('Missing provenance timestamp timezone')
        return result

    names = []
    for page in range(1, 101):
        items = get_page(page)['artifacts']
        for item in items:
            match = NAME_PATTERN.fullmatch(item['name'])
            if not match or match[1] != day:
                continue
            run_id, attempt = int(match[2]), int(match[3])
            origin = item.get('workflow_run') or {}
            reason = 'untrusted_artifact_origin'
            if (origin.get('id') == run_id and attempt > 0 and
                    origin.get('repository_id') == repo_id and
                    origin.get('head_repository_id') == repo_id):
                path = f'/actions/runs/{run_id}'
                latest = fetch(path)
                if (trusted(latest, run_id) and
                        origin.get('head_branch') == latest['head_branch'] and
                        origin.get('head_sha') == latest['head_sha'] and
                        attempt <= latest['run_attempt']):
                    run = fetch(f'{path}/attempts/{attempt}')
                    if (trusted(run, run_id) and run.get('run_attempt') == attempt and
                            run.get('head_sha') == origin['head_sha'] and
                            run.get('head_branch') == origin['head_branch']):
                        created = timestamp(item['created_at'])
                        start = timestamp(run['run_started_at'])
                        # A rerun may forge an older attempt number; its upload
                        # cannot fall inside the older attempt's time window.
                        end = None
                        if attempt < latest['run_attempt']:
                            next_run = fetch(f'{path}/attempts/{attempt + 1}')
                            if not trusted(next_run, run_id) or next_run['run_attempt'] != attempt + 1:
                                raise RuntimeError('Inconsistent next-attempt provenance')
                            end = timestamp(next_run['run_started_at'])
                        if created >= start and (end is None or created < end):
                            names.append(item['name'])
                            continue
                        reason = 'artifact_outside_encoded_attempt'
                    else:
                        reason = 'attempt_metadata_mismatch'
                else:
                    reason = 'untrusted_run_metadata'
            print(f'ledger ignored artifact_id={item.get("id")} reason={reason}')
        if len(items) < 100:
            return names
    raise RuntimeError('Artifact listing exceeded limit; refusing uncertain delivery')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--day', required=True)
    p.add_argument('--manual', action='store_true')
    p.add_argument('--approved-retry', action='store_true')
    args = p.parse_args()
    if args.day != datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat():
        raise SystemExit('Delivery date changed; rebuild before attempting delivery')
    repo = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise SystemExit('Invalid repository')
    def get_api(path):
        req = urllib.request.Request(
            f'https://api.github.com/repos/{repo}{path}',
            headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],
                     'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        with urllib.request.urlopen(req,timeout=30) as response:
            return json.load(response)
    decision, reason = delivery_decision(artifact_names(
        lambda page: get_api(f'/actions/artifacts?per_page=100&page={page}'), get_api, args.day),args.day,
                                        manual=args.manual,approved_retry=args.approved_retry)
    print(f'delivery decision={decision} reason={reason}')
    with open(os.environ['GITHUB_OUTPUT'],'a') as output:
        output.write(f'decision={decision}\n')
    return 1 if decision == 'blocked' else 0


if __name__ == '__main__':
    sys.exit(main())
