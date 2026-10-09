# 重装电脑后恢复

GitHub 上的这个仓库就是完整备份：代码、Claude 每天的指令（`signals/`）、每天的数据和日志（`data/`、`logs/`），以及电脑上原本不进仓库的文件（`backup/`）。`backup/` 每次自动运行都会更新，重装前也可以手动跑一次 `.venv\Scripts\python -m autoinvest backup` 再推一份最新的。

Claude 这边的项目记忆和分析定时任务都在云端，重装电脑不影响。

## 需要你自己重新输入的

- moomoo 账号密码和手机验证码（新电脑登录 OpenD 时会要求）
- GitHub 登录（第一次 `git clone` 时会弹出浏览器）
- `backup/config.yaml` 里标着“备份时已清空”的字段（现在的配置里没有这类字段）

## 步骤

1. **装 Python 和 Git**：[Python 3.10 或更新](https://www.python.org/downloads/)，安装时勾选“Add Python to PATH”；[Git for Windows](https://git-scm.com/download/win)，保持默认选项。
2. **下载程序**：打开“命令提示符 cmd”，运行：

   ```
   cd /d C:\Users\Mitchell\Documents
   git clone https://github.com/Mitchell0402/moomoo.git
   cd moomoo
   git config user.name "Mitchell"
   git config user.email "107948640+Mitchell0402@users.noreply.github.com"
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

3. **拷回配置和状态**：

   ```
   copy backup\config.yaml config.yaml
   copy backup\state.json state.json
   if exist backup\state-real.json copy backup\state-real.json state-real.json
   xcopy backup\logs\run-*.json logs\ /Y
   ```

4. **装 OpenD**：从 [moomoo OpenAPI 下载页](https://www.moomoo.com/download/OpenAPI) 下载 Windows 版，解压后进 `moomoo_OpenD-GUI_...` 那个文件夹，在文件资源管理器里双击安装程序（不要在 Codex 或其他 App 里运行）。版本最好和 `requirements.txt` 里的 moomoo-api 一致（现在是 10.11.7108）。打开后用 moomoo 账号登录，输入手机验证码，勾上“记住密码”和“自动登录”。
5. **开机自动打开 OpenD**：按 Win+R，输入 `shell:startup` 回车，把桌面上的“moomoo OpenD”快捷方式复制进去。
6. **恢复计划任务**：在 PowerShell 里进入程序文件夹，运行：

   ```
   cd C:\Users\Mitchell\Documents\moomoo
   Register-ScheduledTask -TaskName moomoo-autoinvest -Xml (Get-Content backup\moomoo-autoinvest.xml -Raw)
   ```

   如果提示用户不对（新电脑的 Windows 用户名变了），按 README 的“每天自动运行”一节重新建：工作日 10:30 起每 2 小时一次共 3 次，再加一个 16:10 的触发时间，并打开“错过后尽快运行”。
7. **电源设置**：设置 → 系统 → 电源，插电时“睡眠”选“从不”。交易日 10:30–16:10 电脑要开着，别手动点睡眠。
8. **看板**：运行 `powershell -ExecutionPolicy Bypass -File dashboard\install-autostart.ps1`，登录 Windows 时自动启动看板（http://localhost:8080），详见 [dashboard.md](dashboard.md)。
9. **检查**：运行 `.venv\Scripts\python -m autoinvest status`，能看到模拟账户号和持仓就说明连上了。然后在项目里跟 Claude 说一声“检查电脑”，Claude 会核对一遍。

如果 Claude 需要在新电脑上帮你操作，要重新在新电脑上打开 Remote Control（在项目里说一声，Claude 会发连接卡片）。
