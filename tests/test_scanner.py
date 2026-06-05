"""
tests/test_scanner.py — scanner 模块基础单元测试

重点覆盖：
  - P0 bug 区域：htji 影线逻辑（下影线 > 上影线）
  - 工具函数：_is_st, _is_yiziboard, _vol_ratio, _ma
  - 边界条件：空数据、不足数据、极端值

兼容 pytest 和 unittest。
"""
import sys
import unittest
from pathlib import Path

# 确保 modules 可导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.scanner import (
    _is_st,
    _is_yiziboard,
    _vol_ratio,
    _ma,
    _upper_shadow,
    _lower_shadow,
    _model_htji,
)


def _bar(open_, high, low, close, chg, volume=1000000):
    """工厂：创建一根 K 线 dict。"""
    return {"open": open_, "high": high, "low": low, "close": close, "chg": chg, "volume": volume}


# ── _is_st ────────────────────────────────────────────────

class TestIsST(unittest.TestCase):

    def test_st_prefix(self):
        self.assertTrue(_is_st("ST平安"))

    def test_star_st_prefix(self):
        self.assertTrue(_is_st("*ST信威"))

    def test_normal_stock(self):
        self.assertFalse(_is_st("平安银行"))

    def test_normal_stock_premium(self):
        self.assertFalse(_is_st("贵州茅台"))

    def test_boundary_pure_st(self):
        self.assertTrue(_is_st("ST"))

    def test_boundary_pure_star_st(self):
        self.assertTrue(_is_st("*ST"))

    def test_contains_st_but_not_prefix(self):
        self.assertFalse(_is_st("测试ST股"))


# ── _is_yiziboard ─────────────────────────────────────────

class TestIsYiziboard(unittest.TestCase):

    def test_true_oneword_limit(self):
        """一字涨停：涨幅 ≥ 9.7% 且振幅 < 0.5%。"""
        b = _bar(10.00, 10.03, 10.00, 10.03, 0.099)
        self.assertTrue(_is_yiziboard(b))

    def test_not_limit_up(self):
        """不是涨停，不判为一字板。"""
        b = _bar(10.00, 10.02, 9.98, 10.02, 0.02)
        self.assertFalse(_is_yiziboard(b))

    def test_wide_range_not_yiziboard(self):
        """涨停但振幅大（7%）→ 不是一字板。"""
        b = _bar(10.00, 10.70, 9.95, 10.70, 0.099)
        self.assertFalse(_is_yiziboard(b))

    def test_boundary_05percent(self):
        """振幅恰好 ~0.5%（实际 0.4975%）→ <0.005 为 True。"""
        b = _bar(10.00, 10.05, 10.00, 10.05, 0.099)
        self.assertTrue(_is_yiziboard(b))


# ── _vol_ratio ────────────────────────────────────────────

class TestVolRatio(unittest.TestCase):

    def test_normal(self):
        """标准量比：今日 / 5日均量 = 2.0。"""
        bars = [_bar(10, 10, 10, 10, 0) for _ in range(7)]
        bars[-1]["volume"] = 20000
        for i in range(1, 6):
            bars[-(i + 1)]["volume"] = 10000
        self.assertAlmostEqual(_vol_ratio(bars, 5), 2.0)

    def test_insufficient_bars(self):
        """K 线不足 n+1 条 → 返回 0.0。"""
        bars = [_bar(10, 10, 10, 10, 0) for _ in range(3)]
        self.assertEqual(_vol_ratio(bars, 5), 0.0)

    def test_zero_avg_volume(self):
        """均量为 0 → max(avg_vol, 1) 保护不除零。"""
        bars = [_bar(10, 10, 10, 10, 0) for _ in range(7)]
        bars[-1]["volume"] = 500
        for i in range(1, 6):
            bars[-(i + 1)]["volume"] = 0
        self.assertEqual(_vol_ratio(bars, 5), 500.0)


# ── _ma ───────────────────────────────────────────────────

class TestMA(unittest.TestCase):

    def test_basic(self):
        vals = [1, 2, 3, 4, 5, 6]
        result = _ma(vals, 3)
        self.assertEqual(result, [None, None, 2.0, 3.0, 4.0, 5.0])

    def test_insufficient(self):
        vals = [1, 2]
        result = _ma(vals, 5)
        self.assertEqual(result, [None, None])

    def test_all_none_when_empty(self):
        self.assertEqual(_ma([], 3), [])


# ── _upper_shadow / _lower_shadow ─────────────────────────

class TestShadows(unittest.TestCase):

    def test_upper_shadow(self):
        b = _bar(10.0, 10.5, 9.9, 10.2, 0.02)
        self.assertAlmostEqual(_upper_shadow(b), (10.5 - 10.2) / 10.2, places=4)

    def test_lower_shadow(self):
        b = _bar(10.2, 10.3, 9.8, 10.0, -0.02)
        self.assertAlmostEqual(_lower_shadow(b), (10.0 - 9.8) / 10.0, places=4)

    def test_lower_shadow_zero(self):
        b = _bar(10.0, 10.5, 10.0, 10.2, 0.02)
        self.assertEqual(_lower_shadow(b), 0.0)

    def test_upper_shadow_zero(self):
        b = _bar(10.0, 10.0, 9.8, 10.0, 0.0)
        self.assertEqual(_upper_shadow(b), 0.0)


# ── _model_htji — P0 影线逻辑 ─────────────────────────────

class TestHtjiShadowLogic(unittest.TestCase):
    """P0 bug 回归测试：下影线必须 > 0 且 > 上影线 且 ≤ 5%。"""

    @staticmethod
    def _build_surge_kline(normal_days=20, surge_days=10, pullback_days=5):
        """构建带上涨浪+回调的标准 K 线序列。返回 kline 和 peak_close。
        
        回调设计为"急跌→企稳"，让 MA5 跟得上企稳日收盘价。
        """
        kline = []
        # 1. 横盘安静期
        for i in range(normal_days):
            kline.append(_bar(50 + i * 0.1, 50 + i * 0.15, 50 + i * 0.05, 50 + i * 0.1, 0.002, 50000))
        base = kline[-1]["close"]
        # 2. 上涨浪：~60% 涨幅，含 3 个涨停
        for i in range(surge_days):
            chg = 0.099 if i in (2, 5, 8) else 0.035
            c = base * (1 + chg)
            kline.append(_bar(base, c * 1.02, base * 0.99, c, chg, 80000 if i < 3 else 60000))
            base = c
        peak_close = kline[-1]["close"]
        # 3. 急跌后企稳：第 1 日 -10%，后 4 日横在低位
        pullback_close = peak_close * 0.90
        for i in range(pullback_days):
            chg = -0.10 if i == 0 else 0.0
            c = pullback_close if i == 0 else pullback_close + (i - 1) * 0.05
            kline.append(_bar(c * 1.01 if i == 0 else c * 0.995, c * 1.02, c * 0.98, c, chg, 50000))
        return kline, peak_close

    def test_passes_with_valid_lower_shadow(self):
        """下影线 ~2%，>0 且 >上影线 且 ≤5% → 应命中。"""
        kline, peak = self._build_surge_kline()
        # 企稳日在回调平台附近（略高于底部），下影线试探后被买回
        today_close = peak * 0.91
        today_open  = today_close * 0.997
        today_high  = today_close * 1.003  # 极小上影
        today_low   = today_close * 0.98   # ~2% 下影线，>0 且 >上影且 ≤5%
        kline.append(_bar(today_open, today_high, today_low, today_close, 0.005, 45000))
        kline[-2]["volume"] = 50000  # 前日量 ≥ 今日量（不超 1.5 倍）

        result = _model_htji("000001", "测试银行", kline)
        self.assertIsNotNone(result, "下影线 > 上影线 + 站 MA5 + 回调充分 → 应命中")
        self.assertEqual(result["code"], "000001")

    def test_rejects_when_lower_shadow_is_zero(self):
        """下影线 = 0 → 应拒绝。"""
        kline, peak = self._build_surge_kline()
        today_close = peak * 0.91
        # low == min(open, close) → 下影线 = 0
        kline.append(_bar(today_close * 0.997, today_close * 1.003,
                          today_close * 0.997, today_close, 0.005, 45000))
        kline[-2]["volume"] = 50000

        result = _model_htji("000002", "测试科技", kline)
        self.assertIsNone(result)

    def test_rejects_when_upper_shadow_gte_lower_shadow(self):
        """上影线 ≥ 下影线（冲高回落）→ 应拒绝。"""
        kline, peak = self._build_surge_kline()
        today_close = peak * 0.91
        today_high  = today_close * 1.04   # ~4% 长上影
        today_low   = today_close * 0.997  # 几乎无下影
        kline.append(_bar(today_close * 0.997, today_high, today_low,
                          today_close, 0.005, 45000))
        kline[-2]["volume"] = 50000

        result = _model_htji("000003", "测试医药", kline)
        self.assertIsNone(result)

    def test_rejects_when_lower_shadow_too_long(self):
        """下影线 > 5%（恐慌下探）→ 应拒绝。"""
        kline, peak = self._build_surge_kline()
        today_close = peak * 0.91
        kline.append(_bar(today_close * 0.997, today_close * 1.003,
                          today_close * 0.93,   # ~7% 下影线
                          today_close, 0.005, 45000))
        kline[-2]["volume"] = 50000

        result = _model_htji("000004", "测试地产", kline)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
