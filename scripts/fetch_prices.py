import requests, json, time

def fetch_prices_via_clist(codes_needed, page_size=20):
    url = 'http://82.push2.eastmoney.com/api/qt/clist/get'
    prices = {}
    codes_left = set(codes_needed)
    
    for page in range(1, 10):
        if not codes_left:
            break
        try:
            params = {
                'pn': str(page), 'pz': str(page_size),
                'po': '0', 'np': '1', 'fltt': '2', 'invt': '2',
                'fid': 'f3',
                'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
                'fields': 'f2,f12,f14,f3',
            }
            r = requests.get(url, params=params, timeout=15)
            data = r.json()
            stocks = data.get('data', {}).get('diff', [])
            total = data.get('data', {}).get('total', 0)
            
            for s in stocks:
                code = s['f12']
                if code in codes_left:
                    prices[code] = {
                        'price': s['f2'],
                        'name': s['f14'],
                        'chg_pct': s.get('f3'),
                    }
                    codes_left.discard(code)
                    print('  [p{}] {} {}: {} ({})'.format(
                        page, code, s['f14'], s['f2'],
                        'chg=' + str(s.get('f3')) if s.get('f3') else 'N/A'))
            
            if page * page_size >= total:
                break
        except Exception as e:
            print('  [p{}] retry after error: {}'.format(page, str(e)[:80]))
        
        time.sleep(0.5)
    
    return prices

codes_set = set()
with open('/opt/cycleradar-trader/data/trader_tracker.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            codes_set.add(json.loads(line)['code'])

print('Target codes: {}'.format(len(codes_set)))
prices = fetch_prices_via_clist(codes_set, page_size=50)
print('Found: {}/{}'.format(len(prices), len(codes_set)))
missing = codes_set - set(prices.keys())
if missing:
    print('Missing: {} ({})'.format(len(missing), sorted(missing)[:10]))
