/**
 * 好运哥职业超级短线交易系统 — 策略量化模块
 *
 * 来源：好运哥职业超级短线交易系统.docx（2026-06-15 激活）
 * 核心：市场 regime → 仓位/姿态映射 + 资金管理纪律
 *
 * 这不是量化信号生成器，是交易纪律框架——
 * 根据市场 regime 输出当日应遵守的操作纪律。
 */

/**
 * @param {string} marketRegime — 市场 regime（强势做多|进攻|均衡偏多|均衡|防御|强势避险）
 * @param {number} longShortRatio — 多空比 (0.0-∞)
 * @param {number|null} monthlyIndexChange — 月指数涨跌幅 %（可选，null 则跳过月线判断）
 * @returns {{ posture: string, maxPosition: string, monthlyTarget: string, weeklyTarget: string, rules: string[], grade: number }}
 */
function calculatePosture(marketRegime, longShortRatio, monthlyIndexChange) {
  const ratio = longShortRatio || 1.0;
  const regime = marketRegime || '均衡';

  // ——— 月线判断（最高优先级） ———
  if (monthlyIndexChange != null) {
    if (monthlyIndexChange > 5) {
      return {
        posture: '积极进攻',
        maxPosition: '满仓（单品种或分仓≤2只）',
        monthlyTarget: '+30%',
        weeklyTarget: '+10%（超越大盘1倍）',
        rules: [
          '大盘月线强势，积极操作，追求利润最大化',
          '板块强势只做龙一龙二，跟风品种预期收益<10%不做',
          '强势区通常持续1-2周，回调缩量低点是好买点'
        ],
        grade: 2
      };
    }
    if (monthlyIndexChange < -5) {
      return {
        posture: '强制空仓',
        maxPosition: '空仓',
        monthlyTarget: '0%（不亏损为底线）',
        weeklyTarget: '空仓休息',
        rules: [
          '大盘月线熊市主跌段，停止操作，空仓休息',
          '若有好的操作机会，最高半仓，盈利目标+10%',
          '休息也是一种战斗，手持现金务求一击必中'
        ],
        grade: -2
      };
    }
  }

  // ——— 日线 regime 映射 ———
  const REGIME_MAP = {
    '强势做多': {
      posture: '积极进攻',
      maxPosition: '满仓（单品种）',
      monthlyTarget: '+30%',
      weeklyTarget: '+10%',
      grade: 2,
      rules: [
        '强势区通常持续1-2周，回调缩量低点是好买点',
        '板块强势只做龙一龙二',
        '资金日线追求阳多阴少、大阳小阴组合'
      ]
    },
    '进攻': {
      posture: '进攻',
      maxPosition: '满仓（分仓≤2只）',
      monthlyTarget: '+30%',
      weeklyTarget: '+10%',
      grade: 1,
      rules: [
        '板块强势只做龙一龙二，跟风品种不做',
        '预期收益<10%的品种不做',
        '买入强势品种后连续操作期1-2周，每日内可做差价但不让利润断层'
      ]
    },
    '均衡偏多': {
      posture: '均衡偏进攻',
      maxPosition: '7-8成仓',
      monthlyTarget: '+20%',
      weeklyTarget: '+5~10%',
      grade: 0.5,
      rules: [
        '谨慎追高——熊市涨幅+3%以上股票坚决不追涨',
        '要买也要等涨势确定后打涨停板参与',
        '市值徘徊不前→第一反应降低仓位'
      ]
    },
    '均衡': {
      posture: '均衡',
      maxPosition: '半仓',
      monthlyTarget: '+20%',
      weeklyTarget: '盈利即可',
      grade: 0,
      rules: [
        '市值徘徊→第一反应降仓，忌满仓急躁操作',
        '行情处于周目标困难期，应收缩战线',
        '忌满仓追求不切实际的目标'
      ]
    },
    '防御': {
      posture: '防御',
      maxPosition: '≤半仓',
      monthlyTarget: '+10%',
      weeklyTarget: '保本优先',
      grade: -0.5,
      rules: [
        '行情不利时严格执行空仓纪律',
        '最大仓位不能超过50%',
        '资金周阴线-8%或二周连阴→无条件退出清仓'
      ]
    },
    '强势避险': {
      posture: '强制空仓',
      maxPosition: '空仓',
      monthlyTarget: '0%（不亏损为底线）',
      weeklyTarget: '空仓休息',
      grade: -1,
      rules: [
        '休息也是一种战斗',
        '手持现金，不轻易下单，一旦下单务求一击必中',
        '空仓才能转换思维，迅速跟上最新热点'
      ]
    }
  };

  const entry = REGIME_MAP[regime] || REGIME_MAP['均衡'];
  const result = {
    posture: entry.posture,
    maxPosition: entry.maxPosition,
    monthlyTarget: entry.monthlyTarget,
    weeklyTarget: entry.weeklyTarget,
    grade: entry.grade,
    rules: [...entry.rules]
  };

  // ——— 多空比微调 ———
  if (ratio < 0.4 && result.grade >= 0) {
    result.rules.push('⚠️ 多空比极度偏空（' + ratio.toFixed(1) + '），建议收缩仓位');
  }
  if (ratio >= 2.5 && result.grade >= 0) {
    result.rules.push('🔥 多空比强势（' + ratio.toFixed(1) + '），当前仓位可适当激进');
  }

  // ——— 日连阴纪律（通用规则） ———
  result.consecutiveRules = [
    '账户2连阴需高度警惕，检查行情/心态/节奏',
    '账户3连阴必须无条件退出交易',
    '每日收市后检查市值日K线，对比周收益率与大盘涨幅'
  ];

  return result;
}

module.exports = { calculatePosture };
