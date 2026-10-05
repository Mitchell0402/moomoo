# moomoo 自动再平衡

这个程序在 moomoo 里管理 2000 美元。默认按 60% 美股全市场 ETF（SCHB）+ 40% 美国债券 ETF（SCHZ）建仓，之后每个交易日检查一次。只要任一标的偏离目标超过 5 个百分点，就调回目标比例。打开“Claude 每日动态调整”后，目标比例改由 Claude 每天分析市场后决定（见下文）。

默认连**模拟盘**。计划是先模拟两周（到 2026 年 10 月 19 日左右），确认没问题后再按文末“切换到实盘”的步骤改用真钱。

## 它会做什么、不会做什么

- 只管理 `budget_usd` 这么多钱。模拟盘默认有大额虚拟资金，其余部分不会被动用。
- 只买卖 `targets` 里列出的代码，只下当日有效的限价单，不碰期权、融资和做空。
- 单笔不超过 1500 美元，每天合计不超过 2500 美元。
- 已有未完成订单、当天已经买过、不在美股常规交易时段时，都不下单。
- 先卖后买：卖单成交、钱回到账户后才下买单（最多等 90 秒，没成交就留到当天下一次运行）。
- **风险护栏**（写死在代码里，Claude 的指令和固定目标都要过这一关，见 `autoinvest/guards.py`）：
  - SCHB 价格低于约 10 个月（210 个交易日）均线时，股票合计最多 40%。
  - 从高点回撤超过 20% 时，不允许再加股票。
- 从高点回撤超过 25% 时在日志里提醒。
- 总开关：在本文件夹里放一个名为 `STOP` 的空文件，或把 `enabled` 改成 `false`，程序就不做任何操作。
- 远程急停：不在电脑前时，在项目里跟 Claude 说“停”，Claude 会往仓库写 `signals/HALT`，电脑下一次运行拉到它就不做任何操作；说“恢复”就删掉它。

## 第一次安装（大约 30 分钟）

1. **打开模拟交易**：在 moomoo App 里进入模拟交易，确认有美股模拟账户。
2. **安装并登录 OpenD**：从 [moomoo OpenAPI 下载页](https://www.moomoo.com/download/OpenAPI) 下载“可视化 OpenD”。安装后用 moomoo 账号登录，首次登录要输入手机收到的验证码，还要按提示完成 API 问卷和协议。之后让 OpenD 一直开着。
3. **安装 Python**：到 [python.org](https://www.python.org/downloads/) 装 3.10 或更新的版本。Windows 安装时勾选“Add Python to PATH”。
4. **安装本程序**：先装 [Git for Windows](https://git-scm.com/download/win)（安装时保持默认选项）。然后打开“命令提示符 cmd”，运行 `git clone https://github.com/Mitchell0402/moomoo.git`（第一次会弹出浏览器让你登录 GitHub），再运行 `cd moomoo` 进入文件夹，依次运行：

   | 步骤 | Windows（cmd） | macOS（终端） |
   | --- | --- | --- |
   | 建虚拟环境 | `python -m venv .venv` | `python3 -m venv .venv` |
   | 装依赖 | `.venv\Scripts\python -m pip install -r requirements.txt` | `.venv/bin/python -m pip install -r requirements.txt` |
   | 复制配置 | `copy config.example.yaml config.yaml` | `cp config.example.yaml config.yaml` |

   下面的命令里，`PY` 在 Windows 上代表 `.venv\Scripts\python`，在 macOS 上代表 `.venv/bin/python`。

5. **检查连接**：运行 `PY -m autoinvest status`。正常的话会显示模拟账户号（acc_id）、两只 ETF 的价格，以及“需要再平衡”（因为还没建仓）。
6. **演练**：运行 `PY -m autoinvest run`。它会列出计划下的单（planned_orders），但不会真的下单。
7. **在模拟盘下第一笔单**：美股开盘时间内（美东 9:30–16:00）运行 `PY -m autoinvest run --execute`，然后去 App 的模拟盘里看订单。

## 每天自动运行

推荐每个交易日美东时间 10:30、12:30、14:30 各运行一次。程序一天最多买一次，多跑不会重复下单，只是让电脑晚开机、OpenD 临时掉线或卖单没及时成交时，当天还有机会补上。下面的时间请换算成你电脑所在的时区。

**Windows**：在命令提示符（cmd）里运行下面这条。先把路径换成本文件夹的实际路径，再把 10:30 改成你的本地时间。`/RI 120 /DU 05:00` 表示从 10:30 起每 2 小时再跑一次，共 3 次；`/F` 会覆盖同名的旧任务。路径要写完整，不要用 `%CD%`，在 PowerShell 里它不会被替换，任务会找不到程序：

```
schtasks /Create /F /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:30 /RI 120 /DU 05:00 /TN "moomoo-autoinvest" /TR "cmd /c cd /d C:\Users\你的用户名\Documents\moomoo && .venv\Scripts\python -m autoinvest run --execute"
```

再在 PowerShell 里运行下面这条，让电脑错过时间（比如在睡眠）后醒来马上补跑：

```
$s = (Get-ScheduledTask -TaskName moomoo-autoinvest).Settings; $s.StartWhenAvailable = $true; Set-ScheduledTask -TaskName moomoo-autoinvest -Settings $s
```

**macOS**：运行 `crontab -e`，加入一行（把路径换成本文件夹的实际路径）：

```
30 10,12,14 * * 1-5 cd /Users/你的用户名/moomoo-autoinvest && .venv/bin/python -m autoinvest run --execute
```

## 看结果

- `logs/summary.csv`：每次运行一行，包括管理的总金额、回撤、最大偏离、下单数和备注，可以用 Excel 打开。
- `logs/run-*.json`：每次运行的完整记录，包括价格、持仓和每笔订单。
- 有问题时把最近几个日志文件发给 Claude 就可以排查。

## Claude 每日动态调整

打开后，目标比例不再固定，而是由 Claude 每天分析市场后写在 `signals/latest.json` 里。每天的流程：

1. 美东 8:07 左右，Claude 的定时任务读取仓库里的 `data/status.json`（持仓、价格走势）和当天新闻，写出今天的目标比例和理由，提交到 GitHub。
2. 美东 10:30，本程序先 `git pull` 拿到指令，校验通过后按新目标调仓，再把最新的 `data/status.json` 和 `logs/summary.csv` 推回 GitHub。

Claude 的权限由 `config.yaml` 的 `signal` 部分限制：只能用白名单里的 4 只 ETF，股票合计在 40%–75% 之间，每天最多变动 10 个百分点。指令不合规、过期或缺失时，程序沿用上一次的目标，并在日志里写明原因。通过校验的目标还要再过一遍上面的风险护栏，护栏会直接把股票比例压下来，不受每天 10 个百分点的限制。日志里的 `benchmark_value` 是同样 2000 美元按固定 60/40 运行的对照线，用来判断 Claude 的调整有没有帮上忙。Claude 的分析规则写在 `claude/daily-analysis.md`。

打开方法：把 `config.yaml` 里 `signal` 下的 `enabled` 改成 `true`，然后告诉 Claude，Claude 会设好每天的分析任务。程序要在用 `git clone` 下载的这个文件夹里运行，才能和 GitHub 同步。

## 策略对照

同一个模拟盘里，真实下单的只有一套目标（Claude 的动态调整，或没打开时的固定 60/40）。另外 9 个常见策略各有一个虚拟的 2000 美元账户，每天按当天价格记账、需要时虚拟调仓，不下单，只用来比较：

| 名字 | 规则 |
| --- | --- |
| fixed_100 | 100% 股票，不调整 |
| fixed_80_20、fixed_60_40、fixed_50_50 | 固定股债比例，偏离 5 个百分点再调回 |
| trend_100 | 股价在约 10 个月均线上方全仓股票，下方全仓债券 |
| trend_80_30 | 均线上方 80% 股票，下方 30% |
| dual_momentum | 过去 12 个月股票跑赢债券就全仓股票，否则全仓债券 |
| vol_target | 按股票近 6 个月的波动，把整体波动控制在约 10% |
| risk_parity | 按股票、债券各自波动的倒数分配 |

每次执行后，`logs/strategies.csv` 记一行：`actual` 是实际账户价值，后面每列是一个策略的虚拟账户价值。对照账户在第一次执行那天建立，从同一天开始比。用的是 SCHB 和 SCHZ 两只 ETF 的价格，没算分红，所以绝对数字略低于真实收益，但各策略之间可以直接比。

`backtest/compare_results.txt` 是这些规则在 1954 年以来月度数据上的回测（`python -m backtest.compare` 重新生成）。回测用的是每月平均价，会让趋势类策略看起来比实际好，所以已经按“信号晚一个月执行”做了保守处理。Claude 的动态调整没法回测，因为 Claude 已经知道历史行情，只能从现在开始往前跑着比。

## 测试

`PY -m pytest`：用一个假的券商把整个流程跑一遍，不需要 OpenD，也不会连接 moomoo。

## 切换到实盘（模拟两周没问题之后）

切换前先确认这三件事：模拟盘里两只 ETF 都买到了；`logs/summary.csv` 里每天都有记录且没有“错误”；App 里模拟盘的持仓和日志里的 holdings 一致。

实盘默认按账户真实的持仓和现金来管理（`ledger: auto`），这样 ETF 的分红到账后会自动再投资。所以**实盘账户里只放给这个程序的钱**；如果账户里还有别的现金要留着不动，把金额填在 `cash_reserve_usd`。

1. 往 moomoo 实盘账户入金 2000 美元，等资金到账。
2. 在 OpenD 里确认实盘交易已解锁（界面上方会显示解锁状态和到期时间，到期前点“延长授权”）。
3. 打开 `config.yaml`，改两处，**两处都改了才会用真钱**：
   - `trd_env: REAL`
   - `real_money_confirmed: true`
4. 同时把 `start_date` 改成切换当天的日期。
5. 先运行 `PY -m autoinvest run` 演练，确认 acc_id 是你的实盘账户、计划买入的股数合理。
6. 开盘时间内运行一次 `PY -m autoinvest run --execute`，去 App 里确认订单。之后定时任务会照常每天运行。

想退回模拟盘，把 `trd_env` 改回 `SIMULATE` 即可。想马上停止一切操作，在文件夹里放一个名为 `STOP` 的空文件。
