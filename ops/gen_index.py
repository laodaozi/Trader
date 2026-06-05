#!/usr/bin/env python3
"""
ops/gen_index.py — 生成 output/daily/index.html（近30日报告列表）
"""
from pathlib import Path
from datetime import datetime

OUTPUT = Path(__file__).parent.parent / "output" / "daily"


def generate():
    reports = sorted(OUTPUT.glob("trader_????-??-??.html"), reverse=True)[:30]

    items = ""
    for r in reports:
        date = r.stem.replace("trader_", "")
        size = r.stat().st_size // 1024
        mtime = datetime.fromtimestamp(r.stat().st_mtime).strftime("%H:%M")
        items += f"""
    <a href="{r.name}" style="display:flex;justify-content:space-between;align-items:center;
       padding:12px 16px;background:#fff;border-radius:8px;margin-bottom:8px;
       text-decoration:none;color:#1e293b;box-shadow:0 1px 3px rgba(0,0,0,.08)">
      <span style="font-weight:600">{date}</span>
      <span style="color:#9ca3af;font-size:13px">{mtime} &nbsp;·&nbsp; {size} KB</span>
    </a>"""

    if not items:
        items = "<div style='color:#9ca3af;padding:20px'>暂无报告</div>"

    latest_date = reports[0].stem.replace("trader_", "") if reports else ""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="0; url=latest.html">
<title>交易员日报</title>
<style>
  * {{ box-sizing:border-box;margin:0;padding:0 }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;
         background:#f1f5f9;padding:20px;max-width:600px;margin:0 auto }}
</style>
</head>
<body>
  <div style="background:linear-gradient(135deg,#1e3a5f,#2563eb);color:#fff;
              border-radius:12px;padding:20px 24px;margin-bottom:20px">
    <div style="font-size:20px;font-weight:800">🤖 交易员日报</div>
    <div style="font-size:13px;opacity:.7;margin-top:4px">最新: {latest_date}</div>
  </div>
  <a href="latest.html" style="display:block;background:#2563eb;color:#fff;
     text-align:center;padding:14px;border-radius:10px;font-weight:700;
     font-size:16px;text-decoration:none;margin-bottom:16px">
    📊 查看今日报告
  </a>
  <div style="font-size:12px;color:#9ca3af;margin-bottom:10px">历史报告</div>
  {items}
  <div style="font-size:12px;color:#9ca3af;text-align:center;margin-top:16px">
    仅供参考，不构成投资建议
  </div>
</body>
</html>"""

    (OUTPUT / "index.html").write_text(html, encoding="utf-8")
    print(f"  [index] 已生成，共 {len(reports)} 份报告")


if __name__ == "__main__":
    generate()
