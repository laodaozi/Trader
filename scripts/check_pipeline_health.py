#!/usr/bin/env python3.9
"""check_pipeline_health.py — scan artifacts and write summary"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/lib')
from pipeline_health import write_artifact_health, write_job_health, get_health_summary
import json, subprocess

DATA = "/opt/cycleradar-trader/data"

ARTIFACTS = [
    ("timing",     f"{DATA}/timing_history.json",     [], 10, 24),
    ("alpha_full", f"{DATA}/alpha_latest.json",         [], 10, 36),
    ("narrative",  f"{DATA}/event_narrative_latest.json",[], 1, 36),
    ("scanner",    f"{DATA}/upstream_signals.jsonl",    ["signal_id","strategy","direction"], 3, 24),
    ("rotation",   f"{DATA}/rotation_snapshot.json",    [], 0, 24),
    ("positions",  f"{DATA}/positions.json",            [], 0, 168),
    ("pulse",      f"{DATA}/pulse_latest.json",         ["verdict","timing","scanner","alpha"], 1, 12),
]

def check_system():
    try:
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
        parts = r.stdout.strip().split('\n')[-1].split()
        disk_pct = int(parts[4].replace('%','')) if len(parts) >= 5 else 0
        r = subprocess.run(["free"], capture_output=True, text=True)
        mem_parts = r.stdout.strip().split('\n')[1].split()
        mem_total = int(mem_parts[1]) if len(mem_parts) >= 2 else 0
        mem_used = int(mem_parts[2]) if len(mem_parts) >= 3 else 0
        mem_pct = round(mem_used / max(mem_total, 1) * 100, 1)
        with open("/proc/loadavg") as f:
            load1 = float(f.read().strip().split()[0])
        status = "WARN" if (disk_pct > 85 or mem_pct > 90 or load1 > 4) else "OK"
        write_job_health("system", status, metrics={"disk_pct": disk_pct, "mem_pct": mem_pct, "load_1m": load1})
    except Exception as e:
        write_job_health("system", "WARN", error=str(e))

def main():
    for name, path, fields, min_rec, age_hrs in ARTIFACTS:
        write_artifact_health(name, path, required_fields=fields, min_records=min_rec, max_age_hours=age_hrs)
    check_system()
    summary = get_health_summary()
    summary_path = os.path.join(DATA, "..", "admin", "health", "summary_latest.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
