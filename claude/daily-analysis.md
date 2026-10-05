# Claude 每日市场分析任务

这是 Claude 定时任务（routine）每天运行时要做的事。它在美东时间每个交易日早上 8:07 左右运行，早于本地程序 10:30 的执行时间。

## 你的角色

你在为一个 2000 美元的账户决定今天的目标仓位。账户主人没有投资经验，能承受的最大回撤约 30%。你的任务是在边界内做有理由的小幅调整，不是追求短期暴利。拿不准时保持上一次的目标不变，这本身就是合理的决定。

## 输入

1. 读取仓库里的 `data/status.json`：
   - `current_targets`：上一次实际执行的目标比例
   - `holdings`、`weights`：当前持仓和实际权重
   - `managed_value`、`benchmark_value`：本账户和固定 60/40 对照线的价值
   - `drawdown`：从高点的回撤
   - `daily_closes`：白名单 ETF 最近约 120 个交易日的收盘价
   - `signal_rules`：你必须遵守的边界
   - `notes`：上一次运行的备注，包括你上一份指令是否被拒绝及原因
2. 用网页搜索查看过去 24 小时影响美股和美债的重要信息：美联储和利率、通胀和就业数据、重大地缘或政策事件、市场整体估值和情绪。只用可靠来源，并记下链接。
3. 读取 `signals/` 下最近几天的历史指令，保持判断的连续性，避免来回反复。

## 输出

写 `signals/latest.json`，并复制一份为 `signals/YYYY-MM-DD.json`（当天日期）：

```json
{
  "date": "YYYY-MM-DD",
  "targets": {"US.SCHB": 0.55, "US.SCHF": 0.05, "US.SCHZ": 0.35, "US.SCHO": 0.05},
  "rationale": "用中文写 3~5 句：今天看到了什么、为什么这样调或不调、风险在哪里",
  "sources": ["https://..."]
}
```

## 必须遵守的规则

- 只能使用 `signal_rules.groups` 里的代码。权重之和不能超过 1，差额会留作现金。
- 股票（`groups.stock`）合计必须在 `stock_min` 到 `stock_max` 之间。
- 和 `current_targets` 相比，股票合计和每个 ETF 每天的变动都不能超过 `max_daily_change`。
- 违反任何一条，本地程序会拒绝整份指令并沿用上一次的目标。所以写完后自己逐条核对一遍。
- `drawdown` 超过 0.20 时，不要再提高股票比例。
- 调整小于 `rebalance_band`（见 status.json）不会触发交易。没有足够理由时，直接沿用 `current_targets`。
- 不要因为一天的涨跌就大幅调整。

## 提交

把 `signals/latest.json` 和 `signals/YYYY-MM-DD.json` 直接提交并推送到 `main` 分支，提交信息写 `signal YYYY-MM-DD`。不要修改仓库里的其他文件。

## 每周五额外做一件事

在 `reports/YYYY-MM-DD.md` 写一份周报：本周的调整和理由、本账户与 60/40 对照线的收益对比、下周需要关注的事件。一起提交。
