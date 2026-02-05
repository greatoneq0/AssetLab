#!/usr/bin/env python3
"""
한국장/미국장 종료 후 가격 데이터 갱신.

- GitHub Actions에서 1일 2회 실행 (한국장 종료 후, 미국장 종료 후).
- FinanceDataReader 사용: 한국 종목은 KRX 소스로 안정 조회, 미국은 심볼 직접 조회.
- 심볼당 요청 후 2초 대기로 차단 위험 최소화, 실패 시 재시도 1회.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
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

# 한국 종목: KRX 소스(FinanceDataReader)로 조회 시 안정적. 미국: 심볼만 지정
ASSETS = [
    {"id": "KODEX200", "market": "KR", "symbol": "KRX:069500"},
    {"id": "SPY", "market": "US", "symbol": "SPY"},
    {"id": "TLT", "market": "US", "symbol": "TLT"},
]


def today_str() -> str:
    return date.today().isoformat()


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


def fetch_close(symbol: str, retries: int = 2) -> float | None:
    """FinanceDataReader로 종가 조회. 한국은 KRX:, 미국은 심볼만."""
    end = date.today()
    start = end - timedelta(days=14)  # 휴장일 고려해 2주 구간
    start_str = start.isoformat()
    end_str = end.isoformat()
    for attempt in range(retries):
        try:
            df = fdr.DataReader(symbol, start_str, end_str)
            if df is None or df.empty:
                return None
            # DataFrame 컬럼: Close (단일 종목) 또는 MultiIndex
            if "Close" in df.columns:
                close = df["Close"].iloc[-1]
            else:
                close = df.iloc[-1].iloc[-1]  # 다중 종목 시 마지막 컬럼
            return float(close)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(DELAY_BETWEEN_SYMBOLS)
            else:
                print(f"  warn: {symbol} failed: {e}")
    return None


def fetch_prices(market: str) -> dict:
    """market: 'KR' | 'US' | 'both'. 필요한 심볼만 요청하고, 심볼당 DELAY 대기해 차단 위험 최소화."""
    today = today_str()
    row = {"date": today, "kr": {}, "us": {}}

    to_fetch = [
        a for a in ASSETS
        if market == "both" or (market == "KR" and a["market"] == "KR") or (market == "US" and a["market"] == "US")
    ]
    for asset in to_fetch:
        price = fetch_close(asset["symbol"])
        time.sleep(DELAY_BETWEEN_SYMBOLS)
        if price is None:
            continue
        if asset["market"] == "KR":
            row["kr"][asset["id"]] = round(price)
            row["us"][asset["id"]] = row["kr"][asset["id"]]
        else:
            row["us"][asset["id"]] = round(price * 100) / 100
            row["kr"][asset["id"]] = None  # 미국 자산은 한국장 시점 미갱신

    return row


def merge_row(existing: dict, new_row: dict) -> None:
    series = existing["series"]
    if not series:
        series.append(new_row)
        return
    last = series[-1]
    if last.get("date") == new_row.get("date"):
        for point in ("kr", "us"):
            last[point] = {**(last.get(point) or {}), **(new_row.get(point) or {})}
    else:
        series.append(new_row)


def main() -> None:
    market = os.environ.get("MARKET", "both").lower()
    existing = load_existing()

    if market == "both":
        row = fetch_prices("both")
        if row.get("kr") or row.get("us"):
            merge_row(existing, row)
    else:
        row = fetch_prices(market)
        if (market == "kr" and row.get("kr")) or (market == "us" and row.get("us")):
            merge_row(existing, row)

    if not existing["series"]:
        print("No new data; prices.json unchanged.")
        return

    existing["meta"] = existing.get("meta") or {}
    existing["meta"]["assets"] = existing["meta"].get("assets") or [a["id"] for a in ASSETS]
    existing["meta"]["start"] = existing["meta"].get("start") or existing["series"][0]["date"]
    existing["meta"]["end"] = existing["series"][-1]["date"]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PRICES_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = load_meta()
    meta["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Updated prices.json:", len(existing["series"]), "rows, end:", existing["meta"]["end"])


if __name__ == "__main__":
    main()
