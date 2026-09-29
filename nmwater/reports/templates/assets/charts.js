/* Small chart library for river report pages: line (with bands), bars, and a status strip.
   No dependencies. Every chart: resizes with its box, has a hover/touch/keyboard readout, and takes its
   colours from CSS custom properties so light and dark themes both work.
   Labels are inserted with textContent (data is never parsed as HTML). */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const DAY = 864e5;
  const f0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
  const f1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
  const fD = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const fM = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
  const fMo = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });

  function el(name, attrs, parent) {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function niceStep(span, target) {
    const raw = span / target, p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    return [1, 2, 2.5, 5, 10].map(m => m * p).find(s => s >= raw);
  }
  function yTicks(lo, hi) {
    if (hi === lo) hi = lo + 1;
    const step = niceStep(hi - lo, 5), a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step, out = [];
    for (let t = a; t <= b + 1e-9; t += step) out.push(+t.toFixed(6));
    return out;
  }
  function fmtVal(v, spec) { return v == null || Number.isNaN(v) ? "no data" : (spec.fmt ? spec.fmt(v) : (Math.abs(v) >= 100 ? f0 : f1).format(v)) + (spec.unit ? " " + spec.unit : ""); }
  function fmtX(x, spec) {
    if (spec.xType === "time") return spec.xRes === "month" ? fM.format(x) : fD.format(x);
    if (spec.xType === "doy") return fMo.format(Date.UTC(2021, 0, 1) + (x - 1) * DAY) + " " + new Date(Date.UTC(2021, 0, 1) + (x - 1) * DAY).getUTCDate();
    if (spec.xType === "cat") return spec.labels[x];
    return String(x);
  }
  function xTicks(spec, a, b, width) {
    const maxT = Math.max(3, Math.floor(width / 80)), out = [];
    if (spec.xType === "cat") { const n = spec.labels.length, every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(width / 70))));
      spec.labels.forEach((l, i) => { if ((i === 0 || l !== spec.labels[i - 1]) && (every === 1 || out.length === 0 || i - out[out.length - 1].x >= every)) out.push({ x: i, label: l }); });
      return out; }
    if (spec.xType === "year") { const st = [1, 2, 5, 10, 20, 25].find(s => (b - a) / s <= maxT) || 50;
      for (let y = Math.ceil(a / st) * st; y <= b; y += st) out.push({ x: y, label: String(y) }); return out; }
    if (spec.xType === "doy") { [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335].forEach((d, i) => { if (i % Math.ceil(12 / maxT) === 0) out.push({ x: d, label: fMo.format(Date.UTC(2021, i, 1)) }); }); return out; }
    const yrs = (b - a) / (365.25 * DAY);
    if (yrs <= 2.5) { const d = new Date(a); let m = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1); const every = Math.ceil(yrs * 12 / maxT);
      while (m <= b) { const dm = new Date(m); if (dm.getUTCMonth() % every === 0) out.push({ x: m, label: dm.getUTCMonth() === 0 ? String(dm.getUTCFullYear()) : fMo.format(m) });
        m = Date.UTC(dm.getUTCFullYear(), dm.getUTCMonth() + 1, 1); } return out; }
    const st = [1, 2, 5, 10, 20, 25, 50].find(s => yrs / s <= maxT) || 50;
    for (let y = Math.ceil(new Date(a).getUTCFullYear() / st) * st; Date.UTC(y, 0, 1) <= b; y += st) out.push({ x: Date.UTC(y, 0, 1), label: String(y) });
    return out;
  }

  function legend(box, items) {
    if (!items.length) return;
    const ul = document.createElement("ul"); ul.className = "legend";
    items.forEach(it => { const li = document.createElement("li"), i = document.createElement("i");
      if (it.kind === "box") i.className = "box"; if (it.kind === "dash") i.className = "dash";
      i.style.setProperty("--c", it.color); li.append(i, document.createTextNode(it.name)); ul.appendChild(li); });
    box.appendChild(ul);
  }
  function frame(box, spec) {
    box.classList.add("chart"); box.replaceChildren();
    if (spec.legendItems) legend(box, spec.legendItems);
    const svg = el("svg", { role: "img", tabindex: "0", "aria-label": (spec.label || "Chart") +
      ". Use the left and right arrow keys to read values; the data is also available as a table below the chart." }, box);
    const tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; tip.setAttribute("aria-hidden", "true"); box.appendChild(tip);
    // read out values to screen readers, only while the chart is driven from the keyboard
    const live = document.createElement("p"); live.className = "vh"; live.setAttribute("aria-live", "polite"); box.appendChild(live);
    box._live = live;
    return { svg, tip };
  }
  /* A "Show the data as a table" disclosure under the chart, built when first opened.
     head: column names; rows: arrays of cell text (first cell is the row header). */
  function dataTable(box, caption, head, rows) {
    const d = document.createElement("details"); d.className = "data";
    const s = document.createElement("summary"); s.textContent = "Show the data as a table"; d.appendChild(s);
    d.addEventListener("toggle", () => {
      if (!d.open || d.querySelector("table")) return;
      const wrap = document.createElement("div"); wrap.className = "scroll"; wrap.tabIndex = 0;
      wrap.setAttribute("role", "region"); wrap.setAttribute("aria-label", caption + " (table)");
      const t = document.createElement("table"), cap = document.createElement("caption");
      cap.className = "vh"; cap.textContent = caption; t.appendChild(cap);
      const hr = document.createElement("tr");
      head.forEach(h => { const th = document.createElement("th"); th.scope = "col"; th.textContent = h; hr.appendChild(th); });
      const th = document.createElement("thead"); th.appendChild(hr); t.appendChild(th);
      const tb = document.createElement("tbody");
      rows.forEach(r => { const tr = document.createElement("tr");
        r.forEach((c, i) => { const td = document.createElement(i ? "td" : "th"); if (!i) td.scope = "row"; td.textContent = c; tr.appendChild(td); });
        tb.appendChild(tr); });
      t.appendChild(tb); wrap.appendChild(t); d.appendChild(wrap);
    });
    box.appendChild(d);
  }
  function tipShow(box, tip, px, head, rows) {
    tip.hidden = false; tip.replaceChildren();
    const h = document.createElement("div"); h.className = "h"; h.textContent = head; tip.appendChild(h);
    rows.forEach(r => { const d = document.createElement("div"); d.className = "r"; d.style.setProperty("--c", r.color);
      const i = document.createElement("i"); if (r.box) i.className = "box"; const n = document.createElement("span"); n.textContent = r.name;
      const v = document.createElement("b"); v.textContent = r.value; d.append(i, n, v); tip.appendChild(d); });
    if (box._kbd && box._live) box._live.textContent = head + ": " + rows.map(r => r.name + " " + r.value).join("; ") + ".";
    const w = tip.offsetWidth; tip.style.left = Math.max(0, px + 14 + w > box.clientWidth ? px - w - 14 : px + 14) + "px"; tip.style.top = "6px";
  }
  function wire(svg, box, tip, n, onSel) {
    let sel = null;
    const pick = ev => { box._kbd = false; const r = svg.getBoundingClientRect(); onSel.pick((ev.clientX - r.left) * (svg.viewBox.baseVal.width / r.width)); };
    svg.addEventListener("pointermove", pick); svg.addEventListener("pointerdown", pick);
    svg.addEventListener("pointerleave", ev => { if (ev.pointerType === "mouse") onSel.clear(); });
    svg.addEventListener("blur", () => onSel.clear());
    svg.addEventListener("keydown", ev => { const k = ev.key; let i = onSel.cur();
      if (k === "Escape") { onSel.clear(); return; }
      if (k === "ArrowLeft") i = Math.max(0, (i ?? n) - 1); else if (k === "ArrowRight") i = Math.min(n - 1, (i ?? -1) + 1);
      else if (k === "Home") i = 0; else if (k === "End") i = n - 1; else return;
      box._kbd = true; ev.preventDefault(); onSel.set(i); });
    return sel;
  }

  /* line: {x:[...], xType:'time'|'doy'|'year'|'cat', labels?, series:[{name,values,color,dash,width,points}],
            bands:[{name, lo:[], hi:[], color}], unit, yMin, height, label} */
  function line(box, spec) {
    if (!spec.legendItems && (spec.series.length > 1 || (spec.bands || []).length))
      spec.legendItems = spec.series.map(s => ({ name: s.name, color: s.color, kind: s.dash ? "dash" : "line" }))
        .concat((spec.bands || []).map(b => ({ name: b.name, color: b.color, kind: "box" })));
    const { svg, tip } = frame(box, spec);
    const M = { t: 24, r: 12, b: 28, l: 52 };
    let W = 600, H = spec.height || 260, cur = null, active = false;
    function draw() {
      W = Math.max(280, box.clientWidth); svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.replaceChildren();
      const xs = spec.x, n = xs.length, a = xs[0], b = xs[n - 1];
      const all = [];
      spec.series.forEach(s => s.values.forEach(v => v != null && !Number.isNaN(v) && all.push(v)));
      (spec.bands || []).forEach(bd => { bd.lo.forEach(v => v != null && all.push(v)); bd.hi.forEach(v => v != null && all.push(v)); });
      let lo = spec.yMin != null ? Math.min(spec.yMin, ...all) : Math.min(...all), hi = Math.max(...all);
      if (!all.length) { lo = 0; hi = 1; }
      const ticks = yTicks(lo, hi); lo = ticks[0]; hi = ticks[ticks.length - 1];
      const x = v => spec.xType === "cat" ? M.l + (n === 1 ? 0.5 : v / (n - 1)) * (W - M.l - M.r) : M.l + (v - a) / Math.max(1e-9, b - a) * (W - M.l - M.r);
      const y = v => M.t + (1 - (v - lo) / Math.max(1e-9, hi - lo)) * (H - M.t - M.b);
      ticks.forEach(t => { el("line", { x1: M.l, x2: W - M.r, y1: y(t), y2: y(t), stroke: t === 0 ? "var(--axis)" : "var(--grid)" }, svg);
        el("text", { x: M.l - 6, y: y(t) + 4, "text-anchor": "end" }, svg).textContent = (Math.abs(t) >= 1000 ? f0 : f1).format(t); });
      if (spec.unit) el("text", { x: M.l - 6, y: 10, "text-anchor": "end" }, svg).textContent = spec.unit;
      xTicks(spec, a, b, W - M.l - M.r).forEach(tk => { const px = x(tk.x); if (px < M.l - 1 || px > W - M.r + 1) return;
        const anchor = px < M.l + 30 ? "start" : px > W - M.r - 30 ? "end" : "middle";
        el("text", { x: px, y: H - 8, "text-anchor": anchor }, svg).textContent = tk.label; });
      (spec.bands || []).forEach(bd => { let d = "", top = [], bot = [];
        xs.forEach((xv, i) => { if (bd.lo[i] == null || bd.hi[i] == null) return; top.push([x(spec.xType === "cat" ? i : xv), y(bd.hi[i])]); bot.push([x(spec.xType === "cat" ? i : xv), y(bd.lo[i])]); });
        if (top.length) { d = "M" + top.map(p => p.join(" ")).join("L") + "L" + bot.reverse().map(p => p.join(" ")).join("L") + "Z";
          el("path", { d, fill: bd.color, stroke: "none" }, svg); } });
      spec.series.forEach(s => { let d = "", pen = false;
        s.values.forEach((v, i) => { if (v == null || Number.isNaN(v)) { pen = false; return; } const px = x(spec.xType === "cat" ? i : xs[i]);
          d += (pen ? "L" : "M") + px.toFixed(1) + " " + y(v).toFixed(1); pen = true;
          if (s.points) el("circle", { cx: px, cy: y(v), r: 3, fill: s.color, stroke: "var(--surface-1)", "stroke-width": 1 }, svg); });
        if (!s.points || s.joined) el("path", { d, fill: "none", stroke: s.color, "stroke-width": s.width || 2, "stroke-dasharray": s.dash ? "5 4" : "none", "stroke-linejoin": "round" }, svg); });
      const g = el("g", {}, svg);
      if (active && cur != null) {
        const px = x(spec.xType === "cat" ? cur : xs[cur]);
        el("line", { x1: px, x2: px, y1: M.t, y2: H - M.b, stroke: "var(--text-muted)", "stroke-dasharray": "3 3" }, g);
        spec.series.forEach(s => { const v = s.values[cur]; if (v != null && !Number.isNaN(v)) el("circle", { cx: px, cy: y(v), r: 4, fill: s.color, stroke: "var(--surface-1)", "stroke-width": 2 }, g); });
        const rows = spec.series.map(s => ({ name: s.name, color: s.color, value: fmtVal(s.values[cur], spec) }));
        (spec.bands || []).forEach(bd => { if (bd.lo[cur] != null) rows.push({ name: bd.name, color: bd.color, box: true, value: fmtVal(bd.lo[cur], { fmt: spec.fmt }) + " to " + fmtVal(bd.hi[cur], spec) }); });
        tipShow(box, tip, px, fmtX(xs[cur], spec), rows);
      } else tip.hidden = true;
      draw.x = x;
    }
    wire(svg, box, tip, spec.x.length, {
      pick: px => { let best = 0, bd = Infinity; spec.x.forEach((xv, i) => { const d = Math.abs(draw.x(spec.xType === "cat" ? i : xv) - px); if (d < bd) { bd = d; best = i; } }); cur = best; active = true; draw(); },
      clear: () => { active = false; draw(); }, cur: () => cur, set: i => { cur = i; active = true; draw(); } });
    new ResizeObserver(draw).observe(box); draw();
    dataTable(box, spec.label || "Chart data", [spec.xLabel || (spec.xType === "year" ? "Year" : spec.xType === "cat" ? "Category" : "Date")]
      .concat(spec.series.map(s => s.name), (spec.bands || []).map(b => b.name)),
      spec.x.map((xv, i) => [fmtX(spec.xType === "cat" ? i : xv, spec)].concat(spec.series.map(s => fmtVal(s.values[i], spec)),
        (spec.bands || []).map(b => b.lo[i] == null ? "no data" : fmtVal(b.lo[i], { fmt: spec.fmt }) + " to " + fmtVal(b.hi[i], spec))))
        .filter(r => r.slice(1).some(c => c !== "no data")));
    return { redraw: draw };
  }

  /* bars: {x:[...], xType, labels?, values:[], color | colorFn(v,i), ref:{name, values, color}, unit, name, height, label, zero} */
  function bars(box, spec) {
    if (!spec.legendItems && spec.ref)
      spec.legendItems = [{ name: spec.name || "value", color: spec.color || "var(--s1)", kind: "box" }, { name: spec.ref.name, color: spec.ref.color || "var(--text-primary)", kind: "dash" }];
    const { svg, tip } = frame(box, spec);
    const M = { t: 24, r: 12, b: 28, l: 52 };
    let cur = null, active = false;
    function draw() {
      const W = Math.max(280, box.clientWidth), H = spec.height || 220; svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.replaceChildren();
      const n = spec.values.length, all = spec.values.filter(v => v != null).concat((spec.ref ? spec.ref.values : []).filter(v => v != null));
      const ticks = yTicks(Math.min(0, ...all), Math.max(0, ...all, 1)); const lo = ticks[0], hi = ticks[ticks.length - 1];
      const bw = (W - M.l - M.r) / Math.max(1, n), x = i => M.l + i * bw, y = v => M.t + (1 - (v - lo) / (hi - lo)) * (H - M.t - M.b);
      ticks.forEach(t => { el("line", { x1: M.l, x2: W - M.r, y1: y(t), y2: y(t), stroke: t === 0 ? "var(--axis)" : "var(--grid)" }, svg);
        el("text", { x: M.l - 6, y: y(t) + 4, "text-anchor": "end" }, svg).textContent = (Math.abs(t) >= 1000 ? f0 : f1).format(t); });
      if (spec.unit) el("text", { x: M.l - 6, y: 10, "text-anchor": "end" }, svg).textContent = spec.unit;
      const every = Math.ceil(n / Math.max(3, Math.floor((W - M.l - M.r) / 60)));
      spec.values.forEach((v, i) => {
        if (i % every === 0) el("text", { x: x(i) + bw / 2, y: H - 8, "text-anchor": "middle" }, svg).textContent = spec.xType === "cat" ? spec.labels[i] : (spec.tickFmt ? spec.tickFmt(spec.x[i]) : fmtX(spec.x[i], spec));
        if (v == null) return;
        const c = spec.colorFn ? spec.colorFn(v, i) : spec.color, top = y(Math.max(0, v)), bot = y(Math.min(0, v));
        el("rect", { x: x(i) + Math.min(1, bw * 0.1), y: top, width: Math.max(1, bw - Math.min(2, bw * 0.2)), height: Math.max(v === 0 ? 0 : 1, bot - top), fill: c, opacity: active && cur !== i ? 0.55 : 1 }, svg);
      });
      if (spec.ref) { let d = "", pen = false; spec.ref.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + (x(i) + bw / 2).toFixed(1) + " " + y(v).toFixed(1); pen = true; });
        el("path", { d, fill: "none", stroke: spec.ref.color || "var(--text-primary)", "stroke-width": 1.5, "stroke-dasharray": "4 3" }, svg); }
      if (active && cur != null) {
        const rows = [{ name: spec.name || "value", color: spec.colorFn ? spec.colorFn(spec.values[cur], cur) : spec.color, box: true, value: fmtVal(spec.values[cur], spec) }];
        if (spec.ref) rows.push({ name: spec.ref.name, color: spec.ref.color || "var(--text-primary)", value: fmtVal(spec.ref.values[cur], spec) });
        (spec.extra || []).forEach(e => rows.push({ name: e.name, color: e.color || "var(--axis)", value: e.values[cur] == null ? "" : String(e.values[cur]) }));
        tipShow(box, tip, x(cur) + bw / 2, spec.xType === "cat" ? spec.labels[cur] : fmtX(spec.x[cur], spec), rows);
      } else tip.hidden = true;
      draw.pick = px => Math.max(0, Math.min(n - 1, Math.floor((px - M.l) / bw)));
    }
    wire(svg, box, tip, spec.values.length, { pick: px => { cur = draw.pick(px); active = true; draw(); },
      clear: () => { active = false; draw(); }, cur: () => cur, set: i => { cur = i; active = true; draw(); } });
    new ResizeObserver(draw).observe(box); draw();
    const lab = i => spec.xType === "cat" ? spec.labels[i] : (spec.tickFmt && spec.xRes !== "month" ? spec.tickFmt(spec.x[i]) : fmtX(spec.x[i], spec));
    dataTable(box, spec.label || spec.name || "Chart data", [spec.xType === "year" ? "Year" : "Date", spec.name || "Value"]
      .concat(spec.ref ? [spec.ref.name] : [], (spec.extra || []).map(e => e.name)),
      spec.values.map((v, i) => [lab(i), fmtVal(v, spec)].concat(spec.ref ? [fmtVal(spec.ref.values[i], spec)] : [],
        (spec.extra || []).map(e => e.values[i] == null ? "" : String(e.values[i])))).filter(r => r[1] !== "no data" || r.length > 2));
  }

  /* strip: {rows:[names], x:[ms], cls:[[class or null]], pct:[[number or null]], classes:{name: cssVar}, label} */
  function strip(box, spec) {
    const { svg, tip } = frame(box, spec);
    let cur = null, active = false;
    function draw() {
      const W = Math.max(280, box.clientWidth), rowH = 22, lw = Math.min(190, W * 0.34), M = { t: 4, r: 8, b: 24, l: lw };
      const H = M.t + spec.rows.length * rowH + M.b; svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.replaceChildren();
      const n = spec.x.length, cw = (W - M.l - M.r) / n;
      spec.rows.forEach((name, r) => {
        const t = el("text", { x: M.l - 8, y: M.t + r * rowH + rowH / 2 + 4, "text-anchor": "end" }, svg); t.textContent = name.length > 26 ? name.slice(0, 25) + "…" : name;
        spec.cls[r].forEach((c, i) => el("rect", { x: M.l + i * cw + 0.5, y: M.t + r * rowH + 2, width: Math.max(1, cw - 1), height: rowH - 4,
          fill: c ? spec.classes[c] : "var(--c-none)", stroke: active && cur && cur[0] === r && cur[1] === i ? "var(--text-primary)" : "none", "stroke-width": 1.5 }, svg));
      });
      xTicks({ xType: "time" }, spec.x[0], spec.x[n - 1], W - M.l - M.r).forEach(tk => { const i = (tk.x - spec.x[0]) / (spec.x[n - 1] - spec.x[0]) * (n - 1);
        el("text", { x: M.l + (i + 0.5) * cw, y: H - 6, "text-anchor": "middle" }, svg).textContent = tk.label; });
      if (active && cur) { const [r, i] = cur, c = spec.cls[r][i], p = spec.pct[r][i];
        tipShow(box, tip, M.l + (i + 0.5) * cw, "Week of " + fD.format(spec.x[i]), [{ name: spec.rows[r], color: c ? spec.classes[c] : "var(--c-none)", box: true,
          value: c ? c + (p != null ? " (" + f0.format(p) + "th pct)" : "") : "no rating" }]); } else tip.hidden = true;
      draw.geo = { M, cw, rowH };
    }
    svg.addEventListener("pointermove", ev => { const r = svg.getBoundingClientRect(), g = draw.geo, s = svg.viewBox.baseVal.width / r.width;
      const px = (ev.clientX - r.left) * s, py = (ev.clientY - r.top) * s, i = Math.floor((px - g.M.l) / g.cw), row = Math.floor((py - g.M.t) / g.rowH);
      if (i >= 0 && i < spec.x.length && row >= 0 && row < spec.rows.length) { cur = [row, i]; active = true; draw(); } });
    svg.addEventListener("pointerleave", () => { active = false; draw(); });
    svg.addEventListener("pointermove", () => { box._kbd = false; });
    svg.addEventListener("blur", () => { active = false; draw(); });
    svg.addEventListener("keydown", ev => {
      const k = ev.key, n = spec.x.length, m = spec.rows.length; let [r, i] = cur || [0, n - 1];
      if (k === "Escape") { active = false; draw(); return; }
      if (!active && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(k)) { /* start on the latest week of the first row */ }
      else if (k === "ArrowLeft") i = Math.max(0, i - 1); else if (k === "ArrowRight") i = Math.min(n - 1, i + 1);
      else if (k === "ArrowUp") r = Math.max(0, r - 1); else if (k === "ArrowDown") r = Math.min(m - 1, r + 1);
      else if (k === "Home") i = 0; else if (k === "End") i = n - 1; else return;
      ev.preventDefault(); box._kbd = true; cur = [r, i]; active = true; draw(); });
    new ResizeObserver(draw).observe(box); draw();
    svg.setAttribute("aria-label", (spec.label || "Chart") + ". Use the arrow keys to move between weeks (left and right) and segments (up and down); the data is also available as a table below the chart.");
    dataTable(box, spec.label || "Chart data", ["Week of"].concat(spec.rows),
      spec.x.map((xv, i) => [fD.format(xv)].concat(spec.rows.map((_, r) => spec.cls[r][i] ? spec.cls[r][i] + (spec.pct[r][i] != null ? " (" + f0.format(spec.pct[r][i]) + "th percentile)" : "") : "no rating"))));
  }

  window.RiverCharts = { line, bars, strip, DAY };
})();
