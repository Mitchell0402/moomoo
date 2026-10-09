// 手写的 SVG 图表：折线图（带悬停读数）、柱状图、迷你走势、环形图。不依赖任何第三方库。
const NS = "http://www.w3.org/2000/svg";

export function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

export function niceTicks(lo, hi, count = 4) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const start = Math.floor(lo / step) * step;
  const ticks = [];
  for (let v = start; v <= hi + step * 0.5; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

function pathOf(xs, ys, X, Y) {
  let d = "", pen = false;
  for (let i = 0; i < xs.length; i++) {
    const v = ys[i];
    if (v === null || v === undefined || Number.isNaN(v)) { pen = false; continue; }
    d += `${pen ? "L" : "M"}${X(xs[i]).toFixed(1)},${Y(v).toFixed(1)}`;
    pen = true;
  }
  return d;
}

const observers = new WeakMap();
function watch(node, draw) {
  node._draw = draw;
  if (observers.has(node)) return;
  let w = node.clientWidth, h = node.clientHeight;
  const ro = new ResizeObserver(() => {
    if (Math.abs(node.clientWidth - w) > 1 || Math.abs(node.clientHeight - h) > 1) {
      w = node.clientWidth; h = node.clientHeight;
      node._draw && node._draw();
    }
  });
  ro.observe(node);
  observers.set(node, ro);
}

/* 折线图
   o.xs: 横坐标（数字）；o.xDomain: [min, max]；o.series: [{name, color, values, width, dash, visible}]
   o.yFmt / o.tipFmt: 数字格式；o.xTicks: [{x, label}]；o.tipTitle(i)；o.zero: 参考线的值；o.marks: [{x, label}] */
export function lineChart(node, o) {
  watch(node, () => lineChart(node, o));
  node.innerHTML = "";
  const W = node.clientWidth, H = node.clientHeight;
  if (!W || !H) return;
  const m = { l: o.left ?? 52, r: o.right ?? 14, t: 12, b: 26 };
  const vis = o.series.filter((s) => s.visible !== false);
  const all = vis.flatMap((s) => s.values).filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  if (o.zero !== undefined && o.zero !== null) all.push(o.zero);
  if (!all.length || !o.xs.length) {
    node.innerHTML = `<div class="empty" style="height:100%">${o.empty || "暂无数据"}</div>`;
    return;
  }
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.002 || 1;
  lo -= pad; hi += pad;
  const ticks = niceTicks(lo, hi, o.yTickCount || 4).filter((t) => t >= lo && t <= hi);
  const [x0, x1] = o.xDomain || [o.xs[0], o.xs[o.xs.length - 1]];
  const X = (x) => m.l + (x1 === x0 ? (W - m.l - m.r) / 2 : ((x - x0) / (x1 - x0)) * (W - m.l - m.r));
  const Y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);

  const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": o.label || "" }, node);
  const grid = el("g", { class: "grid" }, svg);
  const axis = el("g", { class: "axis" }, svg);
  for (const t of ticks) {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) }, grid);
    const tx = el("text", { x: m.l - 10, y: Y(t) + 3.5, "text-anchor": "end" }, axis);
    tx.textContent = o.yFmt(t);
  }
  for (const t of o.xTicks || []) {
    const tx = el("text", { x: X(t.x), y: H - 6, "text-anchor": t.anchor || "middle" }, axis);
    tx.textContent = t.label;
  }
  if (o.zero !== undefined && o.zero !== null) {
    el("line", { class: "zero", x1: m.l, x2: W - m.r, y1: Y(o.zero), y2: Y(o.zero) }, svg);
  }
  for (const mk of o.marks || []) {
    const g = el("g", {}, svg);
    el("line", { x1: X(mk.x), x2: X(mk.x), y1: m.t, y2: H - m.b, stroke: css("--line-strong"), "stroke-dasharray": "2 3" }, g);
    const tx = el("text", { x: X(mk.x) + 4, y: m.t + 10, fill: css("--text-3"), "font-size": 10.5 }, g);
    tx.textContent = mk.label;
  }
  for (const s of [...vis].reverse()) {
    if (s.area) {
      const d = pathOf(o.xs, s.values, X, Y);
      if (d) {
        const firstI = s.values.findIndex((v) => v !== null && v !== undefined);
        let lastI = s.values.length - 1;
        while (lastI >= 0 && (s.values[lastI] === null || s.values[lastI] === undefined)) lastI--;
        const base = Y(o.zero ?? lo);
        el("path", { d: `${d}L${X(o.xs[lastI])},${base}L${X(o.xs[firstI])},${base}Z`, fill: s.color, opacity: 0.07 }, svg);
      }
    }
    el("path", {
      d: pathOf(o.xs, s.values, X, Y), fill: "none", stroke: s.color, "stroke-width": s.width || 1.5,
      "stroke-dasharray": s.dash || null, "stroke-linejoin": "round", "stroke-linecap": "round",
    }, svg);
    if (o.dots) {
      s.values.forEach((v, i) => {
        if (v !== null && v !== undefined) el("circle", { cx: X(o.xs[i]), cy: Y(v), r: s.width > 1.8 ? 3 : 2.2, fill: s.color }, svg);
      });
    }
  }
  if (o.endDot) {
    const s = vis[0];
    let i = s ? s.values.length - 1 : -1;
    while (i >= 0 && (s.values[i] === null || s.values[i] === undefined)) i--;
    if (i >= 0) {
      el("circle", { cx: X(o.xs[i]), cy: Y(s.values[i]), r: 7, fill: s.color, opacity: 0.18 }, svg);
      el("circle", { cx: X(o.xs[i]), cy: Y(s.values[i]), r: 3.5, fill: s.color }, svg);
    }
  }

  // 悬停读数
  const tip = document.createElement("div");
  tip.className = "tip";
  node.appendChild(tip);
  const cross = el("line", { y1: m.t, y2: H - m.b, stroke: css("--text-3"), "stroke-width": 1, opacity: 0 }, svg);
  const dots = vis.map((s) => el("circle", { r: 4, fill: s.color, stroke: css("--surface"), "stroke-width": 2, opacity: 0 }, svg));
  const hit = el("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" }, svg);
  const hide = () => { tip.classList.remove("show"); cross.setAttribute("opacity", 0); dots.forEach((d) => d.setAttribute("opacity", 0)); };
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
    let best = -1, bd = Infinity;
    for (let i = 0; i < o.xs.length; i++) {
      if (!vis.some((s) => s.values[i] !== null && s.values[i] !== undefined)) continue;
      const d = Math.abs(X(o.xs[i]) - px);
      if (d < bd) { bd = d; best = i; }
    }
    if (best < 0) return hide();
    const cx = X(o.xs[best]);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("opacity", 0.6);
    let rows = "";
    vis.forEach((s, k) => {
      const v = s.values[best];
      if (v === null || v === undefined) { dots[k].setAttribute("opacity", 0); return; }
      dots[k].setAttribute("cx", cx); dots[k].setAttribute("cy", Y(v)); dots[k].setAttribute("opacity", 1);
      rows += `<div class="row"><span><i style="background:${s.color}"></i>${s.name}</span><b>${(o.tipFmt || o.yFmt)(v, s, best)}</b></div>`;
    });
    tip.innerHTML = `<div class="t">${o.tipTitle(best)}</div>${rows}`;
    tip.classList.add("show");
    const tw = tip.offsetWidth;
    tip.style.left = `${cx + 14 + tw > W ? cx - tw - 14 : cx + 14}px`;
    tip.style.top = `${m.t + 4}px`;
  };
  hit.addEventListener("mousemove", move);
  hit.addEventListener("touchstart", move, { passive: true });
  hit.addEventListener("touchmove", move, { passive: true });
  hit.addEventListener("mouseleave", hide);
}

/* 每日涨跌柱：o.labels, o.values（我的账户），o.refs（标普 500，画成横线） */
export function barChart(node, o) {
  watch(node, () => barChart(node, o));
  node.innerHTML = "";
  const W = node.clientWidth, H = node.clientHeight;
  if (!W || !H) return;
  if (!o.values.length) { node.innerHTML = `<div class="empty" style="height:100%">暂无数据</div>`; return; }
  const m = { l: o.left ?? 52, r: 14, t: 8, b: 22 };
  const all = [...o.values, ...o.refs].filter((v) => v !== null && v !== undefined);
  const ext = Math.max(0.001, ...all.map(Math.abs)) * 1.15;
  const Y = (v) => m.t + (1 - (v + ext) / (2 * ext)) * (H - m.t - m.b);
  const n = o.values.length, band = (W - m.l - m.r) / n, bw = Math.min(28, band * 0.56);
  const X = (i) => m.l + band * i + band / 2;
  const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, node);
  const axis = el("g", { class: "axis" }, svg);
  el("line", { x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0), stroke: css("--line-strong") }, svg);
  for (const t of [ext / 1.15, -ext / 1.15]) {
    const tx = el("text", { x: m.l - 10, y: Y(t) + 3.5, "text-anchor": "end" }, axis);
    tx.textContent = o.yFmt(t);
  }
  const every = Math.max(1, Math.ceil(n / Math.floor((W - m.l) / 64)));
  o.values.forEach((v, i) => {
    if (v !== null && v !== undefined) {
      const y = Math.min(Y(0), Y(v)), h = Math.max(1, Math.abs(Y(v) - Y(0)));
      el("rect", { x: X(i) - bw / 2, y, width: bw, height: h, rx: 2, fill: v >= 0 ? css("--up") : css("--down"), opacity: 0.8 }, svg);
    }
    const r = o.refs[i];
    if (r !== null && r !== undefined) el("line", { x1: X(i) - bw / 2 - 4, x2: X(i) + bw / 2 + 4, y1: Y(r), y2: Y(r), stroke: css("--spy"), "stroke-width": 2 }, svg);
    if (i % every === 0 || i === n - 1) {
      const tx = el("text", { x: X(i), y: H - 5, "text-anchor": "middle" }, axis);
      tx.textContent = o.labels[i];
    }
  });
  const tip = document.createElement("div");
  tip.className = "tip";
  node.appendChild(tip);
  const hit = el("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1, Math.floor((ev.clientX - r.left - m.l) / band)));
    tip.innerHTML = o.tip(i);
    tip.classList.add("show");
    const tw = tip.offsetWidth;
    tip.style.left = `${X(i) + 16 + tw > W ? X(i) - tw - 16 : X(i) + 16}px`;
    tip.style.top = "0px";
  });
  hit.addEventListener("mouseleave", () => tip.classList.remove("show"));
}

export function sparkline(values, prev, w = 120, h = 36, color = "currentColor") {
  const vs = values.filter((v) => v !== null && v !== undefined);
  if (vs.length < 2) return `<svg width="${w}" height="${h}"></svg>`;
  const all = prev ? [...vs, prev] : vs;
  const lo = Math.min(...all), hi = Math.max(...all), rg = hi - lo || 1;
  const X = (i) => (i / (vs.length - 1)) * (w - 2) + 1;
  const Y = (v) => 2 + (1 - (v - lo) / rg) * (h - 4);
  const d = vs.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const base = prev ? `<line x1="0" x2="${w}" y1="${Y(prev).toFixed(1)}" y2="${Y(prev).toFixed(1)}" stroke="${css("--line-strong")}" stroke-dasharray="2 3"/>` : "";
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${base}<path d="${d}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
}

export function donut(segments, size = 150, thick = 16, center = "", sub = "") {
  const total = segments.reduce((a, s) => a + Math.max(0, s.value), 0) || 1;
  const r = (size - thick) / 2, c = size / 2, C = 2 * Math.PI * r;
  let acc = 0, arcs = "";
  for (const s of segments) {
    const len = (Math.max(0, s.value) / total) * C;
    if (len <= 0) continue;
    const gap = len > 4 ? 2 : 0;
    arcs += `<circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="${s.color}" stroke-width="${thick}" stroke-dasharray="${(len - gap).toFixed(2)} ${(C - len + gap).toFixed(2)}" stroke-dashoffset="${(-acc).toFixed(2)}" transform="rotate(-90 ${c} ${c})"/>`;
    acc += len;
  }
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="配置">
    <circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="${css("--surface-2")}" stroke-width="${thick}"/>${arcs}
    <text x="${c}" y="${c - 2}" text-anchor="middle" fill="${css("--text")}" font-size="20" font-weight="600">${center}</text>
    <text x="${c}" y="${c + 17}" text-anchor="middle" fill="${css("--text-3")}" font-size="11.5">${sub}</text></svg>`;
}
