#!/usr/bin/env python3.9
"""
ingest_db.py — source_articles DB 管理器（供 Node.js execFile 调用）

用法：
  python3.9 ingest_db.py init
  python3.9 ingest_db.py status <date>
  python3.9 ingest_db.py upsert <source_id> <date> <url>
  python3.9 ingest_db.py save_result <source_id> <date> <json_result>
  python3.9 ingest_db.py manual <source_id> <date> <title> <content_text>
  python3.9 ingest_db.py get_url <source_id> <date>
"""
import sys, json, sqlite3, os
from datetime import datetime, timezone, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'source_articles.db')

SOURCES = [
    {'id':'MP_WXS_3988180000','name':'叙事平权','tier':'S','weight':1.0,'min_len':3000},
    {'id':'MP_WXS_3242358265','name':'微策神机','tier':'A','weight':0.9,'min_len':500},
    {'id':'MP_WXS_3233243226','name':'财闻私享','tier':'A','weight':0.9,'min_len':500},
    {'id':'MP_WXS_3583532298','name':'在下杜牛牛','tier':'A','weight':0.85,'min_len':500},
    {'id':'MP_WXS_2398512110','name':'财经早餐','tier':'B','weight':0.6,'min_len':200},
    {'id':'MP_WXS_3080543482','name':'数据宝','tier':'B','weight':0.6,'min_len':200},
    {'id':'MP_WXS_3521606446','name':'小马白话期权','tier':'B','weight':0.5,'min_len':200},
    {'id':'MP_WXS_3191151316','name':'台球之门','tier':'C','weight':0.4,'min_len':200},
    {'id':'MP_WXS_3901470107','name':'低吸波段王','tier':'C','weight':0.4,'min_len':200},
]
SOURCE_MAP = {s['id']: s for s in SOURCES}

def bj_now():
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS source_articles (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      source_id     TEXT NOT NULL,
      source_name   TEXT NOT NULL,
      tier          TEXT NOT NULL,
      weight        REAL NOT NULL,
      publish_date  TEXT NOT NULL,
      title         TEXT,
      url           TEXT,
      content_text  TEXT,
      content_len   INTEGER DEFAULT 0,
      fetch_status  TEXT DEFAULT 'pending',
      fetch_method  TEXT,
      fetch_error   TEXT,
      fetched_at    TEXT,
      created_at    TEXT DEFAULT (datetime('now','+8 hours')),
      updated_at    TEXT DEFAULT (datetime('now','+8 hours')),
      UNIQUE(source_id, publish_date)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS fetch_attempts (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      source_id    TEXT NOT NULL,
      publish_date TEXT NOT NULL,
      method       TEXT, status TEXT,
      content_len  INTEGER DEFAULT 0,
      error        TEXT, elapsed_ms INTEGER DEFAULT 0,
      attempted_at TEXT DEFAULT (datetime('now','+8 hours'))
    )''')
    db.commit()
    return db

def cmd_init():
    get_db()
    print(json.dumps({'ok': True}))

def cmd_status(date):
    db = get_db()
    rows = {r['source_id']: dict(r) for r in
            db.execute('SELECT * FROM source_articles WHERE publish_date=?', (date,)).fetchall()}
    result = []
    for s in SOURCES:
        row = rows.get(s['id'])
        entry = {**s}
        if row:
            entry['article'] = {k: row[k] for k in row if k not in ('content_text',)}
            entry['status'] = row['fetch_status']
            entry['content_len'] = row['content_len'] or 0
            entry['preview'] = (row['content_text'] or '')[:200]
        else:
            entry['article'] = None
            entry['status'] = 'missing'
            entry['content_len'] = 0
            entry['preview'] = ''
        result.append(entry)
    print(json.dumps({'date': date, 'sources': result}, ensure_ascii=False))

def cmd_upsert(source_id, date, url):
    src = SOURCE_MAP.get(source_id)
    if not src:
        print(json.dumps({'ok': False, 'error': f'未知信源 {source_id}'}))
        return
    db = get_db()
    db.execute('''INSERT INTO source_articles (source_id,source_name,tier,weight,publish_date,url,fetch_status)
      VALUES (?,?,?,?,?,?,'pending')
      ON CONFLICT(source_id,publish_date) DO UPDATE SET
        url=excluded.url, fetch_status='pending', updated_at=?
    ''', (src['id'],src['name'],src['tier'],src['weight'],date,url,bj_now()))
    db.commit()
    print(json.dumps({'ok': True}))

def cmd_save_result(source_id, date, result_json):
    r = json.loads(result_json)
    db = get_db()
    now = bj_now()
    if r.get('status') == 'success':
        db.execute('''UPDATE source_articles SET
            title=?, content_text=?, content_len=?,
            fetch_status='success', fetch_method=?, fetched_at=?,
            fetch_error=NULL, updated_at=?
          WHERE source_id=? AND publish_date=?''',
          (r.get('title',''), r.get('content_text',''), r.get('content_len',0),
           r.get('method','http'), now, now, source_id, date))
    else:
        db.execute('''UPDATE source_articles SET
            fetch_status=?, fetch_error=?, fetch_method=?, updated_at=?
          WHERE source_id=? AND publish_date=?''',
          (r.get('status','failed'), r.get('error',''), r.get('method','http'),
           now, source_id, date))
    db.execute('''INSERT INTO fetch_attempts (source_id,publish_date,method,status,content_len,error,elapsed_ms)
      VALUES (?,?,?,?,?,?,?)''',
      (source_id, date, r.get('method','http'), r.get('status','failed'),
       r.get('content_len',0), r.get('error',''), r.get('elapsed_ms',0)))
    db.commit()
    print(json.dumps({'ok': True}))

def cmd_manual(source_id, date, title, content_text):
    src = SOURCE_MAP.get(source_id)
    if not src:
        print(json.dumps({'ok': False, 'error': f'未知信源 {source_id}'})); return
    db = get_db()
    now = bj_now()
    db.execute('''INSERT INTO source_articles
        (source_id,source_name,tier,weight,publish_date,title,content_text,content_len,fetch_status,fetch_method,fetched_at)
      VALUES (?,?,?,?,?,?,?,?,'success','manual',?)
      ON CONFLICT(source_id,publish_date) DO UPDATE SET
        title=excluded.title, content_text=excluded.content_text,
        content_len=excluded.content_len, fetch_status='success',
        fetch_method='manual', fetched_at=?, updated_at=?''',
      (src['id'],src['name'],src['tier'],src['weight'],date,title,content_text,len(content_text),now,now,now))
    db.commit()
    print(json.dumps({'ok': True}))

def cmd_get_url(source_id, date):
    db = get_db()
    row = db.execute('SELECT url FROM source_articles WHERE source_id=? AND publish_date=?',
                     (source_id,date)).fetchone()
    print(json.dumps({'url': row['url'] if row else None}))

def cmd_get_content(date):
    """供 enrich_hot_events.py 调用：返回当日所有成功的正文"""
    db = get_db()
    rows = db.execute('''SELECT source_id, source_name, tier, weight, title, content_text, content_len, fetch_method
      FROM source_articles WHERE publish_date=? AND fetch_status='success' AND content_len>0
      ORDER BY weight DESC''', (date,)).fetchall()
    result = [dict(r) for r in rows]
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'init'
    if cmd == 'init':       cmd_init()
    elif cmd == 'status':   cmd_status(sys.argv[2])
    elif cmd == 'upsert':   cmd_upsert(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == 'save_result': cmd_save_result(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == 'manual':   cmd_manual(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == 'get_url':  cmd_get_url(sys.argv[2], sys.argv[3])
    elif cmd == 'get_content': cmd_get_content(sys.argv[2])
    else: print(json.dumps({'error': f'unknown cmd {cmd}'}))
