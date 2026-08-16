"""pipeline_health.py — cron job / artifact health tracker"""
from __future__ import annotations
import json, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))
DATA_DIR = Path(os.environ.get("CYCLERADAR_DATA_DIR", "/opt/cycleradar-trader/data"))
HEALTH_DIR = DATA_DIR / ".." / "admin" / "health"
JOBS_FILE = HEALTH_DIR / "jobs_latest.json"
ARTIFACTS_FILE = HEALTH_DIR / "artifacts_latest.json"

def _ensure(): HEALTH_DIR.mkdir(parents=True, exist_ok=True)
def _load(f): return json.load(open(f)) if f.exists() else {}
def _save(f, d):
    _ensure(); t = str(f) + ".tmp"
    json.dump(d, open(t,"w"), ensure_ascii=False, indent=2)
    os.replace(t, str(f))
def _now(): return datetime.now(TZ).isoformat()

def write_job_health(job, status, metrics=None, error=None, started=None, finished=None):
    d = _load(JOBS_FILE); entry = {"job": job, "status": status, "updated_at": finished or _now()}
    if started: entry["started_at"] = started
    if metrics: entry["metrics"] = metrics
    if error: entry["error"] = str(error)[:500]
    d[job] = entry; _save(JOBS_FILE, d)

def write_artifact_health(name, path, required_fields=None, min_records=0, max_age_hours=36):
    p = Path(path); now = datetime.now(TZ)
    if not p.exists():
        st, rec, ok, age, sz = "MISSING", 0, False, None, 0
    else:
        s = p.stat(); sz = s.st_size
        mtime = datetime.fromtimestamp(s.st_mtime, tz=TZ)
        age = (now - mtime).total_seconds() / 3600
        rec, ok = _inspect(p, required_fields or [])
        if age > max_age_hours: st = "STALE"
        elif rec == 0: st = "EMPTY"
        elif not ok: st = "MALFORMED"
        else: st = "FRESH"
    d = _load(ARTIFACTS_FILE)
    d[name] = {"artifact": name, "path": str(p), "status": st, "updated_at": _now(),
               "file_updated_at": datetime.fromtimestamp(p.stat().st_mtime, tz=TZ).isoformat() if p.exists() else None,
               "size_bytes": sz, "age_hours": round(age,1) if age else None, "records": rec, "fields_ok": ok}
    _save(ARTIFACTS_FILE, d)

def _inspect(p, req):
    rec, ok = 0, True
    try:
        if p.suffix == ".jsonl":
            for line in open(p):
                if not line.strip(): continue
                try:
                    o = json.loads(line.strip()); rec += 1
                    if req and rec == 1 and not _has_fields(o, req): ok = False
                except: ok = False; break
        elif p.suffix == ".json":
            data = json.load(open(p))
            if isinstance(data, list):
                rec = len(data)
                if rec > 0 and req and not _has_fields(data[0], req): ok = False
            elif isinstance(data, dict):
                rec = 1
                for key in ("history","signals","events","stocks","alpha_signals"):
                    if key in data and isinstance(data[key], list) and data[key]:
                        rec = max(rec, len(data[key]))
                        if req and not _has_fields(data[key][0], req):
                            ok = False
                            break
                if ok and req:
                    if not _has_fields(data, req):
                        for key in ("history","signals","events","stocks","alpha_signals"):
                            if key in data and isinstance(data[key], list) and data[key]:
                                if _has_fields(data[key][0], req):
                                    ok = True
                                    break
    except: ok = False
    return rec, ok

def _has_fields(obj, fields):
    return all(f in obj for f in fields)

def get_health_summary():
    jobs = _load(JOBS_FILE); arts = _load(ARTIFACTS_FILE)
    js, ac = {}, {}
    for j in jobs.values():
        s = j.get("status","UNKNOWN"); js[s] = js.get(s,0) + 1
    for a in arts.values():
        s = a.get("status","UNKNOWN"); ac[s] = ac.get(s,0) + 1
    if js.get("FAIL",0) > 0 or ac.get("MISSING",0) > 0: overall = "BLOCKED"
    elif js.get("WARN",0) > 0 or ac.get("STALE",0) > 0: overall = "DEGRADED"
    else: overall = "OK"
    return {"checked_at": _now(), "overall": overall,
            "jobs": {"total": len(jobs), "by_status": js,
                      "blocking": [j["job"] for j in jobs.values() if j.get("status")=="FAIL"]},
            "artifacts": {"total": len(arts), "by_status": ac,
                           "blocking": [a["artifact"] for a in arts.values() if a.get("status") in ("MISSING","MALFORMED")]}}
