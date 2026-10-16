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


def artifact_names(get_page):
    """Exhaust pagination; never infer absence from a partial API response."""
    names = []
    for page in range(1, 101):
        data = get_page(page)
        items = data['artifacts']
        names.extend(item['name'] for item in items)
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
    def get_page(page):
        req = urllib.request.Request(
            f'https://api.github.com/repos/{repo}/actions/artifacts?per_page=100&page={page}',
            headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],
                     'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        with urllib.request.urlopen(req,timeout=30) as response:
            return json.load(response)
    decision, reason = delivery_decision(artifact_names(get_page),args.day,
                                        manual=args.manual,approved_retry=args.approved_retry)
    print(f'delivery decision={decision} reason={reason}')
    with open(os.environ['GITHUB_OUTPUT'],'a') as output:
        output.write(f'decision={decision}\n')
    return 1 if decision == 'blocked' else 0


if __name__ == '__main__':
    sys.exit(main())
