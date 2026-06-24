import requests, json, time

def fetch_stock_price(code):
    market = 1 if code.startswith('6') else 0
    secid = '{}.{}'.format(market, code)
    url = 'http://push2.eastmoney.com/api/qt/stock/get'
    params = {
        'secid': secid,
        'fields': 'f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f116,f117,f169,f170'
    }
    r = requests.get(url, params=params, timeout=10)
    d = r.json().get('data', {})
    if d is None:
        return None
    return {
        'price': d.get('f43'),
        'high': d.get('f44'),
        'low': d.get('f45'),
        'chg_pct': d.get('f170'),
    }

codes_set = set()
with open('/opt/cycleradar-trader/data/trader_tracker.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            codes_set.add(json.loads(line)['code'])

codes = sorted(codes_set)
print('Total codes: {}'.format(len(codes)))

ok, fail = 0, 0
for c in codes:
    try:
        p = fetch_stock_price(c)
        if p and p.get('price'):
            print('  {} price={} high={} low={} chg={}%'.format(
                c, p['price'], p['high'], p['low'], p['chg_pct']))
            ok += 1
        else:
            print('  {} no data'.format(c))
            fail += 1
    except Exception as e:
        print('  {} ERROR: {}'.format(c, e))
        fail += 1
    time.sleep(0.1)

print('OK={} FAIL={}'.format(ok, fail))
