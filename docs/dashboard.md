# 本地看板

在电脑的浏览器里打开 **http://localhost:8080** 就能看到：

- 今天的总资产和盈亏，和标普 500、股债 60/40、规则基准的对比，当天每分钟的走势
- Claude 当天的计划（早上 8:07 写完就能看到，不用等 10:30）、目标配置和程序的执行情况
- 市场概况：标普 500、纳斯达克 100、道琼斯、罗素 2000、长期美债、黄金、原油、美元，以及趋势护栏
- 持仓、资产走势、对照策略排行、每日记录、交易记录、运行记录和系统状态

看板是**只读**的：不能下单，不改任何文件，只监听本机（家里 WiFi 上的其他设备访问不到）。它和交易程序分开运行，看板出问题不影响每天的交易。

金额只算程序管理的那 2000 美元，和 moomoo App 里整个模拟账户的数字不同，这是正常的。

## 数据从哪来

| 内容 | 来源 |
| --- | --- |
| 持仓、现金、交易记录、运行记录 | `logs/run-*.json`（程序每次运行写的记录） |
| 资产走势、对照策略 | `logs/strategies.csv`、`state.json` |
| 今日计划 | `signals/`；另外每 15 分钟 `git fetch` 一次，直接读 GitHub 上最新的 `signals/latest.json`（只读，不改电脑上的文件；程序运行前 5 分钟到运行后 10 分钟不做） |
| 实时价格、分钟走势、市场行情 | 本机 OpenD 的行情接口（不用交易接口）。交易时段每 15 秒更新，其他时间每 5 分钟 |

OpenD 没开时，页面显示最近一次程序运行时的数据，并在顶部说明；OpenD 打开后一分钟内自动恢复。

## 安装（新电脑恢复自动投资的线程照这里做）

在程序文件夹 `C:\Users\Mitchell\Documents\moomoo` 里：

1. 拉最新代码：`git pull`
2. 设置自动启动（普通权限就行）：

   ```
   powershell -ExecutionPolicy Bypass -File dashboard\install-autostart.ps1
   ```

   它会：建一个计划任务 `moomoo-dashboard`，登录 Windows 时用 `.venv\Scripts\pythonw.exe -m dashboard` 启动看板（没有黑窗口，意外退出会自动重启；建计划任务失败时改为在“启动”文件夹放快捷方式）；在桌面放一个“投资看板”快捷方式；最后打开浏览器。
3. 检查：浏览器里能看到页面，顶部显示“模拟盘”，右上角是市场状态。`logs\dashboard.log` 里有一行“看板已启动”。

不需要改 `config.yaml`。想改端口或涨跌颜色时，在 `config.yaml` 末尾加：

```yaml
dashboard:
  port: 8080        # 端口被占用时改这里
  up_color: green   # green = 绿涨红跌；red = 红涨绿跌
```

## 更新

不用做任何事。交易程序每次运行都会 `git pull`：页面部分刷新浏览器就是新的；看板服务发现自己的代码变了，30 秒内会自己重启。

## 手动启动、停止

- 手动启动：双击 `dashboard\start-dashboard.cmd`，或运行 `.venv\Scripts\pythonw -m dashboard`。已经在运行时不会再开第二个。
- 取消自动启动并关掉看板：

  ```
  powershell -ExecutionPolicy Bypass -File dashboard\install-autostart.ps1 -Uninstall
  ```

- 演示模式（不连 OpenD，行情是模拟的，只用来预览页面）：`.venv\Scripts\python -m dashboard.server --demo --port 8090`，然后打开 http://localhost:8090

## 出问题时

| 现象 | 处理 |
| --- | --- |
| 浏览器打不开 localhost:8080 | 看 `logs\dashboard.log`。“端口被占用”就在 `config.yaml` 里换一个端口（比如 8765），再运行一次安装脚本 |
| 顶部提示“没有连上 OpenD” | 打开并登录 OpenD；一分钟内自动恢复 |
| 今日计划显示的是前一天的 | Claude 8:07 还没写完，或者 GitHub 同步失败（页面最下方“系统状态”里能看到） |
| 提示“今天 10:30 应该运行一次，但还没有运行记录” | 交易程序没跑：电脑睡眠、没插电或计划任务出问题，在项目里跟 Claude 说一声 |

## 代码在哪

- `dashboard/server.py`：本地网页服务（Python 标准库，没有新依赖）
- `dashboard/data.py`：读文件、算盈亏和对比
- `dashboard/quotes.py`：OpenD 行情线程和演示数据
- `dashboard/gitsync.py`：定时读 GitHub 上的最新计划
- `dashboard/static/`：页面（原生 HTML/CSS/JavaScript，图表是手写的 SVG，不需要 Node，也不连外网）
- 测试：`tests/test_dashboard.py`
