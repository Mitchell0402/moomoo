import { lineChart, barChart, sparkline, donut, css } from "/static/charts.js";

const $ = (id) => document.getElementById(id);
const MINUS = "−";
const GROUP_COLOR = { stock: "--stock", bond: "--bond", gold: "--gold", cash: "--cash" };
const GROUP_LABEL = { stock: "股票", bond: "债券", gold: "黄金", cash: "现金" };
let D = null;
let lastOk = 0;
let historyRange = 0;
const hidden = new Set();
const openRows = new Set();

// ---------------------------------------------------------------- 格式
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nn = (v) => v !== null && v !== undefined && !Number.isNaN(v);
function money(v, d = 2) {
  if (!nn(v)) return "—";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  return (v < 0 ? MINUS : "") + "$" + s;
}
function smoney(v, d = 2) {
  if (!nn(v)) return "—";
  return (v > 0 ? "+" : v < 0 ? MINUS : "") + "$" + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function pct(v, d = 2, sign = true) {
  if (!nn(v)) return "—";
  const s = Math.abs(v * 100).toFixed(d) + "%";
  return (v > 0 && sign ? "+" : v < 0 ? MINUS : "") + s;
}
const w = (v, d = 1) => (nn(v) ? (v * 100).toFixed(d) + "%" : "—");
const cls = (v) => (!nn(v) || Math.abs(v) < 1e-9 ? "flat" : v > 0 ? "up" : "down");
const price = (v) => (nn(v) ? v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—");
function cnDate(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${m} 月 ${d} 日`;
}
const weekday = (iso) => "日一二三四五六"[new Date(iso.slice(0, 10) + "T12:00:00").getDay()];
const hm = (iso) => (iso ? iso.slice(11, 16) : "");
const MODE = { execute: "执行", "dry-run": "演练", status: "查询" };

// ---------------------------------------------------------------- 顶栏、提醒
function renderTop() {
  document.documentElement.dataset.updown = D.settings.up_color;
  const env = $("env");
  const demo = D.feed.status === "demo";
  env.textContent = demo ? "演示数据" : D.env === "REAL" ? "实盘" : "模拟盘";
  env.className = "pill " + (demo ? "demo" : D.env === "REAL" ? "env-real" : "env-sim");
  const ph = D.market_phase;
  $("phase-dot").className = "dot " + ph.phase;
  $("phase").textContent = `${ph.label} · 美东 ${hm(D.generated_at)}`;
  $("foot-time").textContent = `生成于美东 ${D.generated_at.replace("T", " ")}`;

  const alerts = [...D.alerts];
  if (demo) alerts.unshift({ level: "info", text: "演示模式：行情是模拟的，用来预览页面，不是真实数据" });
  if (Date.now() - lastOk > 90000 && lastOk) alerts.unshift({ level: "error", text: "看板服务没有响应，正在重试；下面是最后一次拿到的数据" });
  $("alerts").innerHTML = alerts.map((a) => `<div class="alert ${a.level}"><span class="dot ${a.level === "info" ? "" : a.level}"></span><span>${esc(a.text)}</span></div>`).join("");
}

function tickUpdated() {
  if (!lastOk) return;
  const s = Math.round((Date.now() - lastOk) / 1000);
  $("updated").textContent = s < 5 ? "刚刚更新" : s < 60 ? `${s} 秒前更新` : `${Math.floor(s / 60)} 分钟前更新`;
}

// ---------------------------------------------------------------- 今日概览
let prevValue = null;
function renderHero() {
  const a = D.account;
  const v = $("value");
  if (nn(a.value)) {
    const [i, c] = money(a.value).split(".");
    v.innerHTML = `${i}<span class="cents">.${c}</span>`;
    if (prevValue !== null && Math.abs(prevValue - a.value) > 0.004) { v.classList.remove("flash"); void v.offsetWidth; v.classList.add("flash"); }
    prevValue = a.value;
  }
  const ph = D.market_phase;
  const when = ph.phase === "open" ? "今日" : ph.quote_date === D.generated_at.slice(0, 10) ? "今日" : `${cnDate(ph.quote_date)}`;
  const tag = a.live ? (ph.phase === "open" ? "实时" : ph.phase === "post" || ph.phase === "closed" ? "已收盘" : "盘前，显示上一交易日")
    : `程序 ${hm(a.as_of)} 记录`;
  $("day").innerHTML = `<span class="${cls(a.day_pnl)}">${smoney(a.day_pnl)}</span><span class="${cls(a.day_pct)}">${pct(a.day_pct)}</span><span class="tag">${when} · ${tag}</span>`;

  const it = D.intraday;
  const series = [
    { key: "account", name: "我的账户", color: css("--accent"), width: 2.2, area: true },
    { key: "spy", name: "标普 500", color: css("--spy"), width: 1.4 },
    { key: "balanced", name: "股债 60/40", color: css("--series-3"), width: 1.2, dash: "4 4" },
  ].filter((s) => it && it[s.key]);
  const lastOf = (arr) => { for (let i = arr.length - 1; i >= 0; i--) if (nn(arr[i])) return arr[i]; return null; };
  $("intraday-legend").innerHTML = series.map((s) => `<button data-k="${s.key}" class="${hidden.has("i-" + s.key) ? "off" : ""}"><span class="swatch ${s.dash ? "dash" : ""}" style="border-color:${s.color}"></span>${s.name}<span class="val ${cls(lastOf(it[s.key]))}">${pct(lastOf(it[s.key]))}</span></button>`).join("");
  $("intraday-legend").querySelectorAll("button").forEach((b) => b.onclick = () => { const k = "i-" + b.dataset.k; hidden.has(k) ? hidden.delete(k) : hidden.add(k); renderHero(); });

  const node = $("intraday");
  if (!it) {
    node.innerHTML = `<div class="empty" style="height:100%">${a.live ? "今天还没有分钟数据，开盘后会显示走势" : "没有连上 OpenD，暂时看不到当天的分钟走势。<br>打开 OpenD 后一分钟内会自动恢复。"}</div>`;
    return;
  }
  const mins = it.t.map((t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m - 570; });
  lineChart(node, {
    xs: mins, xDomain: [0, 390], zero: 0, endDot: true,
    series: series.map((s) => ({ ...s, values: it[s.key], visible: !hidden.has("i-" + s.key) })),
    yFmt: (v) => pct(v, Math.abs(v) < 0.01 ? 2 : 1), tipFmt: (v) => pct(v),
    xTicks: [{ x: 0, label: "9:30", anchor: "start" }, ...(node.clientWidth < 560 ? [3, 5] : [2, 3, 4, 5, 6]).map((h) => ({ x: h * 60 - 30, label: `${9 + h}:00` })), { x: 390, label: "16:00", anchor: "end" }],
    marks: (it.marks || []).filter((m) => m.orders > 0).map((m) => { const [h, mm] = m.t.split(":").map(Number); return { x: h * 60 + mm - 570, label: `调仓 ${m.orders} 笔` }; }),
    tipTitle: (i) => `${it.t[i]} 美东`, label: "今天账户和标普 500 的涨跌",
  });
}

function renderCompare() {
  const c = D.compare;
  const me = c.find((x) => x.key === "actual"), spy = c.find((x) => x.key === "spy");
  const sw = D.account.stock_weight;
  if (me && spy && nn(me.pct) && nn(spy.pct)) {
    const diff = (me.pct - spy.pct) * 100;
    const word = Math.abs(diff) < 0.005 ? "和标普 500 持平" : diff > 0 ? `跑赢标普 500 <span class="up">${diff.toFixed(2)}</span> 个百分点` : `跑输标普 500 <span class="down">${Math.abs(diff).toFixed(2)}</span> 个百分点`;
    $("verdict").innerHTML = word;
  } else {
    $("verdict").innerHTML = `<span class="muted" style="font-size:15px">${D.feed.status === "offline" ? "连上 OpenD 后显示标普 500 的对比" : "暂无对比数据"}</span>`;
  }
  const vals = c.map((x) => x.pct).filter(nn);
  const ext = Math.max(0.002, ...vals.map(Math.abs));
  $("compare").innerHTML = c.map((x) => {
    const v = x.pct;
    const width = nn(v) ? (Math.abs(v) / ext) * 50 : 0;
    const left = nn(v) && v < 0 ? 50 - width : 50;
    const color = x.key === "actual" ? css("--accent") : x.key === "spy" ? css("--spy") : css("--series-4");
    return `<div class="cmp-row ${x.key === "actual" ? "me" : ""}"><span class="name">${x.label}</span><span class="cmp-track"><span class="cmp-bar" style="left:${left}%;width:${width}%;background:${color}"></span></span><span class="v ${cls(v)}">${pct(v)}</span></div>`;
  }).join("");
  const hs = D.holdings.filter((h) => nn(h.day_pnl));
  const maxAbs = Math.max(0.01, ...hs.map((h) => Math.abs(h.day_pnl)));
  $("contrib").innerHTML = hs.length ? `<div class="subhead">今天的盈亏来自哪里</div>${[...hs].sort((a, b) => b.day_pnl - a.day_pnl).map((h) => {
    const wd = (Math.abs(h.day_pnl) / maxAbs) * 50;
    return `<div class="cmp-row"><span class="name"><b style="color:var(--text);font-weight:500">${h.symbol}</b></span><span class="cmp-track"><span class="cmp-bar" style="left:${h.day_pnl < 0 ? 50 - wd : 50}%;width:${wd}%;background:${css(GROUP_COLOR[h.group])};opacity:.8"></span></span><span class="v ${cls(h.day_pnl)}">${smoney(h.day_pnl)}</span></div>`;
  }).join("")}` : "";
  $("compare-sub").textContent = D.market_phase.phase === "open" ? "实时" : cnDate(D.market_phase.quote_date);
  $("compare-note").textContent = nn(sw)
    ? `账户里股票约占 ${Math.round(sw * 100)}%，其余是债券、黄金和现金，所以大涨的日子通常跑输标普 500、大跌的日子通常跑赢，这是设计使然。和同样股债比例的「股债 60/40」「规则基准」比更公平。`
    : "";
}

function renderKpis() {
  const a = D.account, s = D.schedule;
  const lastRun = D.runs[0];
  const items = [
    { label: "累计收益", value: `<span class="${cls(a.total_pnl)}">${smoney(a.total_pnl)}</span>`, foot: `${pct(a.total_return)} · 自 ${cnDate(D.settings.start_date)}起，本金 ${money(D.settings.budget, 0)}` },
    { label: "从高点回撤", value: a.drawdown > 0.0001 ? `<span class="down">${pct(-a.drawdown)}</span>` : `0.00%`, foot: `最高 ${money(a.peak)} · 超过 20% 不再加股票` },
    { label: "股票占比", value: w(a.stock_weight), foot: `现金 ${money(a.cash)}（${w(a.cash_weight)}）` },
    { label: "下次运行", value: s.next ? (s.next.slice(0, 10) === D.generated_at.slice(0, 10) ? hm(s.next) : `周${weekday(s.next)} ${hm(s.next)}`) : "—",
      foot: lastRun ? `最近一次 ${lastRun.time.slice(5, 10).replace("-", "/")} ${hm(lastRun.time)} ${lastRun.ok ? "正常" : "出错"} · 今天已运行 ${s.today_count} 次` : "还没有运行记录" },
  ];
  $("kpis").innerHTML = items.map((k) => `<div class="card kpi"><div class="label">${k.label}</div><div class="kpi-value">${k.value}</div><div class="kpi-foot">${k.foot}</div></div>`).join("");
}

// ---------------------------------------------------------------- 今日计划
let rationaleOpen = false;
function renderPlan() {
  const p = D.plan;
  const regime = p.regime === "above" ? "趋势向上 · 基准 70/20/10" : p.regime === "below" ? "趋势向下 · 基准 40/50/10" : "";
  const rat = p.rationale ? `<p class="rationale ${rationaleOpen ? "" : "clamp"}" id="rat">${esc(p.rationale)}</p><button class="linkbtn" id="rat-btn">${rationaleOpen ? "收起" : "展开全文"}</button>` : `<div class="empty">今天还没有 Claude 的分析。每个交易日早上 8:07 左右生成。</div>`;
  const src = p.sources.length ? `<div class="sources"><span class="faint">来源</span>${p.sources.map((u) => { let h = u; try { const x = new URL(u); const seg = x.pathname.split("/").filter(Boolean).pop(); h = x.hostname.replace(/^www\./, "").split(".")[0] + (seg ? " · " + decodeURIComponent(seg).replace(/\.[a-z]+$/, "") : ""); } catch (e) { /* 保留原文 */ } return `<a href="${esc(u)}" target="_blank" rel="noreferrer">${esc(h)}</a>`; }).join("")}</div>` : "";
  const tl = p.timeline.length ? p.timeline.map((r) => {
    const ok = !r.notes.some((n) => String(n).startsWith("错误"));
    const orders = p.orders_today.filter((o) => o.time === r.time);
    const chips = orders.length ? `<div class="orders-mini">${orders.map((o) => `<span class="chip"><span class="${o.side === "BUY" ? "side-buy" : "side-sell"}">${o.side === "BUY" ? "买" : "卖"}</span>${o.symbol} ${o.qty} 股 @ ${price(o.price)}</span>`).join("")}</div>` : "";
    return `<div class="tl-item"><span class="time">${hm(r.time)}</span><span class="rail"><span class="dot ${ok ? (r.orders ? "ok" : "") : "error"}"></span></span><div class="body"><b>${MODE[r.mode] || r.mode}${r.orders ? ` · 下单 ${r.orders} 笔` : " · 未交易"}</b><br>${r.notes.map(esc).join("；") || "—"}${chips}</div></div>`;
  }).join("") : `<div class="faint" style="font-size:13px">今天程序还没有运行。下一次：${D.schedule.next ? hm(D.schedule.next) : "—"}</div>`;
  const tlDay = p.timeline.length && p.timeline[0].time.slice(0, 10) !== D.generated_at.slice(0, 10) ? `（${cnDate(p.timeline[0].time)}）` : "";
  $("plan-main").innerHTML = `
    <div class="plan-head"><h3>${p.date ? `${cnDate(p.date)} 周${weekday(p.date)}` : "今日计划"}</h3><span class="pill state ${p.state}">${esc(p.state_label)}</span>${regime ? `<span class="pill">${regime}</span>` : ""}${p.from_github ? `<span class="pill" title="交易程序 10:30 运行时才会拉取，看板先从 GitHub 读到了">来自 GitHub</span>` : ""}</div>
    ${rat}${src}
    <div class="subhead">程序执行情况${tlDay}</div>
    <div class="timeline">${tl}</div>`;
  const btn = $("rat-btn");
  if (btn) btn.onclick = () => { rationaleOpen = !rationaleOpen; renderPlan(); };

  const rows = p.groups.map((g) => {
    const color = css(GROUP_COLOR[g.key]);
    const range = g.range ? `<span class="range" style="left:${g.range[0] * 100}%;width:${(g.range[1] - g.range[0]) * 100}%"></span>` : "";
    const base = nn(g.baseline) && g.key !== "cash" ? `<span class="base" style="left:${g.baseline * 100}%"></span>` : "";
    return `<div class="alloc-row"><div class="alloc-top"><span class="g"><i style="background:${color}"></i>${g.label}</span><span class="nums">当前 <b>${w(g.current)}</b> · 目标 <b>${w(g.target, 0)}</b>${nn(g.baseline) && g.key !== "cash" ? ` · 基准 ${w(g.baseline, 0)}` : ""}${g.range ? ` · 允许 ${w(g.range[0], 0)}–${w(g.range[1], 0)}` : ""}</span></div>
      <div class="track">${range}<span class="cur" style="width:${Math.min(100, (g.current || 0) * 100)}%;background:${color}"></span>${base}<span class="tgt" style="left:${(g.target || 0) * 100}%"></span></div></div>`;
  }).join("");
  const etf = p.etfs.map((e) => `<tr><td><span class="sym">${e.symbol}</span> <span class="faint" style="font-size:12px">${esc(e.name)}</span></td><td>${w(e.current)}</td><td>${w(e.target, 0)}</td><td class="faint">${w(e.baseline, 0)}</td></tr>`).join("");
  $("plan-alloc").innerHTML = `
    <div class="card-head"><h3>目标配置</h3><span class="sub">偏离超过 ${Math.round((p.rebalance_band || 0.05) * 100)} 个百分点才调仓</span></div>
    <div class="alloc-legend"><span><i class="lg-cur"></i>当前</span><span><i class="lg-tgt"></i>今日目标</span><span><i class="lg-base"></i>规则基准</span><span><i class="lg-range"></i>Claude 可调范围</span></div>
    ${rows}
    <div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>ETF</th><th>当前</th><th>目标</th><th>基准</th></tr></thead><tbody>${etf}</tbody></table></div>`;
}

// ---------------------------------------------------------------- 市场
function renderMarket() {
  const m = D.market;
  $("market-sub").textContent = D.feed.status === "offline" ? "没有连上 OpenD" : `用对应 ETF 代表 · ${D.market_phase.phase === "open" ? "实时" : cnDate(D.market_phase.quote_date) + " 收盘"}`;
  if (!m.length) {
    $("tiles").innerHTML = `<div class="card empty" style="grid-column:1/-1">没有连上 OpenD，暂时看不到行情。打开 OpenD 后一分钟内会自动恢复。</div>`;
  } else {
    $("tiles").innerHTML = m.map((q) => {
      const color = q.pct >= 0 ? css("--up") : css("--down");
      const pos = nn(q.high52) && nn(q.low52) && q.high52 > q.low52 ? Math.max(0, Math.min(1, (q.price - q.low52) / (q.high52 - q.low52))) : null;
      return `<div class="card tile"><div class="tile-top"><span class="lb">${esc(q.label)}</span><span class="sy">${q.symbol}</span></div>
        <div class="tile-mid"><div><div class="tile-price">${price(q.price)}</div><div class="tile-chg ${cls(q.pct)}">${q.change >= 0 ? "+" : MINUS}${price(Math.abs(q.change))}　${pct(q.pct)}</div></div>${sparkline(q.spark, q.prev_close, 112, 40, color)}</div>
        ${pos !== null ? `<div class="range52"><div class="bar"><em style="left:${pos * 100}%"></em></div><div class="ends"><span>${price(q.low52)}</span><span>52 周区间 ${Math.round(pos * 100)}%</span><span>${price(q.high52)}</span></div></div>` : ""}</div>`;
    }).join("");
  }
  const t = D.trend;
  if (nn(t.sma)) {
    const pa = t.pct_above;
    const pos = Math.max(4, Math.min(96, 50 + (pa / 0.2) * 50));
    $("trend").innerHTML = `<div><div class="label">趋势护栏</div>
      <div style="font-size:18px;font-weight:600;margin:6px 0 4px">${t.symbol} ${pa >= 0 ? "在" : "跌破"} 10 个月均线${pa >= 0 ? "上方" : "，在下方"} <span class="${pa >= 0 ? "up" : "down"}">${pct(Math.abs(pa), 1, false)}</span></div>
      <div class="muted" style="font-size:13px">${pa >= 0 ? "趋势向上，规则基准是 70% 股票 / 20% 债券 / 10% 黄金。" : "趋势向下，股票最多 40%，规则基准是 40% / 50% / 10%。"}${t.drawdown_brake ? " 回撤刹车已触发：不再加股票。" : ""}</div></div>
      <div><div class="trend-scale"><span class="line"></span><span class="mid"></span><span class="pt" style="left:${pos}%"></span><span class="lab" style="left:50%">均线 ${price(t.sma)}</span><span class="lab" style="left:${pos}%;top:-6px">现价 ${price(t.price)}</span></div>
      <div class="ends faint" style="display:flex;justify-content:space-between;font-size:11px;margin-top:6px"><span>低 20%</span><span>高 20%</span></div></div>`;
  } else {
    $("trend").innerHTML = `<div class="faint">还没有趋势数据（需要程序运行一次）</div>`;
  }
}

// ---------------------------------------------------------------- 持仓
function renderHoldings() {
  const hs = D.holdings, a = D.account;
  const band = D.plan.rebalance_band || 0.05;
  const rows = hs.map((h) => {
    const color = css(GROUP_COLOR[h.group] || "--stock");
    const drift = nn(h.drift) ? `<span class="${Math.abs(h.drift) >= band * 0.8 ? "drift-warn" : "muted"}">${h.drift > 0 ? "+" : h.drift < 0 ? MINUS : ""}${Math.abs(h.drift * 100).toFixed(1)}</span>` : "—";
    return `<tr><td><div class="sym-cell"><i style="background:${color}"></i><div><div class="sym">${h.symbol}</div><div class="nm">${esc(h.name)}</div></div></div></td>
      <td>${h.qty}</td><td>${price(h.price)}</td><td class="${cls(h.day_pct)}">${pct(h.day_pct)}</td><td>${money(h.value)}</td><td class="${cls(h.day_pnl)}">${smoney(h.day_pnl)}</td>
      <td><span class="wbar"><span>${w(h.weight)}</span><span class="bar"><span style="width:${Math.min(100, (h.weight || 0) * 100)}%;background:${color}"></span><em style="left:${(h.target || 0) * 100}%"></em></span></span></td>
      <td class="muted">${w(h.target, 0)}</td><td>${drift}</td></tr>`;
  }).join("");
  const dayTotal = hs.reduce((s, h) => s + (h.day_pnl || 0), 0);
  $("holdings-table").innerHTML = `<table><thead><tr><th>ETF</th><th>股数</th><th>现价</th><th>今日</th><th>市值</th><th>今日盈亏</th><th>比例</th><th>目标</th><th>偏离（点）</th></tr></thead>
    <tbody>${rows}<tr><td><div class="sym-cell"><i style="background:${css("--cash")}"></i><div><div class="sym">现金</div><div class="nm">美元</div></div></div></td><td></td><td></td><td></td><td>${money(a.cash)}</td><td></td><td><span class="wbar"><span>${w(a.cash_weight)}</span><span class="bar"><span style="width:${(a.cash_weight || 0) * 100}%;background:${css("--cash")}"></span></span></span></td><td></td><td></td></tr></tbody>
    <tfoot><tr><td>合计</td><td></td><td></td><td></td><td>${money(a.value)}</td><td class="${cls(dayTotal)}">${smoney(dayTotal)}</td><td>100%</td><td></td><td></td></tr></tfoot></table>`;

  const groups = ["stock", "bond", "gold"].map((g) => ({ key: g, value: hs.filter((h) => h.group === g).reduce((s, h) => s + (h.value || 0), 0) }));
  groups.push({ key: "cash", value: a.cash || 0 });
  const total = groups.reduce((s, g) => s + g.value, 0) || 1;
  const target = Object.fromEntries(D.plan.groups.map((g) => [g.key, g.target]));
  $("donut").innerHTML = `<div class="card-head"><h3>资产配置</h3><span class="sub">当前 / 目标</span></div>
    <div class="donut-wrap">${donut(groups.map((g) => ({ value: g.value, color: css(GROUP_COLOR[g.key]) })), 150, 16, w(a.stock_weight, 0), "股票")}
    <div class="donut-legend">${groups.map((g) => `<div><i style="background:${css(GROUP_COLOR[g.key])}"></i><span>${GROUP_LABEL[g.key]}<span class="t">${money(g.value, 0)}</span></span><span class="num">${w(g.value / total)} <span class="faint">/ ${w(target[g.key], 0)}</span></span></div>`).join("")}</div></div>
    ${driftGauge(hs, band)}`;
}

function driftGauge(hs, band) {
  const top = hs.filter((h) => nn(h.drift)).sort((a, b) => Math.abs(b.drift) - Math.abs(a.drift))[0];
  if (!top) return "";
  const d = Math.abs(top.drift), ratio = Math.min(1, d / band);
  const near = ratio >= 0.8;
  return `<div class="subhead">离下一次调仓还有多远</div>
    <div class="alloc-top"><span class="nums">偏离最大的是 <b>${top.symbol}</b>：${top.drift > 0 ? "多" : "少"} <b>${(d * 100).toFixed(1)}</b> 个百分点</span><span class="nums">调仓线 ${(band * 100).toFixed(0)} 点</span></div>
    <div class="track"><span class="cur" style="width:${ratio * 100}%;background:${near ? css("--warn") : css("--series-3")}"></span><span class="tgt" style="left:100%"></span></div>
    <p class="footnote">${near ? "已经接近调仓线，下一次运行可能会交易。" : "任一只 ETF 偏离目标超过调仓线时，下一次运行才会买卖；平时不动，省手续费也少折腾。"}</p>`;
}

// ---------------------------------------------------------------- 走势
function renderHistory() {
  const h = D.history;
  const n = h.dates.length;
  const k = historyRange && historyRange < n ? historyRange : n;
  const idx = [...Array(k).keys()].map((i) => i + n - k);
  const pick = (arr) => (arr ? idx.map((i) => arr[i]) : []);
  const defs = [
    { key: "actual", name: "我的账户", color: css("--accent"), width: 2.4, area: true },
    { key: "spy", name: h.spy_label || "标普 500", color: css("--spy"), width: 1.5 },
    { key: "fixed_60_40", name: "股债 60/40", color: css("--series-3"), width: 1.3, dash: "5 4" },
    { key: "baseline", name: "规则基准", color: css("--series-4"), width: 1.3, dash: "2 3" },
  ].filter((s) => h.series[s.key]);
  const lastOf = (arr) => { for (let i = arr.length - 1; i >= 0; i--) if (nn(arr[i])) return arr[i]; return null; };
  const budget = D.settings.budget;
  $("history-legend").innerHTML = defs.map((s) => { const v = lastOf(h.series[s.key]); return `<button data-k="${s.key}" class="${hidden.has("h-" + s.key) ? "off" : ""}"><span class="swatch ${s.dash ? "dash" : ""}" style="border-color:${s.color}"></span>${s.name}<span class="val ${cls(v - budget)}">${nn(v) ? pct(v / budget - 1) : "—"}</span></button>`; }).join("");
  $("history-legend").querySelectorAll("button").forEach((b) => b.onclick = () => { const kk = "h-" + b.dataset.k; hidden.has(kk) ? hidden.delete(kk) : hidden.add(kk); renderHistory(); });
  $("history-sub").textContent = n ? `${cnDate(h.dates[0])}起 · 每个交易日一个点（当天最后一次运行，今天是实时）` : "";
  const dates = pick(h.dates);
  const every = Math.max(1, Math.ceil(k / 8));
  lineChart($("history-chart"), {
    xs: idx.map((_, i) => i), xDomain: [-0.3, k - 0.7], dots: k <= 30, zero: budget,
    series: defs.map((s) => ({ ...s, values: pick(h.series[s.key]), visible: !hidden.has("h-" + s.key) })),
    yFmt: (v) => "$" + Math.round(v).toLocaleString("en-US"), tipFmt: (v) => `${money(v)}　<span class="${cls(v - budget)}">${pct(v / budget - 1)}</span>`,
    xTicks: dates.map((d, i) => ({ x: i, label: d.slice(5).replace("-", "/") })).filter((_, i) => i % every === 0 || i === k - 1),
    tipTitle: (i) => `${cnDate(dates[i])} 周${weekday(dates[i])}`, left: 58, empty: "还没有历史数据", label: "资产走势",
  });
  const daily = [...D.daily].reverse().slice(-k);
  barChart($("daily-chart"), {
    labels: daily.map((d) => d.date.slice(5).replace("-", "/")), values: daily.map((d) => d.pct), refs: daily.map((d) => d.spy_pct),
    yFmt: (v) => pct(v, 1), left: 58,
    tip: (i) => { const d = daily[i]; return `<div class="t">${cnDate(d.date)}</div><div class="row"><span><i style="background:${css("--accent")}"></i>我的账户</span><b class="${cls(d.pct)}">${pct(d.pct)}</b></div><div class="row"><span><i style="background:${css("--spy")}"></i>标普 500</span><b>${pct(d.spy_pct)}</b></div>`; },
  });
}

// ---------------------------------------------------------------- 策略
function renderStrategies() {
  const ss = D.strategies;
  const ext = Math.max(0.002, ...ss.map((s) => Math.abs(s.return || 0)));
  const rank = ss.findIndex((s) => s.key === "actual") + 1;
  $("strategy-table").innerHTML = `<table><thead><tr><th>策略</th><th class="l desc">说明</th><th>当前价值</th><th>今日</th><th>累计收益</th></tr></thead><tbody>${ss.map((s, i) => {
    const r = s.return || 0, wd = (Math.abs(r) / ext) * 50;
    const color = s.key === "actual" ? css("--accent") : r >= 0 ? css("--up") : css("--down");
    return `<tr class="${s.key === "actual" ? "me" : ""}"><td><span class="rank">${i + 1}</span><b style="font-weight:${s.key === "actual" ? 600 : 500}">${esc(s.name)}</b></td><td class="l desc">${esc(s.desc.replace(/^[^：]{1,12}：/, ""))}</td><td>${money(s.value)}</td><td class="${cls(s.day_pct)}">${pct(s.day_pct)}</td>
      <td><span class="retbar"><span class="bar"><span style="left:${r < 0 ? 50 - wd : 50}%;width:${wd}%;background:${color};opacity:${s.key === "actual" ? 1 : 0.55}"></span></span><span class="${cls(r)}" style="min-width:58px;display:inline-block">${pct(r)}</span></span></td></tr>`;
  }).join("")}</tbody></table>
  <p class="footnote">我的账户目前排第 ${rank} / ${ss.length}。只跑了几天时排名主要是运气，至少看几个月再下结论。</p>`;
}

// ---------------------------------------------------------------- 记录
function renderRecords() {
  const rows = D.daily.map((d) => {
    const open = openRows.has(d.date);
    const tag = d.time === "实时" ? `<span class="tag-live">实时</span>` : d.partial ? `<span class="tag-partial">${d.time} 记录</span>` : "";
    return `<tr data-d="${d.date}" style="cursor:${d.rationale ? "pointer" : "default"}"><td><span class="expand ${open ? "open" : ""}">${d.rationale ? "›" : ""}</span> ${cnDate(d.date)} <span class="faint">周${weekday(d.date)}</span>${tag}</td>
      <td>${money(d.value)}</td><td class="${cls(d.change)}">${smoney(d.change)}</td><td class="${cls(d.pct)}">${pct(d.pct)}</td><td class="${cls(d.spy_pct)}">${pct(d.spy_pct)}</td>
      <td class="${cls(d.diff)}">${nn(d.diff) ? (d.diff > 0 ? "+" : d.diff < 0 ? MINUS : "") + Math.abs(d.diff * 100).toFixed(2) : "—"}</td><td>${w(d.stock_weight)}</td><td>${d.orders || "—"}</td></tr>
      ${open ? `<tr class="detail"><td colspan="8">${esc(d.rationale)}</td></tr>` : ""}`;
  }).join("");
  $("daily-table").innerHTML = D.daily.length ? `<table><thead><tr><th>日期</th><th>资产</th><th>当日盈亏</th><th>当日涨跌</th><th>标普 500</th><th>相对标普（点）</th><th>股票占比</th><th>交易笔数</th></tr></thead><tbody>${rows}</tbody></table>` : `<div class="empty">还没有记录</div>`;
  $("daily-table").querySelectorAll("tr[data-d]").forEach((tr) => tr.onclick = () => { const d = tr.dataset.d; openRows.has(d) ? openRows.delete(d) : openRows.add(d); renderRecords(); });

  const os = D.orders.slice(0, 12);
  $("orders-table").innerHTML = os.length ? `<table><thead><tr><th>时间</th><th class="l">ETF</th><th class="l">方向</th><th>数量</th><th>限价</th><th>金额</th></tr></thead><tbody>${os.map((o) => `<tr><td class="muted">${o.time.slice(5, 10).replace("-", "/")} ${hm(o.time)}</td><td class="l"><span class="sym">${o.symbol}</span></td><td class="l"><span class="side ${o.side === "BUY" ? "buy" : "sell"}">${o.side === "BUY" ? "买入" : "卖出"}</span></td><td>${o.qty}</td><td>${price(o.price)}</td><td>${money(o.amount)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">还没有下过单</div>`;

  const rs = D.runs.slice(0, 10);
  $("runs-sub").textContent = `共 ${D.runs.length >= 30 ? "30+" : D.runs.length} 次 · 计划时间 ${D.settings.schedule.join("、")}`;
  $("runs-table").innerHTML = rs.length ? `<table><thead><tr><th>时间</th><th class="l hide-m">类型</th><th>资产</th><th class="l">结果</th></tr></thead><tbody>${rs.map((r) => `<tr><td class="muted">${r.time.slice(5, 10).replace("-", "/")} ${hm(r.time)}</td><td class="l hide-m">${MODE[r.mode] || r.mode}</td><td>${money(r.value)}</td><td class="l runs-note" style="white-space:normal;max-width:360px"><span class="dot ${runLevel(r)}" style="margin-right:6px"></span><span class="${r.ok ? "muted" : ""}" title="${esc(r.notes.join("；"))}">${esc(runText(r))}</span></td></tr>`).join("")}</tbody></table>` : `<div class="empty">还没有运行记录</div>`;

  const s = D.system, f = D.feed;
  const item = (lvl, name, val) => `<div><span class="dot ${lvl}"></span><span>${name}</span><span>${val}</span></div>`;
  const feedTxt = f.status === "live" ? `已连接 · ${hm(f.updated_at)} 更新` : f.status === "demo" ? "演示模式" : esc(f.message || "未连接");
  const gitTxt = s.git && s.git.fetched_at ? (s.git.ok ? `${hm(s.git.fetched_at)} 同步` : "同步失败") : "未启用";
  $("system").innerHTML = [
    item(f.status === "live" || f.status === "demo" ? "ok" : "warn", "OpenD 行情", feedTxt),
    item(s.git && s.git.ok === false ? "warn" : s.git && s.git.ok ? "ok" : "", "GitHub 同步", gitTxt),
    item(D.runs[0] && !D.runs[0].ok ? "error" : "ok", "交易程序", D.runs[0] ? `${D.runs[0].time.slice(5, 10).replace("-", "/")} ${hm(D.runs[0].time)} ${D.runs[0].ok ? "正常" : "出错"}` : "无记录"),
    item(s.stop ? "warn" : "ok", "总开关", s.stop ? "已关闭" : "打开"),
    item(s.halt ? "warn" : "ok", "远程急停", s.halt ? "已打开" : "未打开"),
    item(D.trend.drawdown_brake ? "warn" : "ok", "回撤刹车", D.trend.drawdown_brake ? "已触发" : "未触发"),
  ].join("");
}

function runLevel(r) {
  if (!r.ok) return "error";
  return r.notes.some((n) => /被拒绝|警告|失败|没有/.test(n)) ? "warn" : "ok";
}
function runText(r) {
  const main = r.notes.find((n) => !String(n).startsWith("采用")) || "";
  if (r.mode === "execute" && r.orders) return `下单 ${r.orders} 笔${main ? "；" + main : ""}`;
  return main || r.notes[0] || "正常";
}

// ---------------------------------------------------------------- 导航高亮
function navSpy() {
  const links = [...document.querySelectorAll("nav.sections a")];
  const io = new IntersectionObserver((es) => {
    for (const e of es) if (e.isIntersecting) links.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + e.target.id));
  }, { rootMargin: "-40% 0px -55% 0px" });
  document.querySelectorAll("main section").forEach((s) => io.observe(s));
}

function renderAll() {
  const parts = [renderTop, renderHero, renderCompare, renderKpis, renderPlan, renderMarket, renderHoldings, renderHistory, renderStrategies, renderRecords];
  for (const f of parts) {
    try { f(); } catch (e) { console.error(f.name, e); }
  }
}

let timer = null;
async function load() {
  clearTimeout(timer);
  let wait = 60000;
  try {
    const r = await fetch("/api/dashboard", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    D = await r.json();
    lastOk = Date.now();
    renderAll();
    wait = D.market_phase.phase === "open" ? 15000 : 60000;
  } catch (e) {
    if (D) renderTop();
    else $("alerts").innerHTML = `<div class="alert error"><span class="dot error"></span><span>连不上看板服务，正在重试</span></div>`;
    wait = 10000;
  }
  tickUpdated();
  timer = setTimeout(load, wait);
}

document.getElementById("range").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  historyRange = Number(b.dataset.n);
  document.querySelectorAll("#range button").forEach((x) => x.classList.toggle("on", x === b));
  if (D) renderHistory();
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && D && Date.now() - lastOk > 20000) load(); });
setInterval(tickUpdated, 1000);
navSpy();
load();
