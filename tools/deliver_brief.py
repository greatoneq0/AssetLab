"""Send an already-built brief once and record the actual transport outcome.

Called only after the workflow has persisted a started-attempt artifact.
No automatic retry, and no message/token/chat ID in the outcome artifact.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from market_brief import send_telegram, TelegramRejected


def deliver(message, token, chat_id, send=send_telegram):
    try:
        send(token,chat_id,message)
    except TelegramRejected:
        return 'rejected'  # Telegram explicitly confirmed ok=false / HTTP 4xx
    except Exception:
        return 'unknown'  # timeout/reset/invalid response may follow delivery
    return 'sent'


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--message',required=True)
    p.add_argument('--day',required=True)
    p.add_argument('--state-dir',required=True)
    args=p.parse_args()
    dest=Path(args.state_dir);dest.mkdir(parents=True,exist_ok=True)
    # No send on date rollover or invalid/missing content/configuration.
    message=Path(args.message).read_text()
    token=os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id=os.environ.get('TELEGRAM_CHAT_ID')
    if (args.day != datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
            or not token or not chat_id or not 1 <= len(message.encode('utf-16-le'))//2 <=4096):
        status='rejected'
    else:
        status=deliver(message,token,chat_id)
    (dest/'outcome.json').write_text(json.dumps({'day':args.day,'status':status}))
    with open(os.environ['GITHUB_OUTPUT'],'a') as output:
        output.write(f'status={status}\n')
    print('delivery outcome='+status)
    return 0 if status=='sent' else 1


if __name__ == '__main__':
    raise SystemExit(main())
