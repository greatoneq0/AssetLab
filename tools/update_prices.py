#!/usr/bin/env python3
"""
한국장/미국장 종료 후 가격 데이터 갱신.

- GitHub Actions에서 1일 2회 실행 (한국장 종료 후, 미국장 종료 후).
- FinanceDataReader 사용: 한국 ETF는 NAVER 소스로 조회, 미국은 심볼 직접 조회.
- 심볼당 요청 후 2초 대기로 차단 위험 최소화, 실패 시 재시도 1회.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from market_sessions import session_target, valid_price
from pathlib import Path

try:
    import FinanceDataReader as fdr
except ImportError:
    raise SystemExit("finance-datareader 필요: pip install -r tools/requirements.txt")

# 프로젝트 루트 기준 경로 (스크립트는 tools/ 에 있음)
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PRICES_PATH = DATA_DIR / "prices.json"
META_PATH = DATA_DIR / "meta.json"

# 심볼당 요청 후 대기(초). 과도한 요청 시 IP 차단 가능성 완화
DELAY_BETWEEN_SYMBOLS = 2.0

# 한국 ETF: NAVER 일봉. 미국: 심볼만 지정.
ASSETS = [
    {"id": "KODEX200", "market": "KR", "symbol": "NAVER:069500"},
    {"id": "SPY", "market": "US", "symbol": "SPY"},
    {"id": "TLT", "market": "US", "symbol": "TLT"},
]


def load_existing() -> dict:
    if not PRICES_PATH.exists():
        return {
            "series": [],
            "meta": {"assets": [a["id"] for a in ASSETS], "start": None, "end": None},
        }
    raw = PRICES_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, list):
        return {"series": data, "meta": {}}
    return {
        "series": data.get("series", data) or [],
        "meta": data.get("meta", {}),
    }


def load_meta() -> dict:
    if not META_PATH.exists():
        return {"assets": [a["id"] for a in ASSETS], "updatedAt": None}
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def fetch_close(symbol: str, market: str, now=None, retries: int = 2):
    """Return only the expected completed session, with its original date."""
    target, _ = session_target(market, now)
    start = (date.fromisoformat(target) - timedelta(days=35)).isoformat()
    for attempt in range(retries):
        try:
            df = fdr.DataReader(symbol, start, target)
            if df is not None and not df.empty and "Close" in df.columns:
                for stamp, price in df["Close"].items():
                    if stamp.date().isoformat() == target and valid_price(price):
                        return target, float(price)
        except Exception:
            print(f"  warn: {symbol} provider failed (attempt {attempt + 1})")
        if attempt < retries - 1:
            time.sleep(DELAY_BETWEEN_SYMBOLS)
    print(f"  warn: {symbol}: no valid confirmed close for {target}")
    return None


def fetch_prices(market: str, now=None) -> list:
    market = market.upper()
    if market not in {"KR", "US", "BOTH"}:
        raise ValueError("MARKET must be kr, us or both")
    rows = {}
    for asset in ASSETS:
        if market != "BOTH" and asset["market"] != market:
            continue
        quote = fetch_close(asset["symbol"], asset["market"], now)
        time.sleep(DELAY_BETWEEN_SYMBOLS)
        if quote is None:
            continue
        day, price = quote
        row = rows.setdefault(day, {"date": day, "kr": {}, "us": {}})
        if asset["market"] == "KR":
            row["kr"][asset["id"]] = round(price)
            row["us"][asset["id"]] = round(price)
        else:
            row["us"][asset["id"]] = round(price, 2)
    return [rows[day] for day in sorted(rows)]


def merge_row(existing: dict, new_row: dict) -> None:
    series = existing["series"]
    for row in series:
        if row.get("date") == new_row["date"]:
            for point in ("kr", "us"):
                row[point] = {**(row.get(point) or {}), **new_row[point]}
            break
    else:
        series.append(new_row)
    series.sort(key=lambda row: row["date"])


def main() -> None:
    market = os.environ.get("MARKET", "both").lower()
    existing = load_existing()

    rows = fetch_prices(market)
    if not rows:
        raise SystemExit("No confirmed closes; cached data and updatedAt unchanged.")
    before = json.dumps(existing, sort_keys=True)
    for row in rows:
        merge_row(existing, row)
    if json.dumps(existing, sort_keys=True) == before:
        print("Confirmed closes unchanged; cache timestamp preserved.")
        return

    existing["meta"] = existing.get("meta") or {}
    existing["meta"]["assets"] = existing["meta"].get("assets") or [a["id"] for a in ASSETS]
    existing["meta"]["start"] = existing["meta"].get("start") or existing["series"][0]["date"]
    existing["meta"]["end"] = existing["series"][-1]["date"]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PRICES_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    meta = load_meta()
    # Only verified fetched assets gain a date; legacy cache dates are not inferred.
    as_of = meta.setdefault("priceAsOf", {})
    for row in rows:
        for asset in ASSETS:
            point = "kr" if asset["market"] == "KR" else "us"
            if asset["id"] in row[point]:
                as_of[asset["id"]] = row["date"]
    meta["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    print("Updated prices.json:", len(existing["series"]), "rows, end:", existing["meta"]["end"])


if __name__ == "__main__":
    main()
