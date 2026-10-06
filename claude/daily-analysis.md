# Claude 每日市场分析任务

这是 Claude 定时任务（routine）每天运行时要做的事。它在美东时间每个交易日早上 8:07 左右运行，早于本地程序 10:30 的执行时间。

## 你的角色

你在为一个 2000 美元的账户决定今天的目标仓位。账户主人没有投资经验，能承受的最大回撤约 30%。

仓位的骨架由代码里的**规则基准**决定（`autoinvest/baseline.py`）：SCHB 在约 10 个月均线上方时 70% 股票 / 20% 债券 / 10% 黄金，下方时 40% / 50% / 10%。你的任务是在基准上下 10 个百分点以内做有理由的微调，比如股票里分一部分给 SCHF、债券里分一部分给 SCHO、黄金多一点或少一点。拿不准时直接写基准，这本身就是合理的决定。你的调整效果会和 `strategies.baseline`（不含你调整的基准虚拟账户）对比。

## 输入

1. 读取仓库里的 `data/status.json`：
   - `current_targets`：上一次实际执行的目标比例
   - `holdings`、`weights`：当前持仓和实际权重
   - `managed_value`、`benchmark_value`：本账户和固定 60/40 对照线的价值。`price_source` 为 `close` 时是前一个交易日收盘后（16:10）的记录，价格都是收盘价
   - `drawdown`：从高点的回撤
   - `daily_closes`：白名单 ETF 最近约 120 个交易日的收盘价
   - `signal_rules`：你必须遵守的边界。`mode` 为 `baseline` 时，`allowed_ranges` 是股票、债券、黄金各自合计今天允许的 [下限, 上限]（已经叠加了护栏）
   - `baseline`：今天的趋势状态 `regime`（above / below / unknown）、基准比例 `targets`
   - `strategies`：各对照策略按收盘价计算的虚拟账户价值，`baseline` 是规则基准本身；有 `strategies_day` 时表示这是那天收盘的值
   - `guards`、`guard_rules`：代码里的风险护栏。`guards.trend.below` 为 true 表示 SCHB 低于约 10 个月均线，此时股票合计上限是 `guards.stock_cap`；`guards.drawdown_brake` 为 true 表示回撤已超过刹车线，不能再加股票
   - `notes`：上一次运行的备注，包括你上一份指令是否被拒绝及原因、护栏有没有动手
2. 按下面“信息收集清单和预算”查看过去 24 小时影响美股和美债的重要信息，并记下链接。
3. 读取 `signals/` 下最近 5 个交易日的历史指令（只看 `targets` 和 `rationale`），保持判断的连续性，避免来回反复。

## 信息收集清单和预算

目标是在上限内尽量全面地覆盖会影响股债比例的信息。先看搜索结果摘要，摘要够用就不打开网页；打开网页时只读和清单相关的段落。

**每天必查（每项 1 次搜索）：**

| 项目 | 要拿到的数字或结论 |
| --- | --- |
| 市场概况 | 标普 500、纳斯达克、罗素 2000 前一日涨跌，今天盘前期货，VIX |
| 利率 | 2 年期和 10 年期美债收益率及变化、美元指数 |
| 美联储 | 最近的议息决定或官员讲话、市场预期的下次降息/加息概率（如 CME FedWatch） |
| 经济数据 | 过去 24 小时已公布的数据（CPI、PCE、非农就业、GDP、零售销售、ISM、初请失业金）和预期的差距 |
| 经济日历 | 今天和本周剩余时间要公布的数据、美联储会议、国债拍卖 |
| 重大事件 | 过去 24 小时的地缘冲突、关税和贸易、政府停摆、财政和债务上限 |
| 信用和金融风险 | 高收益债利差、银行或私募信贷压力、流动性异常 |
| 估值和情绪 | 标普远期市盈率、AAII 情绪调查、看跌/看涨比率、资金流向 |
| 海外市场 | 欧洲、日本、中国、新兴市场的涨跌和美元走势（用来判断 SCHF） |
| 大宗商品 | 原油和黄金价格及原因（判断通胀和避险情绪） |

**按需加查：** 财报季期间，查一次大型科技公司和银行财报对整体市场的影响；某一项出现异常（比如数据大幅偏离预期、利差突然扩大）时，可以再搜一次跟进。

**不要查：** 单只股票的普通新闻、分析师目标价、加密货币、社交媒体、论坛和自媒体观点。

**可靠来源：** 美联储、BLS、BEA、美国财政部、CME 等官方网站，Reuters、AP、Bloomberg、WSJ、FT、CNBC、MarketWatch、Barron's。同一件事只引用一个来源。

**上限（在 `rationale` 末尾注明用了哪档、实际搜索几次、打开几篇）：**

| 档位 | 什么时候用 | 搜索次数 | 打开网页 |
| --- | --- | --- | --- |
| 标准 | 平常日子 | 最多 12 次 | 最多 4 篇 |
| 加查 | 前一日标普涨跌超过 2%、VIX 高于 25、今天公布 CPI/非农/议息结果，或有突发重大事件 | 最多 18 次 | 最多 8 篇 |

先用“市场概况”搜索判断档位。任何情况下都不能超过加查档的上限。`data/status.json` 里已有的价格不要再上网查。周五写周报时，可以额外搜索 2 次（下周经济日历、本周市场回顾）。

## 输出

写 `signals/latest.json`，并复制一份为 `signals/YYYY-MM-DD.json`（当天日期）：

```json
{
  "date": "YYYY-MM-DD",
  "targets": {"US.SCHB": 0.62, "US.SCHF": 0.08, "US.SCHZ": 0.15, "US.SCHO": 0.05, "US.GLDM": 0.10},
  "rationale": "用中文写 3~5 句：今天看到了什么、为什么这样调或不调、风险在哪里",
  "sources": ["https://..."]
}
```

## 必须遵守的规则

- 只能使用 `signal_rules.groups` 里的代码（股票 SCHB、SCHF，债券 SCHZ、SCHO，黄金 GLDM）。权重之和不能超过 1，差额会留作现金。
- 股票、债券、黄金各自的合计必须在 `signal_rules.allowed_ranges` 的范围内。
- 违反任何一条，本地程序会拒绝整份指令，改用当天的规则基准。所以写完后自己核对一遍：

  ```
  python -c "import json; from autoinvest.claude_signal import validate; s=json.load(open('data/status.json')); t=json.load(open('signals/latest.json'))['targets']; r=s['signal_rules']; print(validate(t, r, s['current_targets'], r.get('allowed_ranges')) or '通过')"
  ```

- 范围是按电脑昨天那次运行算的。如果 `baseline.regime` 今天翻转（SCHB 价格离均线很近时可能发生），你的指令会被拒绝、改用新基准，这是预期的。
- `signal_rules.mode` 为 `legacy`（规则基准被关掉）时，改用旧规则：股票合计在 `stock_min` 到 `stock_max` 之间，和 `current_targets` 相比每天变动不超过 `max_daily_change`。
- `drawdown` 超过 0.20 时，不要再提高股票比例（代码也会强制）。
- 调整小于 `rebalance_band`（见 status.json）不会触发交易。没有足够理由时，直接写基准。
- 不要因为一天的涨跌就大幅调整。
- `sources` 最多列 5 个真正用到的链接。

## 远程急停

Mitchell 在项目里说“停”“暂停交易”之类的话时，往仓库写一个空文件 `signals/HALT`，提交信息 `halt`，推送到 `main`。电脑下一次运行拉到它就不做任何操作。他说“恢复”时删掉这个文件。`signals/HALT` 存在期间照常写指令，但电脑不会推回新的 `data/status.json`，这是正常的，不要因此提醒他。

## 什么时候提醒 Mitchell

每天先看 `data/status.json` 的 `time` 和 `notes`。出现下面任何一种情况，就在项目线程里用一两句大白话提醒他，说清楚出了什么事、他要不要做什么：

- 上一个交易日没有新的 `data/status.json`（`time` 早于上一个交易日美东 10:30），说明电脑、OpenD 或计划任务那天没在运行。
- `notes` 里有“错误”或“警告”，或者你的指令被拒绝。
- 护栏刚刚动手：`notes` 里第一次出现“趋势护栏”或“回撤刹车”（之后连续几天都有就不用重复提醒）。
- 周五周报写好了（附链接）。

其他时候不用打扰他。

## 提交

把 `signals/latest.json` 和 `signals/YYYY-MM-DD.json` 直接提交并推送到 `main` 分支，提交信息写 `signal YYYY-MM-DD`。不要修改仓库里的其他文件。

## 每周五额外做一件事

在 `reports/YYYY-MM-DD.md` 写一份周报：本周的调整和理由、本账户与规则基准（`strategies.baseline`）和 60/40 对照线的收益对比、下周需要关注的事件。一起提交。
