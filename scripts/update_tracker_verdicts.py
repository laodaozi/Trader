#!/usr/bin/env python3.9
"""Update tracker verdicts: fetch latest prices and mark HIT/MISS/EXPIRED/PENDING."""

import json, sys, os
from datetime import datetime, date, timedelta
import requests

TRACKER_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "trader_tracker.jsonl")
TODAY = date.today()

def fetch_sina_prices(codes):
    """Fetch latest prices for given stock codes via Sina API."""
    sina_codes = []
    code_prefix = {}
    for c in codes:
        prefix = "sh" if c.startswith(("6", "5", "9")) else "sz"
        sina_codes.append(prefix + c)
        code_prefix[prefix + c] = c

    url = "http://hq.sinajs.cn/list=" + ",".join(sina_codes)
    headers = {"Referer": "https://finance.sina.com.cn"}

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.encoding = "gbk"
    except Exception as e:
        print("[ERROR] Sina API failed: {}".format(e), file=sys.stderr)
        return {}

    prices = {}
    for line in r.text.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        code_part, data_part = line.split("=", 1)
        sc = code_part.replace("var hq_str_", "")
        code = code_prefix.get(sc)
        if not code:
            continue
        values = data_part.strip("\"").split(",")
        if len(values) >= 32:
            try:
                prices[code] = {
                    "price": float(values[3]) if values[3] else None,
                    "high": float(values[4]) if values[4] else None,
                    "low": float(values[5]) if values[5] else None,
                    "open": float(values[1]) if values[1] else None,
                    "data_date": values[30],
                }
            except (ValueError, IndexError):
                pass
    return prices


def compute_verdict(rec, price_data):
    """Determine HIT / MISS / EXPIRED / PENDING for one tracker record."""
    code = rec.get("code")
    entry = rec.get("entry")
    stop = rec.get("stop")
    targets = rec.get("targets") or []
    signal_date_str = rec.get("signal_date")
    horizon = rec.get("horizon", 5)

    # Parse signal date
    try:
        signal_date = datetime.strptime(signal_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return "PENDING", None

    expiry_date = signal_date + timedelta(days=horizon)

    # Check expiry first
    if TODAY > expiry_date:
        return "EXPIRED", None

    # Get current price
    cur = price_data.get(code, {})
    price = cur.get("price")
    high = cur.get("high")
    low = cur.get("low")

    if price is None:
        return "PENDING", None  # no price data yet

    # Check HIT (take-profit target reached)
    if targets and targets[0]:
        target = targets[0]
        if high and high >= target:
            return "HIT", high
        if price >= target:
            return "HIT", price

    # Check MISS (stop-loss triggered)
    if stop and low and low <= stop:
        return "MISS", low
    if stop and price <= stop:
        return "MISS", price

    # Check if expired today (last day, no HIT/MISS)
    if TODAY >= expiry_date:
        if entry and price:
            pct = (price - entry) / entry * 100
            return "NEUTRAL", round(pct, 1)
        return "EXPIRED", None

    return "PENDING", None


def main():
    records = []
    with open(TRACKER_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    print("Loaded {} tracker records".format(len(records)))

    codes = sorted(set(r.get("code") for r in records if r.get("code")))
    print("Unique codes: {}".format(len(codes)))

    prices = fetch_sina_prices(codes)
    print("Fetched prices for {}/{} codes".format(len(prices), len(codes)))

    if prices:
        sample_dates = set(p.get("data_date") for p in prices.values() if p.get("data_date"))
        print("Data date(s): {}".format(sample_dates))

    stats = {"HIT": 0, "MISS": 0, "EXPIRED": 0, "NEUTRAL": 0, "PENDING": 0}
    changed = 0

    for rec in records:
        old = rec.get("result")
        verdict, detail = compute_verdict(rec, prices)

        if verdict != old:
            rec["result"] = verdict
            if detail is not None:
                rec["detail"] = detail
            msg = "  {} {}: {} -> {}".format(rec['code'], rec.get('name', '?'), old, verdict)
            if detail is not None:
                msg = msg + " ({})".format(detail)
            print(msg)
            changed += 1

        stats[verdict] = stats.get(verdict, 0) + 1

    # Write back
    with open(TRACKER_PATH, "w") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("\nWrote {} records ({} changed)".format(len(records), changed))
    print("Results: {}".format(dict(stats)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
