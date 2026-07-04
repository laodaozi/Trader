#!/usr/bin/env python3
"""
生成 run_manifest.json — 每次盘前 cron 后执行，记录各任务新鲜度与状态。
输出: /opt/trader/output/contracts/run_manifest.json
用法: python3 generate_manifest.py
"""
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path("/opt/cycleradar-trader")
CONTRACTS_DIR = Path("/opt/trader/output/contracts")
LOG_DIR = PROJECT_ROOT / "data" / "logs"

def file_age_hours(p):
    if not p.exists():
        return None
    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    return round((now - mtime).total_seconds() / 3600, 1)

def check_task(name, file, stale_hours, log_file=None):
    age = file_age_hours(file)
    if age is None:
        status = "missing"
        error = f"文件不存在: {file.name}"
        record_count = 0
    elif age > stale_hours:
        status = "stale"
        error = f"上次更新 {age}h 前 (>{stale_hours}h 视为过期)"
        record_count = _count_records(file)
    else:
        status = "ok"
        error = None
        record_count = _count_records(file)

    result = {
        "name": name,
        "status": status,
        "file": str(file),
        "age_hours": age,
        "stale_threshold_hours": stale_hours,
        "record_count": record_count,
    }
    if error:
        result["error"] = error
    # 读 log 文件最后一行时间
    if log_file and log_file.exists():
        try:
            lines = log_file.read_text(encoding='utf-8', errors='ignore').strip().splitlines()
            result["last_log"] = lines[-1][:100] if lines else None
        except Exception:
            pass
    return result

def _count_records(p: Path) -> int:
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            for key in ('signals', 'events', 'stocks'):
                if key in data and isinstance(data[key], list):
                    return len(data[key])
            return 1
    except Exception:
        return 0

def _wewe_rss_age_hours():
    """读 wewe-rss.db 最新文章时间，返回距今小时数。"""
    import sqlite3, time
    db_path = Path("/opt/wewe-rss-deploy/data/wewe-rss.db")
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT MAX(publish_time) FROM articles").fetchone()
        conn.close()
        if row and row[0]:
            age = (time.time() - row[0]) / 3600
            return round(age, 1)
    except Exception:
        pass
    return None

def check_wewe_rss():
    """wewe-rss 专用检查：7天内算 ok，不搞日度提醒。"""
    age = _wewe_rss_age_hours()
    stale_threshold = 168  # 7天
    if age is None:
        return {"name": "wewe_rss", "status": "missing", "age_hours": None,
                "stale_threshold_hours": stale_threshold, "record_count": 0,
                "error": "wewe-rss.db 不存在"}
    status = "ok" if age <= stale_threshold else "stale"
    result = {"name": "wewe_rss", "status": status, "age_hours": age,
              "stale_threshold_hours": stale_threshold, "record_count": 0}
    if status == "stale":
        days = round(age / 24, 1)
        result["error"] = f"停更 {days}天（>{stale_threshold//24}天视为过期）"
    return result

def main():
    now = datetime.now()
    run_id = now.strftime("RUN-%Y%m%d-%H%M")
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%S")

    tasks = [
        check_task(
            "timing",
            PROJECT_ROOT / "data" / "timing_history.json",
            stale_hours=28,
            log_file=LOG_DIR / "timing_cron.log"
        ),
        check_task(
            "enrich_nightly",
            PROJECT_ROOT / "data" / "hot_enrichment.json",
            stale_hours=28,
        ),
        check_task(
            "generate_contracts",
            CONTRACTS_DIR / "alpha_latest.json",
            stale_hours=28,
        ),
        check_task(
            "event_narrative",
            CONTRACTS_DIR / "event_narrative_latest.json",
            stale_hours=28,
        ),
        check_task(
            "rotation_snapshot",
            PROJECT_ROOT / "data" / "rotation_snapshot.json",
            stale_hours=72,  # 轮动快照复盘驱动，允许3天内
        ),
        check_task(
            "watchlist_signals",
            CONTRACTS_DIR / "watchlist_signals.json",
            stale_hours=28,
        ),
        check_wewe_rss(),
    ]

    ok    = sum(1 for t in tasks if t["status"] == "ok")
    stale = sum(1 for t in tasks if t["status"] == "stale")
    error = sum(1 for t in tasks if t["status"] in ("missing", "error"))

    manifest = {
        "run_id": run_id,
        "generated_at": generated_at,
        "summary": {
            "total": len(tasks),
            "ok": ok,
            "stale": stale,
            "error": error,
        },
        "tasks": tasks,
    }

    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    out = CONTRACTS_DIR / "run_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"[manifest] {run_id}: {ok}/{len(tasks)} ok, {stale} stale, {error} error → {out}")
    return manifest

if __name__ == "__main__":
    main()
