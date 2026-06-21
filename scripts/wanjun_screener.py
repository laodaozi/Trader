#!/usr/bin/env python3
"""
万军量化选股筛选脚本 — Claude Code 交互式调用
==============================================
实现 11 个可自动执行的模型（v0.2）：
  模型  1: 钱坤寻龙（STUB — 需龙虎榜数据）
  模型  2: 向上缺口
  模型  3: 回调狙击
  模型  4: 向上缺口（scanner版）
  模型  5: 中线狙击
  模型  6: 波段雄鹰
  模型  7: 弱转强
  模型  8: 低开接力
  模型  9: 涨停回踩
  模型 10: 主升狙击
  模型 11: 均线共振

用法：
  python wanjun_screener.py                    # 跑全部 11 个模型
  python wanjun_screener.py --model 2          # 只跑模型 2
  python wanjun_screener.py --model 2,8,10     # 跑指定模型
  python wanjun_screener.py --jsonl            # 输出 JSONL 格式（写入 upstream_signals）

数据源：新浪spot分页 + 腾讯qfqday日线（AKShare已被东方财富封禁，ECS不可达）
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
import requests


# ─── 配置 ────────────────────────────────────────────────

# 默认排除
EXCLUDE_PREFIXES = ('ST', '*ST', 'N', 'C')          # ST / 新股 / 次新股
EXCLUDE_SECTORS  = ('北证', 'B股')                   # 北交所 / B 股

# 模型 2 阈值
M2_GAP_MIN      = 0.01      # 跳空 ≥ 1%
M2_INTRADAY_MIN = 0.05      # 盘中涨幅 ≥ 5%
M2_SHADOW_MAX   = 0.02      # 下影线 ≤ 2%
M2_CHANGE_MAX   = 0.08      # 涨幅 < 8%（避免追高）
M2_VOL_RATIO    = 0.85      # 前日量 < 5日均量 * 0.85（缩量）

# 模型 8 阈值
M8_GAP_MIN      = -0.02     # 低开 ≥ 2%（gap_up_pct ≤ -2%）
M8_CHANGE_MIN   = 0.0       # 收盘涨幅 ≥ 0%
M8_VOL_RATIO    = 0.85      # 当日量 < 5日均量 * 0.85（缩量）
M8_MA_WINDOWS   = [5, 10, 21]  # 多头排列 MA5 > MA10 > MA21

# 模型 10 阈值
M10_MA_WINDOWS  = [5, 10, 21, 60, 120, 250]  # 全线均线
M10_VOL_SURGE   = 1.5       # 当日量 > 10日均量 * 1.5

# 候选预筛选
PRE_FILTER_GAP_UP    = 0.01    # 跳空高开 ≥ 1%（模型2候选）
PRE_FILTER_GAP_DOWN  = -0.02   # 跳空低开 ≤ -2%（模型8候选）
PRE_FILTER_CHANGE    = 0.03    # 涨 ≥ 3%（模型7/10候选）
PRE_FILTER_VOL_SURGE = 1.2     # 量比 > 1.2（模型10备选）
PRE_FILTER_MA_LONG   = 0.02    # 涨 > 2%（模型5备选）


# ─── 数据获取 ─────────────────────────────────────────────

SINA_SPOT_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/Market_Center.getHQNodeData"
)
SINA_PAGE_SIZE = 100
SINA_MAX_PAGES = 60


def fetch_spot_data() -> pd.DataFrame:
    """获取全 A 股实时行情（新浪spot分页），返回 DataFrame。

    替代原 AKShare stock_zh_a_spot_em()（东方财富API ECS被封）。
    新浪接口免费、无需认证、ECS可达，全量 ~5527 只约 26 秒。
    """
    print("[数据] 获取全 A 股行情（新浪分页）...", file=sys.stderr)
    all_rows = []
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Referer": "https://vip.stock.finance.sina.com.cn/",
    })

    for page in range(1, SINA_MAX_PAGES + 1):
        params = {
            "page": page,
            "num": SINA_PAGE_SIZE,
            "sort": "symbol",
            "node": "hs_a",
        }
        try:
            resp = session.get(SINA_SPOT_URL, params=params, timeout=15)
            resp.raise_for_status()
            # 新浪有时返回非JSON（如空页或编码问题）
            raw = resp.text
            if not raw.strip() or raw.strip().startswith("null"):
                break  # 分页结束
            page_data = json.loads(raw)
            if not isinstance(page_data, list) or len(page_data) == 0:
                break
            all_rows.extend(page_data)
            if len(page_data) < SINA_PAGE_SIZE:
                break  # 最后一页不足100条
        except json.JSONDecodeError:
            print(f"  [WARN] 第{page}页JSON解析失败，跳过", file=sys.stderr)
            continue
        except requests.RequestException as e:
            print(f"  [WARN] 第{page}页请求失败: {e}，跳过", file=sys.stderr)
            continue

    if not all_rows:
        print("[ERROR] 新浪spot数据为空，请检查网络", file=sys.stderr)
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)

    # 标准化列名（新浪 → 内部统一字段）
    col_map = {
        'symbol': 'symbol',         # 如 "sz000001" → keep, 含交易所前缀
        'code': 'code',             # 如 "000001"
        'name': 'name',
        'trade': 'close',           # 最新价
        'open': 'open',
        'high': 'high',
        'low': 'low',
        'settlement': 'pre_close',  # 昨收
        'volume': 'volume',
        'amount': 'amount',
        'changepercent': 'change_pct',  # 涨跌幅（已为百分比数值,如 -1.445）
        'turnoverratio': 'turnover_rate',
        'per': 'pe',
        'pb': 'pb',
    }
    df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)

    # 确保必要列存在
    for col in ['close', 'open', 'high', 'low', 'pre_close', 'change_pct', 'volume']:
        if col not in df.columns:
            df[col] = np.nan

    # 类型转换
    for col in ['close', 'open', 'high', 'low', 'pre_close', 'change_pct',
                'volume', 'amount', 'turnover_rate', 'pe', 'pb']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # 新浪 changepercent 为百分比数值（-1.445 = -1.445%），转为小数（-0.01445）
    if 'change_pct' in df.columns:
        df['change_pct'] = df['change_pct'] / 100.0

    # 排除 ST / 新股 / 北交所 / B 股
    mask = pd.Series(True, index=df.index)
    for prefix in EXCLUDE_PREFIXES:
        mask &= ~df['name'].str.startswith(prefix)
    for kw in EXCLUDE_SECTORS:
        if 'name' in df.columns:
            mask &= ~df['name'].str.contains(kw, na=False)
        if 'symbol' in df.columns:
            mask &= ~df['symbol'].str.lower().str.startswith(f'bj', na=False)

    df = df[mask]

    # 排除无有效数据
    df = df.dropna(subset=['close', 'open', 'high', 'low', 'pre_close'])

    print(f"[数据] spot 共 {len(df)} 只有效标的", file=sys.stderr)
    return df


def compute_spot_derivations(df: pd.DataFrame) -> pd.DataFrame:
    """在 spot 数据上计算衍生指标。"""
    df = df.copy()

    # 实体 / 影线
    df['amplitude']     = (df['high'] - df['low']) / df['pre_close']
    df['body_pct']      = (df['close'] - df['open']) / df['pre_close']
    df['upper_shadow']  = (df['high'] - df[['open', 'close']].max(axis=1)) / df['pre_close']
    df['lower_shadow']  = (df[['open', 'close']].min(axis=1) - df['low']) / df['pre_close']
    df['is_yang']       = df['close'] > df['open']
    df['gap_up_pct']    = (df['open'] - df['pre_close']) / df['pre_close']
    df['intraday_pct']  = (df['high'] - df['pre_close']) / df['pre_close']

    return df


TENCENT_KL_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_HIST_DELAY = 0.15  # 每个请求间隔150ms，避免被腾讯限流


def _parse_tencent_symbol(sym: str) -> Tuple[str, str]:
    """解析 symbol → (exchange, code)，如 'sz000001' → ('sz', '000001')。"""
    if re.match(r'^[a-z]{2}\d+', sym, re.IGNORECASE):
        return sym[:2].lower(), sym[2:]
    # 不带前缀的，根据首数字推断
    if sym.startswith(('0', '3', '2')):
        return 'sz', sym
    elif sym.startswith(('6', '5', '9')):
        return 'sh', sym
    return 'sz', sym  # fallback


def fetch_hist_batch(symbols: List[str], days: int = 260) -> Dict[str, pd.DataFrame]:
    """批量获取历史日线数据（腾讯 qfqday 前复权）。

    替代原 AKShare stock_zh_a_hist()（东方财富API ECS被封）。
    腾讯接口免费、无需认证、ECS可达，按只逐次调用 ~150ms/只。
    """
    print(f"[数据] 拉取 {len(symbols)} 只标的 {days} 日历史K线（腾讯 qfqday）...",
          file=sys.stderr)
    result = {}
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://gu.qq.com/",
    })

    for i, sym in enumerate(symbols):
        try:
            exchange, code = _parse_tencent_symbol(sym)
            param = f"{exchange}{code},day,,,{days},qfq"
            resp = session.get(TENCENT_KL_URL, params={
                "_var": "kline_dayqfq",
                "param": param,
            }, timeout=10)
            resp.raise_for_status()
            text = resp.text

            # 腾讯返回格式: kline_dayqfq={...}
            if not text.startswith("kline_dayqfq="):
                raise ValueError(f"非预期响应: {text[:100]}")
            payload = json.loads(text[len("kline_dayqfq="):])
            code_num = payload.get("code", -1)
            if code_num != 0:
                raise ValueError(f"腾讯API返回code={code_num}, msg={payload.get('msg','?')}")

            qfq_arr = payload.get("data", {}).get(f"{exchange}{code}", {}).get("qfqday")
            if not qfq_arr:
                raise ValueError("qfqday 数组为空")

            # qfqday 格式: [date, open, close, high, low, volume] — 注意 close 在 index 2
            rows = []
            for row in qfq_arr:
                if len(row) < 6:
                    continue
                rows.append({
                    "date": row[0],
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]),
                })
            if not rows:
                raise ValueError("qfqday 无有效行")

            df_hist = pd.DataFrame(rows)
            df_hist["date"] = pd.to_datetime(df_hist["date"])
            df_hist = df_hist.sort_values("date").tail(days)
            result[sym] = df_hist

            if (i + 1) % 20 == 0:
                print(f"  ... {i+1}/{len(symbols)}", file=sys.stderr)
        except Exception as e:
            print(f"  [WARN] {sym} 历史数据获取失败: {e}", file=sys.stderr)

        if TENCENT_HIST_DELAY > 0:
            time.sleep(TENCENT_HIST_DELAY)

    print(f"[数据] 成功获取 {len(result)} 只历史数据", file=sys.stderr)
    return result


# ─── 模型检查 ─────────────────────────────────────────────

def compute_mas(hist: pd.DataFrame, windows: List[int]) -> Dict[int, pd.Series]:
    """计算多个窗口的移动均线。"""
    mas = {}
    for w in windows:
        mas[w] = hist['close'].rolling(w).mean()
    return mas


def prev_day_volume_ratio(hist: pd.DataFrame) -> float:
    """计算前一日成交量 / 前5日均量。"""
    if len(hist) < 7:
        return 1.0
    prev_vol = hist['volume'].iloc[-2]
    avg5 = hist['volume'].iloc[-7:-2].mean()
    return prev_vol / avg5 if avg5 > 0 else 1.0


def check_model_2(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 2：向上缺口
    跳空高开未回补 + 盘中涨≥5% + 前日缩量 + 涨幅<8%
    """
    failures = []

    # 2.1 跳空幅度
    gap = row['gap_up_pct']
    if gap < M2_GAP_MIN:
        failures.append(f"跳空{ gap*100:.1f}%<{M2_GAP_MIN*100}%")

    # 2.2 全天未回补
    if row['low'] <= row['pre_close']:
        failures.append(f"盘中回补缺口 low={row['low']:.2f}≤pre={row['pre_close']:.2f}")

    # 2.3 盘中涨幅
    intraday = row['intraday_pct']
    if intraday < M2_INTRADAY_MIN:
        failures.append(f"盘中涨幅{ intraday*100:.1f}%<{M2_INTRADAY_MIN*100}%")

    # 2.4 下影线
    shadow = row['lower_shadow']
    if shadow > M2_SHADOW_MAX:
        failures.append(f"下影线{ shadow*100:.1f}%>{M2_SHADOW_MAX*100}%")

    # 2.5 当前涨幅
    chg = row['change_pct']
    if chg >= M2_CHANGE_MAX:
        failures.append(f"涨幅{ chg*100:.1f}%≥{M2_CHANGE_MAX*100}%")

    # 2.6 前日缩量
    prev_vr = prev_day_volume_ratio(hist) if hist is not None and len(hist) >= 7 else 1.0
    if prev_vr > M2_VOL_RATIO:
        failures.append(f"前日量比{ prev_vr:.2f}>{M2_VOL_RATIO}")

    passed = len(failures) == 0
    return passed, "; ".join(failures) if not passed else ""


def check_model_8(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 8：低开接力
    多头排列 + 大幅低开高走收阳 + 缩量 + 守5日线
    """
    failures = []

    # 8.1 均线多头排列
    if hist is None or len(hist) < max(M8_MA_WINDOWS) + 1:
        return False, "历史数据不足"
    mas = compute_mas(hist, M8_MA_WINDOWS)
    ma_values = [mas[w].iloc[-1] for w in M8_MA_WINDOWS]
    for i in range(len(ma_values) - 1):
        if pd.isna(ma_values[i]) or pd.isna(ma_values[i+1]) or ma_values[i] <= ma_values[i+1]:
            failures.append(f"MA{M8_MA_WINDOWS[i]}≤MA{M8_MA_WINDOWS[i+1]} 非多头")
            break

    # 8.2 低开幅度
    gap = row['gap_up_pct']
    if gap > M8_GAP_MIN:
        failures.append(f"低开{ gap*100:.1f}%不足{M8_GAP_MIN*100}%")

    # 8.3 收阳
    if not row['is_yang'] or row['change_pct'] < M8_CHANGE_MIN:
        failures.append(f"未收阳或涨幅{ row['change_pct']*100:.1f}%<{M8_CHANGE_MIN*100}%")

    # 8.4 缩量
    vr = row.get('volume_ratio', 1.0)
    if vr > M8_VOL_RATIO:
        failures.append(f"量比{ vr:.2f}>{M8_VOL_RATIO}")

    # 8.5 守 5 日线
    if not pd.isna(ma_values[0]) and row['close'] < ma_values[0]:
        failures.append(f"close={row['close']:.2f}<MA5={ma_values[0]:.2f}")

    passed = len(failures) == 0
    return passed, "; ".join(failures) if not passed else ""


def check_model_10(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 10：主升狙击
    突破12月新高 + 站上全线均线 + 放量
    """
    failures = []

    if hist is None or len(hist) < 260:
        return False, "历史数据不足250日"

    # 10.1 12 月新高
    high_12m = hist['high'].max()
    if row['close'] < high_12m:
        failures.append(f"close={row['close']:.2f}<12月高点={high_12m:.2f}")

    # 10.2 全线均线之上
    mas = compute_mas(hist, M10_MA_WINDOWS)
    for w in M10_MA_WINDOWS:
        ma_val = mas[w].iloc[-1]
        if pd.isna(ma_val):
            failures.append(f"MA{w}=NaN")
        elif row['close'] <= ma_val:
            failures.append(f"close={row['close']:.2f}≤MA{w}={ma_val:.2f}")

    # 10.3 放量 > 10日均量的 1.5 倍
    vol_10d_avg = hist['volume'].iloc[-11:-1].mean() if len(hist) >= 11 else hist['volume'].mean()
    if vol_10d_avg > 0 and row.get('volume', 0) < vol_10d_avg * M10_VOL_SURGE:
        failures.append(f"量{row.get('volume',0):.0f}<10日均量{vol_10d_avg*M10_VOL_SURGE:.0f}")

    passed = len(failures) == 0
    return passed, "; ".join(failures) if not passed else ""


# ─── 辅助函数：hist 衍生指标 ────────────────────────────

def compute_hist_derivations(hist: pd.DataFrame) -> pd.DataFrame:
    """在历史日线数据上计算衍生指标（涨停检测等）。"""
    if hist is None or len(hist) < 2:
        return hist
    hist = hist.copy()
    hist['change_pct'] = hist['close'].pct_change()
    hist['is_limit_up'] = hist['change_pct'] >= 0.097
    hist['amplitude'] = (hist['high'] - hist['low']) / hist['close']
    hist['is_yiziboard'] = hist['is_limit_up'] & (hist['amplitude'] < 0.005)
    hist['is_tradable_limit'] = hist['is_limit_up'] & ~hist['is_yiziboard']
    return hist


# ─── 模型 1：钱坤寻龙（STUB）───────────────────────────

def check_model_1(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 1：钱坤寻龙 — 当日龙虎榜标的 + 热点板块共振
    ⚠️  STUB：AKShare 不含龙虎榜/热点板块数据。
    建议：部署到 ECS 后通过 MCP 补齐。
    """
    return False, "STUB: 龙虎榜/热点数据不可用（AKShare 不含）"


# ─── 模型 3：回调狙击 ──────────────────────────────────

def check_model_3(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 3：回调狙击 — 前期快速拉升后缩量回踩企稳，低吸短线
    """
    if hist is None or len(hist) < 65:
        return False, "历史数据不足65日"
    hist = compute_hist_derivations(hist)
    mas = compute_mas(hist, [5])
    ma5 = mas[5]

    today_close = row['close']
    today_vol = row['volume']
    lower_s = row['lower_shadow']
    upper_s = row['upper_shadow']
    today_high = row['high']

    m3_surge_rise = 0.35
    m3_surge_up_days = 5
    m3_surge_limits = 2
    m3_pullback_max = 0.95
    m3_shadow_max = 0.05
    m3_vol_max_ratio = 1.5

    if not row.get('is_yang') and row['change_pct'] <= 0:
        return False, "非小阳线"
    if lower_s <= 0:
        return False, f"无下影线({lower_s*100:.2f}%)"
    if lower_s > m3_shadow_max:
        return False, f"下影线过长{lower_s*100:.1f}%>{m3_shadow_max*100}%"
    if upper_s >= lower_s:
        return False, f"上影线{upper_s*100:.2f}%≥下影线{lower_s*100:.2f}%"

    ma5_today = ma5.iloc[-1]
    if pd.isna(ma5_today):
        return False, "MA5=NaN"
    touches_ma5 = (today_close >= ma5_today * 0.99) or (today_high >= ma5_today)
    if not touches_ma5:
        return False, f"close={today_close:.2f}/high={today_high:.2f}未触及MA5={ma5_today:.2f}"

    if len(hist) >= 2:
        prev_vol = hist['volume'].iloc[-2]
        if prev_vol > 0 and today_vol > prev_vol * m3_vol_max_ratio:
            return False, f"放量{today_vol/prev_vol:.1f}x>{m3_vol_max_ratio}x"

    window = hist.iloc[-62:-1]
    if len(window) < 20:
        return False, "历史窗口不足20日"

    peak_idx = window['close'].idxmax()
    peak_close = window.loc[peak_idx, 'close']
    trough_idx = window.loc[:peak_idx, 'close'].idxmin()
    trough_close = window.loc[trough_idx, 'close']

    surge_bars = window.loc[trough_idx:peak_idx]
    total_rise = (peak_close - trough_close) / max(trough_close, 0.01)
    if total_rise < m3_surge_rise:
        return False, f"拉升幅度{total_rise*100:.0f}%<{m3_surge_rise*100}%"
    up_days = surge_bars[surge_bars['change_pct'] > 0].shape[0]
    limit_days = surge_bars[surge_bars['is_tradable_limit'] == True].shape[0]
    if up_days < m3_surge_up_days:
        return False, f"上涨日{up_days}<{m3_surge_up_days}"
    if limit_days < m3_surge_limits:
        return False, f"换手涨停{limit_days}<{m3_surge_limits}"

    if today_close >= peak_close * m3_pullback_max:
        return False, f"未回调 close={today_close:.2f}≥峰值{m3_pullback_max}×{peak_close:.2f}"
    return True, ""


# ─── 模型 4：向上缺口（scanner 版）─────────────────────

def check_model_4(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 4：向上缺口 — 顺势短线（scanner 原版逻辑）
    与模型 2 同名但条件略异：缺口阈值更宽松，前日阴线时要求缩量。
    """
    failures = []

    m4_gap_min = 0.001
    m4_intraday_min = 0.05
    m4_shadow_max = 0.02
    m4_change_max = 0.08

    gap = row['gap_up_pct']
    if gap <= m4_gap_min:
        failures.append(f"跳空{gap*100:.1f}%≤{m4_gap_min*100}%")
    if row['low'] <= row['pre_close']:
        failures.append(f"盘中回补缺口 low={row['low']:.2f}≤pre={row['pre_close']:.2f}")
    if row['intraday_pct'] < m4_intraday_min:
        failures.append(f"盘中涨幅{row['intraday_pct']*100:.1f}%<{m4_intraday_min*100}%")
    if row['lower_shadow'] > m4_shadow_max:
        failures.append(f"下影线{row['lower_shadow']*100:.1f}%>{m4_shadow_max*100}%")
    if row['change_pct'] >= m4_change_max:
        failures.append(f"涨幅{row['change_pct']*100:.1f}%≥{m4_change_max*100}%")

    if hist is not None and len(hist) >= 2:
        prev_row = hist.iloc[-2]
        prev_bearish = prev_row['close'] < prev_row['open']
        if prev_bearish and row['volume'] >= prev_row['volume']:
            failures.append("前日阴线但今日未缩量")

    passed = len(failures) == 0
    return passed, "; ".join(failures) if not passed else ""


# ─── 模型 5：中线狙击 ──────────────────────────────────

def check_model_5(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 5：中线狙击 — MA60 趋势向上，回踩后站上 MA5，低吸中线
    """
    if hist is None or len(hist) < 70:
        return False, "历史数据不足70日"
    mas = compute_mas(hist, [5, 60])
    ma5 = mas[5]
    ma60 = mas[60]

    m5_ma60_lookback = 30
    m5_vol_ratio = 1.5

    ma5_today = ma5.iloc[-1]
    ma5_prev = ma5.iloc[-2]
    ma60_today = ma60.iloc[-1]
    ma60_prev30 = ma60.iloc[-m5_ma60_lookback - 1] if len(ma60) > m5_ma60_lookback else None

    if pd.isna(ma5_today) or pd.isna(ma60_today) or ma60_prev30 is None or pd.isna(ma60_prev30):
        return False, "均线数据不足"
    if ma60_today <= ma60_prev30:
        return False, f"MA60未向上 ma60={ma60_today:.2f}≤{ma60_prev30:.2f}"
    if row['close'] <= ma60_today:
        return False, f"close={row['close']:.2f}≤MA60={ma60_today:.2f}"

    prev_close = hist['close'].iloc[-2]
    if pd.isna(ma5_prev):
        return False, "昨日MA5=NaN"
    if prev_close >= ma5_prev:
        return False, f"昨日收盘{prev_close:.2f}≥昨日MA5={ma5_prev:.2f}"
    if row['close'] < ma5_today:
        return False, f"今日收盘{row['close']:.2f}<MA5={ma5_today:.2f}"

    prev_vol = hist['volume'].iloc[-2]
    if prev_vol > 0 and row['volume'] < prev_vol * m5_vol_ratio:
        return False, f"量{row['volume']:.0f}<{m5_vol_ratio}x昨日{prev_vol:.0f}"

    return True, ""


# ─── 模型 6：波段雄鹰 ──────────────────────────────────

def check_model_6(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 6：波段雄鹰 — 多头排列中缩量回踩 MA10 后放量再启动
    """
    if hist is None or len(hist) < 25:
        return False, "历史数据不足25日"
    mas = compute_mas(hist, [5, 10, 20])

    m6_vol_shrink_max = 0.80
    m6_change_min = 0.003
    m6_change_max = 0.08
    m6_up_days_max = 2

    ma5 = mas[5].iloc[-1]
    ma10 = mas[10].iloc[-1]
    ma20 = mas[20].iloc[-1]
    if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
        return False, "均线数据不足"
    if not (ma5 > ma10 > ma20):
        return False, f"非多头 MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f}"

    vol_5d = hist['volume'].iloc[-6:-1].mean()
    vol_10d = hist['volume'].iloc[-11:-1].mean()
    if vol_5d > vol_10d * m6_vol_shrink_max:
        return False, f"5日均量{vol_5d:.0f}>{m6_vol_shrink_max}x10日均量{vol_10d:.0f}"

    if row['close'] < ma10:
        return False, f"close={row['close']:.2f}<MA10={ma10:.2f}"

    if len(hist) >= 2 and row['volume'] <= hist['volume'].iloc[-2]:
        return False, f"今日量{row['volume']:.0f}≤昨日{hist['volume'].iloc[-2]:.0f}"

    chg = row['change_pct']
    if not (m6_change_min <= chg <= m6_change_max):
        return False, f"涨幅{chg*100:.1f}%不在[{m6_change_min*100}%, {m6_change_max*100}%]"

    hist_d = compute_hist_derivations(hist)
    up_days = hist_d['change_pct'].iloc[-6:-1].gt(0).sum()
    if up_days > m6_up_days_max:
        return False, f"近5日上涨{up_days}天>{m6_up_days_max}"

    return True, ""


# ─── 模型 7：弱转强 ────────────────────────────────────

def check_model_7(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 7：弱转强 — 前期横盘弱势，今日量价共振突破 MA20
    """
    if hist is None or len(hist) < 25:
        return False, "历史数据不足25日"
    mas = compute_mas(hist, [5, 10, 20])

    m7_gain_20d_max = 0.05
    m7_change_min = 0.03
    m7_vol_ratio = 2.0

    ma5 = mas[5]
    ma10 = mas[10]
    ma20 = mas[20]
    ma20_today = ma20.iloc[-1]
    if pd.isna(ma20_today):
        return False, "MA20=NaN"

    close_20d_ago = hist['close'].iloc[-21] if len(hist) >= 21 else hist['close'].iloc[0]
    gain_20d = (row['close'] - close_20d_ago) / max(close_20d_ago, 0.01)
    if gain_20d >= m7_gain_20d_max:
        return False, f"20日涨幅{gain_20d*100:.1f}%≥{m7_gain_20d_max*100}%"
    if row['change_pct'] < m7_change_min:
        return False, f"涨幅{row['change_pct']*100:.1f}%<{m7_change_min*100}%"

    vol_5d = hist['volume'].iloc[-6:-1].mean()
    vol_ratio = row['volume'] / max(vol_5d, 1)
    if vol_ratio < m7_vol_ratio:
        return False, f"量比{vol_ratio:.1f}<{m7_vol_ratio}"

    if row['close'] <= ma20_today:
        return False, f"close={row['close']:.2f}≤MA20={ma20_today:.2f}"

    had_weak = any(
        ma5.iloc[i] is not None and ma10.iloc[i] is not None
        and not pd.isna(ma5.iloc[i]) and not pd.isna(ma10.iloc[i])
        and ma5.iloc[i] < ma10.iloc[i]
        for i in range(-11, -1)
    ) if len(ma5) >= 11 else False
    if not had_weak:
        return False, "近10日无弱势确认（MA5未低于MA10）"

    return True, ""


# ─── 模型 9：涨停回踩 ──────────────────────────────────

def check_model_9(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 9：涨停回踩 — 近15日换手涨停后回踩 MA5 企稳，二次启动
    """
    if hist is None or len(hist) < 20:
        return False, "历史数据不足20日"
    hist = compute_hist_derivations(hist)
    mas = compute_mas(hist, [5])
    ma5 = mas[5].iloc[-1]

    m9_limit_window = 15
    m9_pullback_max = 0.95
    m9_ma5_tolerance = 0.03
    m9_shadow_min = 0.01

    if pd.isna(ma5):
        return False, "MA5=NaN"

    window = hist.iloc[-(m9_limit_window + 1):-1]
    limit_bars = window[window['is_tradable_limit'] == True]
    if len(limit_bars) == 0:
        return False, f"近{m9_limit_window}日无换手涨停"

    limit_bar = limit_bars.iloc[-1]
    limit_close = limit_bar['close']

    if row['close'] > limit_close * m9_pullback_max:
        return False, f"close={row['close']:.2f}>{m9_pullback_max}x涨停收盘{limit_close:.2f}"
    if abs(row['close'] - ma5) / ma5 > m9_ma5_tolerance:
        return False, f"close={row['close']:.2f}偏离MA5={ma5:.2f}>{m9_ma5_tolerance*100}%"
    if row['lower_shadow'] < m9_shadow_min:
        return False, f"下影线{row['lower_shadow']*100:.1f}%<{m9_shadow_min*100}%"
    if row['change_pct'] < 0:
        return False, f"涨幅{row['change_pct']*100:.1f}%<0"

    return True, ""


# ─── 模型 11：均线共振 ──────────────────────────────────

def check_model_11(row: pd.Series, hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    模型 11：均线共振 — MA5/10/20 完整多头 + 三线密集 + 近期金叉
    """
    if hist is None or len(hist) < 25:
        return False, "历史数据不足25日"
    mas = compute_mas(hist, [5, 10, 20])

    m11_spread_max = 0.05
    m11_cross_window = 8
    m11_vol_ratio = 1.2

    ma5 = mas[5].iloc[-1]
    ma10 = mas[10].iloc[-1]
    ma20 = mas[20].iloc[-1]
    if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
        return False, "均线数据不足"

    if not (ma5 > ma10 > ma20):
        return False, f"非多头 MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f}"

    spread = (ma5 - ma20) / ma20
    if spread > m11_spread_max:
        return False, f"MA5-MA20差距{spread*100:.1f}%>{m11_spread_max*100}%"

    had_cross = False
    for i in range(-m11_cross_window - 1, -1):
        if i - 1 < -len(mas[5]):
            break
        v5_prev = mas[5].iloc[i - 1]
        v10_prev = mas[10].iloc[i - 1]
        v5_cur = mas[5].iloc[i]
        v10_cur = mas[10].iloc[i]
        if any(x is None or pd.isna(x) for x in [v5_prev, v10_prev, v5_cur, v10_cur]):
            continue
        if v5_prev <= v10_prev and v5_cur > v10_cur:
            had_cross = True
            break
    if not had_cross:
        return False, f"近{m11_cross_window}日无MA5金叉MA10"

    if row['close'] < ma5:
        return False, f"close={row['close']:.2f}<MA5={ma5:.2f}"

    vol_5d = hist['volume'].iloc[-6:-1].mean()
    vol_ratio = row['volume'] / max(vol_5d, 1)
    if vol_ratio < m11_vol_ratio:
        return False, f"量比{vol_ratio:.1f}<{m11_vol_ratio}"

    return True, ""


# ─── 主流程 ───────────────────────────────────────────────

MODEL_REGISTRY = {
    1:  {"name": "钱坤寻龙",  "fn": check_model_1,  "needs_hist": False, "hist_days": 0},    # STUB
    2:  {"name": "向上缺口",  "fn": check_model_2,  "needs_hist": True,  "hist_days": 10},
    3:  {"name": "回调狙击",  "fn": check_model_3,  "needs_hist": True,  "hist_days": 65},
    4:  {"name": "向上缺口2", "fn": check_model_4,  "needs_hist": True,  "hist_days": 10},
    5:  {"name": "中线狙击",  "fn": check_model_5,  "needs_hist": True,  "hist_days": 70},
    6:  {"name": "波段雄鹰",  "fn": check_model_6,  "needs_hist": True,  "hist_days": 25},
    7:  {"name": "弱转强",    "fn": check_model_7,  "needs_hist": True,  "hist_days": 25},
    8:  {"name": "低开接力",  "fn": check_model_8,  "needs_hist": True,  "hist_days": 30},
    9:  {"name": "涨停回踩",  "fn": check_model_9,  "needs_hist": True,  "hist_days": 20},
    10: {"name": "主升狙击",  "fn": check_model_10, "needs_hist": True,  "hist_days": 260},
    11: {"name": "均线共振",  "fn": check_model_11, "needs_hist": True,  "hist_days": 25},
}


def pre_filter_candidates(df: pd.DataFrame, models: List[int]) -> pd.DataFrame:
    """根据模型组合预筛选候选池，减少历史数据拉取。"""
    mask = pd.Series(False, index=df.index)

    if 2 in models:
        mask |= (df['gap_up_pct'] >= PRE_FILTER_GAP_UP)
    if 3 in models:
        # 回调狙击：小阳线（模型内再精确判断）
        mask |= ((df['change_pct'] > -0.02) & (df['change_pct'] < 0.05))
    if 4 in models:
        # 向上缺口2：跳空 > 0 即可（比模型2宽松）
        mask |= (df['gap_up_pct'] > 0.001)
    if 5 in models:
        mask |= (df['change_pct'] >= PRE_FILTER_MA_LONG)
    if 6 in models:
        # 波段雄鹰：微涨
        mask |= ((df['change_pct'] >= 0.003) & (df['change_pct'] < 0.08))
    if 7 in models:
        mask |= (df['change_pct'] >= PRE_FILTER_CHANGE)
    if 8 in models:
        mask |= (df['gap_up_pct'] <= PRE_FILTER_GAP_DOWN)
    if 9 in models:
        # 涨停回踩：有下影线（支撑有效）
        mask |= ((df['lower_shadow'] >= 0.01) & (df['change_pct'] > -0.01))
    if 10 in models:
        mask |= (df['change_pct'] >= PRE_FILTER_CHANGE)
    if 11 in models:
        # 均线共振：上涨即可（hist内再精判）
        mask |= (df['change_pct'] > 0)

    return df[mask]


def run(models: Optional[List[int]] = None):
    """主入口。"""
    if models is None:
        models = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]

    models = sorted(set(models) & set(MODEL_REGISTRY))
    if not models:
        print("错误：没有有效的模型编号", file=sys.stderr)
        return []

    model_names = [MODEL_REGISTRY[m]["name"] for m in models]
    print(f"⚡ 万军选股启动 — 模型: {', '.join(model_names)}", file=sys.stderr)

    # Phase 1: spot 数据
    df = fetch_spot_data()
    df = compute_spot_derivations(df)

    # Phase 2: 预筛选
    candidates = pre_filter_candidates(df, models)
    print(f"[预筛] {len(candidates)} / {len(df)} 只进入候选池", file=sys.stderr)

    if len(candidates) == 0:
        print("无候选标的", file=sys.stderr)
        return []

    # Phase 3: 拉历史数据（取最大所需窗口）
    max_days = max(MODEL_REGISTRY[m]["hist_days"] for m in models if MODEL_REGISTRY[m]["needs_hist"])
    symbols = candidates['symbol'].tolist()
    hist_data = fetch_hist_batch(symbols, days=max_days)

    # Phase 4: 逐模型匹配
    results = []
    for sym in symbols:
        row = candidates[candidates['symbol'] == sym].iloc[0]
        hist = hist_data.get(sym)  # None if fetch failed
        hit_models = []
        details = {}

        for m in models:
            fn = MODEL_REGISTRY[m]["fn"]
            passed, reason = fn(row, hist)
            if passed:
                hit_models.append(m)
            details[f"model_{m}"] = {"passed": passed, "reason": reason if reason else "OK"}

        if hit_models:
            results.append({
                "symbol": sym,
                "name": row.get('name', ''),
                "close": float(row['close']),
                "change_pct": float(row['change_pct']),
                "gap_up_pct": float(row['gap_up_pct']),
                "volume_ratio": float(row.get('volume_ratio', 0)),
                "hit_models": hit_models,
                "model_names": [MODEL_REGISTRY[m]["name"] for m in hit_models],
                "resonance": len(hit_models),
                "details": details,
            })

    # Phase 5: 输出
    results.sort(key=lambda r: (-r['resonance'], -r['change_pct']))
    return results


def print_table(results: List[dict]):
    """打印结果表格。"""
    if not results:
        print("\n## 万军选股结果 — 无匹配标的")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"\n## 万军选股结果 — {today_str}")
    print()

    # 共振信号
    resonance = [r for r in results if r['resonance'] >= 2]
    if resonance:
        print("### 🔥 共振信号（≥2 模型命中）")
        print(f"| 股票 | 代码 | 命中模型 | 共振度 | 当前价 | 涨跌幅 | 跳空 | 量比 |")
        print(f"|------|------|---------|--------|--------|--------|------|------|")
        for r in resonance:
            print(f"| {r['name']} | {r['symbol']} | {','.join(r['model_names'])} "
                  f"| {'🔥'*min(r['resonance'],3)} | {r['close']:.2f} "
                  f"| {r['change_pct']*100:+.1f}% "
                  f"| {r['gap_up_pct']*100:+.1f}% "
                  f"| {r['volume_ratio']:.2f} |")

    # 独立信号
    singles = [r for r in results if r['resonance'] == 1]
    if singles:
        print()
        print("### 独立信号")
        print(f"| 股票 | 代码 | 命中模型 | 当前价 | 涨跌幅 | 跳空 | 量比 |")
        print(f"|------|------|---------|--------|--------|------|------|")
        for r in singles:
            print(f"| {r['name']} | {r['symbol']} | {r['model_names'][0]} "
                  f"| {r['close']:.2f} "
                  f"| {r['change_pct']*100:+.1f}% "
                  f"| {r['gap_up_pct']*100:+.1f}% "
                  f"| {r['volume_ratio']:.2f} |")

    print(f"\n共 {len(results)} 只命中（共振 {len(resonance)} / 独立 {len(singles)}）")


def output_jsonl(results: List[dict], filepath: str = None):
    """输出 JSONL 格式。"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    for r in results:
        record = {
            "source": "wanjun_models",
            "date": today_str,
            "symbol": r['symbol'],
            "name": r['name'],
            "model_ids": r['hit_models'],
            "model_names": r['model_names'],
            "resonance": r['resonance'],
            "close": r['close'],
            "change_pct": r['change_pct'],
            "gap_up_pct": r['gap_up_pct'],
            "volume_ratio": r['volume_ratio'],
            "operation_mode": "积极操作",  # 需外部传入
            "temperature": 70,             # 需外部传入
            "gen_time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        }
        line = json.dumps(record, ensure_ascii=False)
        if filepath:
            with open(filepath, 'a') as f:
                f.write(line + '\n')
        else:
            print(line)


# ─── CLI ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="万军量化选股筛选脚本")
    parser.add_argument('--model', type=str, default='1,2,3,4,5,6,7,8,9,10,11',
                        help='模型编号，逗号分隔 (默认: 2,8,10)')
    parser.add_argument('--jsonl', action='store_true',
                        help='输出 JSONL 格式')
    parser.add_argument('--output', type=str, default=None,
                        help='JSONL 输出文件路径 (默认: stdout)')
    args = parser.parse_args()

    model_ids = [int(m.strip()) for m in args.model.split(',')]
    results = run(models=model_ids)

    if args.jsonl:
        output_jsonl(results, filepath=args.output)
    else:
        print_table(results)


if __name__ == '__main__':
    main()
