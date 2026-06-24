#!/usr/bin/env python3.9
"""akshare fallback data fetcher - outputs JSON for Node.js consumption"""
import sys
import json
import akshare as ak

def fetch_shibor(tenor="隔夜"):
    df = ak.rate_interbank(market='上海银行同业拆借市场', symbol='Shibor人民币', indicator=tenor)
    if df.empty:
        return {}
    row = df.iloc[-1]
    return {"date": str(row["报告日"]), "rate": float(row["利率"]), "change_bp": float(row["涨跌"])}

def fetch_m2():
    df = ak.macro_china_m2_yearly()
    if df.empty:
        return {}
    valid = df.dropna(subset=["今值"])
    if valid.empty:
        return {}
    row = valid.iloc[-1]
    return {"date": str(row["日期"]), "m2_yoy": float(row["今值"])}

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: akshare_fetch.py <shibor|shibor_all|m2>"}))
        sys.exit(1)

    cmd = sys.argv[1]
    result = {}

    try:
        if cmd == "shibor":
            result = fetch_shibor("隔夜")
        elif cmd == "shibor_all":
            result = {
                "shibor_on": fetch_shibor("隔夜"),
                "shibor_1w": fetch_shibor("1周"),
                "shibor_1m": fetch_shibor("1月"),
                "shibor_3m": fetch_shibor("3月"),
            }
        elif cmd == "m2":
            result = fetch_m2()
        else:
            print(json.dumps({"error": f"Unknown command: {cmd}"}))
            sys.exit(1)
    except Exception as e:
        result = {"error": str(e)}

    print(json.dumps(result, ensure_ascii=False, default=str))

if __name__ == "__main__":
    main()
