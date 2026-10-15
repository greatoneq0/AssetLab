"""Exchange-local dates for completed daily bars (never wall-clock run dates)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import math
import exchange_calendars as xcals


def utc_now():
    return datetime.now(timezone.utc)


def valid_price(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def session_target(market, now=None, morning=False):
    now = now or utc_now()
    if now.tzinfo is None:
        raise ValueError('Timezone-aware datetime required')
    cal = xcals.get_calendar('XKRX' if market == 'KR' else 'XNYS')
    local_day = now.astimezone(ZoneInfo('Asia/Seoul' if market == 'KR' else 'America/New_York')).date()
    if morning and market == 'KR':
        local_day -= timedelta(days=1)
    session = cal.date_to_session(local_day.isoformat(), direction='previous')
    # Allow publication of the final auction bar before accepting the session.
    while cal.session_close(session).to_pydatetime() + timedelta(minutes=20) > now:
        session = cal.previous_session(session)
    return session.date().isoformat(), cal.previous_session(session).date().isoformat()
