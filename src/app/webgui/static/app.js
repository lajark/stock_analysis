/* 股票分析工具 — Liquid Glass 前端逻辑 */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const STAGE_LABELS = {
  validate_request: "校验输入",
  acquire_data: "获取数据",
  validate_evidence: "数据质量校验",
  build_evidence: "本地指标计算",
  generate_report: "生成分析报告",
  render_chart: "生成 K 线图",
  finish: "收尾",
};

const state = {
  boot: null,
  theme: localStorage.getItem("sa-theme") || "dark",
  fontSize: parseInt(localStorage.getItem("sa-font") || "16", 10),
  view: "markdown",          // 分析预览：markdown | text
  streamText: "",
  chartName: null,
  currentResult: null,       // {content, output_path, chart_name}
  es: null,
  running: false,
  progressTimer: null,
  strategy: null,            // 当前策略（含 source/parameters/defaults）
  btParams: {},              // 回测单次参数（name -> value）
  btAdopt: null,             // 最近一次可采用的 {ma_fast, ma_slow}
  updateUrl: null,           // 最新版安装包下载地址
};

/* ---------------- 通用 ---------------- */
async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `请求失败（${resp.status}）`);
  return data;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function renderMarkdown(md) {
  if (window.marked && typeof marked.parse === "function") {
    try {
      return marked.parse(md || "", { gfm: true, breaks: true });
    } catch (e) { /* fall through */ }
  }
  return "<pre>" + escapeHtml(md || "") + "</pre>";
}

function setStatus(el, text, kind) {
  el.textContent = text;
  el.className = "status" + (kind ? " " + kind : "");
}

function fmtPct(v) { return (v * 100).toFixed(2) + "%"; }

/* ---------------- 初始化 ---------------- */
async function init() {
  document.documentElement.dataset.theme = state.theme;
  syncThemeButtons();
  $("#font-label").textContent = state.fontSize + "px";
  document.documentElement.style.setProperty("--doc-font-size", state.fontSize + "px");

  state.boot = await api("/api/bootstrap");
  $("#brand-version").textContent = "v" + state.boot.version + " · 本地分析 · 手动触发";

  renderModes();
  renderStrategyControls();
  renderSettings();
  renderHelp();

  bindEvents();
  openEvents();
}

function renderModes() {
  const select = $("#mode");
  select.innerHTML = "";
  state.boot.modes.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.key;
    opt.textContent = m.desc + (m.deep ? "（深度）" : "");
    select.appendChild(opt);
  });
  select.value = "quick";
}

/* ---------------- 主题与字号 ---------------- */
function syncThemeButtons() {
  $$(".seg-btn[data-theme-set]").forEach((b) => {
    b.classList.toggle("active", b.dataset.themeSet === state.theme);
  });
}
function setTheme(theme) {
  state.theme = theme;
  localStorage.setItem("sa-theme", theme);
  document.documentElement.dataset.theme = theme;
  syncThemeButtons();
}
function setFont(delta) {
  state.fontSize = Math.min(26, Math.max(11, state.fontSize + delta));
  localStorage.setItem("sa-font", String(state.fontSize));
  $("#font-label").textContent = state.fontSize + "px";
  document.documentElement.style.setProperty("--doc-font-size", state.fontSize + "px");
}

/* ---------------- 事件绑定 ---------------- */
function bindEvents() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
  $$(".seg-btn[data-theme-set]").forEach((b) =>
    b.addEventListener("click", () => setTheme(b.dataset.themeSet))
  );
  $("#font-minus").addEventListener("click", () => setFont(-1));
  $("#font-plus").addEventListener("click", () => setFont(1));

  $("#analyze-btn").addEventListener("click", startAnalysis);
  $("#cancel-btn").addEventListener("click", cancelAnalysis);
  $("#toggle-view").addEventListener("click", toggleView);
  $("#open-file").addEventListener("click", openResultFile);
  $("#open-dir").addEventListener("click", () => api("/api/open_dir", { method: "POST" }));

  $("#bt-run").addEventListener("click", () => runBacktest(false));
  $("#bt-optimize").addEventListener("click", () => runBacktest(true));
  $("#adopt-btn").addEventListener("click", adoptParams);

  $("#save-settings").addEventListener("click", saveSettings);
  $("#set-show-secret").addEventListener("change", toggleSecrets);
  $("#strategy-save").addEventListener("click", saveStrategy);
  $("#strategy-reset").addEventListener("click", resetStrategy);
  $("#check-update").addEventListener("click", checkUpdate);
  $("#install-update").addEventListener("click", installUpdate);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.running) cancelAnalysis();
  });
}

function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".page").forEach((p) => p.classList.remove("active"));
  $("#page-" + name).classList.add("active");
}

/* ---------------- SSE 事件流 ---------------- */
function openEvents() {
  if (state.es) state.es.close();
  state.es = new EventSource("/api/events");
  state.es.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    handleEvent(msg);
  };
  state.es.onerror = () => { /* EventSource 自动重连 */ };
}

function handleEvent(msg) {
  switch (msg.type) {
    case "progress":
      setStatus($("#status-line"), msg.payload);
      break;
    case "stage":
      updateStage(msg);
      break;
    case "token":
      state.streamText += msg.payload;
      $("#preview-raw").textContent = state.streamText;
      break;
    case "finished":
      onAnalysisFinished(msg);
      break;
    case "cancelled":
      onAnalysisDone("分析已取消：未生成报告", "error");
      break;
    case "failed":
      onAnalysisDone("分析失败：" + msg.payload, "error");
      break;
    case "batch_done":
      onBatchDone(msg);
      break;
    case "bt_result":
      onBacktestResult(msg);
      break;
    case "bt_error":
      setBtBusy(false);
      setStatus($("#bt-status"), msg.payload, "error");
      break;
    case "update_progress":
      onUpdateProgress(msg);
      break;
    case "update_done":
      onUpdateDone(msg);
      break;
    case "update_error":
      onUpdateError(msg);
      break;
  }
}

function updateStage(msg) {
  const idx = state.boot.stages.indexOf(msg.stage);
  const num = idx >= 0 ? idx + 1 : state.boot.stages.length;
  const label = STAGE_LABELS[msg.stage] || msg.stage;
  const mark = msg.status === "done" ? "✓" : "○";
  $("#stage-line").textContent = `${mark} 阶段 ${num}/${state.boot.stages.length}：${label}`;
}

/* ---------------- 分析 ---------------- */
function startProgress() {
  let p = 0;
  clearInterval(state.progressTimer);
  state.progressTimer = setInterval(() => {
    p = Math.min(90, p + Math.random() * 9);
    $("#progress-bar").style.width = p + "%";
  }, 320);
  $("#progress-wrap").hidden = false;
}
function stopProgress() {
  clearInterval(state.progressTimer);
  $("#progress-bar").style.width = "100%";
  setTimeout(() => {
    $("#progress-wrap").hidden = true;
    $("#progress-bar").style.width = "0%";
  }, 500);
}

async function startAnalysis() {
  const tickers = $("#ticker").value
    .split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
  if (!tickers.length) { setStatus($("#status-line"), "请输入股票代码", "error"); return; }

  state.streamText = "";
  state.currentResult = null;
  $("#preview-raw").textContent = "";
  $("#preview-md").innerHTML = "";
  $("#chart-frame").src = "about:blank";
  $("#chart-pane").hidden = true;
  $("#preview-split").classList.remove("hidden");
  $("#preview-split").classList.add("single");
  $("#preview-text").classList.add("hidden");
  $("#preview-empty").hidden = true;
  state.view = "markdown";
  $("#open-file").disabled = true;

  setRunning(true);
  startProgress();
  setStatus($("#status-line"), "正在准备分析…");
  $("#stage-line").textContent = "";
  try {
    await api("/api/analyze", {
      method: "POST",
      body: JSON.stringify({
        tickers,
        mode: $("#mode").value,
        use_llm: $("#use-llm").checked,
        chart: $("#chart").checked,
      }),
    });
  } catch (err) {
    setRunning(false);
    stopProgress();
    setStatus($("#status-line"), err.message, "error");
  }
}

function setRunning(running) {
  state.running = running;
  $("#analyze-btn").disabled = running;
  $("#cancel-btn").disabled = !running;
  $("#bt-run").disabled = running;
  $("#bt-optimize").disabled = running;
}

async function cancelAnalysis() {
  await api("/api/cancel", { method: "POST" });
  setStatus($("#status-line"), "正在取消…");
}

function onAnalysisFinished(msg) {
  setRunning(false);
  stopProgress();
  $("#stage-line").textContent = "";
  state.currentResult = msg;
  state.streamText = msg.content;

  $("#preview-raw").textContent = msg.content;
  $("#preview-md").innerHTML = renderMarkdown(msg.content);
  state.chartName = msg.chart_name || null;
  if (msg.chart_name) {
    $("#chart-pane").hidden = false;
    $("#chart-frame").src = "/api/chart?path=" + encodeURIComponent(msg.chart_name);
    $("#preview-split").classList.remove("hidden");
    $("#preview-split").classList.remove("single");
    $("#preview-text").classList.add("hidden");
  } else {
    $("#chart-pane").hidden = true;
    $("#preview-split").classList.add("single");
  }
  showPreviewView("markdown");
  $("#open-file").disabled = false;
  setStatus($("#status-line"), `完成：${msg.name}（${msg.ticker}），耗时 ${msg.elapsed} 秒`, "ok");
}

function onBatchDone(msg) {
  setRunning(false);
  stopProgress();
  $("#stage-line").textContent = "";
  const ok = msg.items.filter((i) => i.status === "ok").length;
  const text = msg.items
    .map((i) => (i.status === "ok"
      ? `✓ ${i.ticker} ${i.name} — ${i.elapsed}s\n    ${i.output_path}`
      : `✗ ${i.ticker} — ${i.error || "未知错误"}`))
    .join("\n");
  $("#preview-raw").textContent = "批量分析结果\n" + text;
  $("#preview-md").innerHTML = renderMarkdown("# 批量分析结果\n```\n" + text + "\n```");
  state.chartName = null;
  $("#chart-pane").hidden = true;
  $("#preview-split").classList.add("single");
  showPreviewView("markdown");
  $("#open-file").disabled = true;
  setStatus($("#status-line"),
    msg.cancelled ? `批量已取消：已分析 ${ok}/${msg.items.length} 只` : `批量完成：${ok}/${msg.items.length} 只成功`,
    msg.cancelled ? "error" : "ok");
}

function onAnalysisDone(text, kind) {
  setRunning(false);
  stopProgress();
  $("#stage-line").textContent = "";
  setStatus($("#status-line"), text, kind);
}

function toggleView() {
  showPreviewView(state.view === "markdown" ? "text" : "markdown");
}

function showPreviewView(view) {
  state.view = view;
  const split = $("#preview-split");
  const text = $("#preview-text");
  const empty = $("#preview-empty");
  if (!state.streamText) {
    split.classList.add("hidden");
    text.classList.add("hidden");
    empty.hidden = false;
    $("#toggle-view").textContent = "渲染视图";
    return;
  }
  empty.hidden = true;
  if (view === "markdown") {
    split.classList.remove("hidden");
    text.classList.add("hidden");
    $("#toggle-view").textContent = "纯文本";
  } else {
    split.classList.add("hidden");
    text.classList.remove("hidden");
    $("#toggle-view").textContent = "渲染视图";
  }
}

async function openResultFile() {
  if (!state.currentResult) return;
  try {
    await api("/api/open_file", {
      method: "POST",
      body: JSON.stringify({ path: state.currentResult.output_path }),
    });
  } catch (err) {
    setStatus($("#status-line"), err.message, "error");
  }
}

/* ---------------- 回测 / 优化 ---------------- */
function renderStrategyControls() {
  const strategy = state.boot.strategy;
  state.strategy = strategy;
  const badge = $("#strategy-badge");
  badge.textContent =
    (strategy.source === "user" ? "用户策略 · " : "内置策略 · ") +
    strategy.name + " — " + strategy.description;
  badge.classList.toggle("accent", strategy.source === "user");

  // 单次回测参数（内置为 ma_fast/ma_slow；用户策略用其声明的参数）
  const paramsBox = $("#bt-params");
  paramsBox.innerHTML = "";
  state.btParams = {};
  const paramNames = strategy.parameters && strategy.parameters.length
    ? strategy.parameters
    : ["ma_fast", "ma_slow"];
  paramNames.forEach((name) => {
    const def = (strategy.defaults && strategy.defaults[name]) || 20;
    state.btParams[name] = def;
    const wrap = document.createElement("div");
    wrap.className = "field";
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = name === "ma_fast" ? "快均线周期（" + name + "）"
      : name === "ma_slow" ? "慢均线周期（" + name + "）"
      : "参数 " + name;
    const input = document.createElement("input");
    input.className = "input";
    input.value = def;
    input.addEventListener("change", () => {
      const v = parseInt(input.value, 10);
      state.btParams[name] = Number.isFinite(v) && v > 0 ? v : def;
      input.value = state.btParams[name];
    });
    wrap.appendChild(label);
    wrap.appendChild(input);
    paramsBox.appendChild(wrap);
  });

  // 优化网格
  const gridsBox = $("#bt-grids");
  gridsBox.innerHTML = "";
  paramNames.forEach((name) => {
    const def = (strategy.defaults && strategy.defaults[name]) || 20;
    const wrap = document.createElement("div");
    wrap.className = "field";
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = name;
    const input = document.createElement("input");
    input.className = "input";
    input.dataset.gridFor = name;
    input.value = name === "ma_fast" ? "5,10,20" : name === "ma_slow" ? "30,60,120" : String(def);
    wrap.appendChild(label);
    wrap.appendChild(input);
    gridsBox.appendChild(wrap);
  });

  $("#adopt-btn").hidden = strategy.source !== "builtin";
}

function buildGrids() {
  const grids = {};
  $$("#bt-grids .input").forEach((input) => {
    const name = input.dataset.gridFor;
    const values = input.value
      .split(",").map((s) => parseInt(s.trim(), 10))
      .filter((v) => Number.isFinite(v) && v > 0);
    if (values.length) grids[name] = values;
  });
  return grids;
}

function setBtBusy(busy) {
  $("#bt-run").disabled = busy || state.running;
  $("#bt-optimize").disabled = busy || state.running;
  $("#adopt-btn").disabled = busy;
}

async function runBacktest(optimize) {
  const ticker = $("#bt-ticker").value.trim();
  const start = $("#bt-start").value.trim();
  const end = $("#bt-end").value.trim();
  if (!ticker) { setStatus($("#bt-status"), "请输入股票代码", "error"); return; }
  setBtBusy(true);
  setStatus($("#bt-status"), optimize ? "正在优化…" : "正在取数…");
  $("#bt-empty").hidden = true;
  try {
    if (optimize) {
      await api("/api/optimize", {
        method: "POST",
        body: JSON.stringify({
          ticker, start, end,
          objective: $("#bt-objective").value,
          grids: buildGrids(),
        }),
      });
    } else {
      await api("/api/backtest", {
        method: "POST",
        body: JSON.stringify({
          ticker, start, end,
          ma_fast: state.btParams.ma_fast || 20,
          ma_slow: state.btParams.ma_slow || 60,
        }),
      });
    }
  } catch (err) {
    setBtBusy(false);
    setStatus($("#bt-status"), err.message, "error");
  }
}

function onBacktestResult(msg) {
  setBtBusy(false);
  $("#bt-empty").hidden = true;
  $("#bt-result").innerHTML = renderMarkdown(msg.text);
  $("#bt-result").style.display = "";
  setStatus($("#bt-status"), "完成", "ok");

  const adopt = $("#adopt-btn");
  if (msg.strategy && msg.strategy.source === "builtin") {
    let maFast = null, maSlow = null;
    if (msg.kind === "optimize") {
      const sel = msg.result.selected_parameters || {};
      maFast = sel.ma_fast; maSlow = sel.ma_slow;
    } else {
      maFast = state.btParams.ma_fast;
      maSlow = state.btParams.ma_slow;
    }
    if (Number.isFinite(maFast) && Number.isFinite(maSlow)) {
      state.btAdopt = { ma_fast: maFast, ma_slow: maSlow };
      adopt.hidden = false;
    } else {
      adopt.hidden = true;
    }
  } else {
    adopt.hidden = true;
  }
}

async function adoptParams() {
  if (!state.btAdopt) return;
  try {
    await api("/api/adopt", {
      method: "POST",
      body: JSON.stringify(state.btAdopt),
    });
    state.boot = await api("/api/bootstrap");
    $("#use-params").checked = true;
    $("#set-use-params").checked = true;
    setStatus($("#bt-status"), "已采用建议参数（MA " +
      state.btAdopt.ma_fast + "/" + state.btAdopt.ma_slow + "），分析页可勾选生效", "ok");
  } catch (err) {
    setStatus($("#bt-status"), err.message, "error");
  }
}

/* ---------------- 设置 ---------------- */
function renderSettings() {
  const s = state.boot.settings;
  $("#set-tushare").value = s.TUSHARE_TOKEN || "";
  $("#set-llm-key").value = s.LLM_API_KEY || "";
  $("#set-base-url").value = s.LLM_BASE_URL || "";
  $("#set-model").value = s.LLM_MODEL || "";
  $("#set-deep-model").value = s.LLM_MODEL_DEEP || "";
  $("#set-mairui").value = s.MAIRUI_LICENCE || "";
  $("#set-biying").value = s.BIYINGAPI_APPCODE || "";
  $("#set-ma-periods").value = s.ANALYSIS_MA_PERIODS || "";
  const enabled = ["1", "true", "yes", "on"].includes(String(s.USE_ANALYSIS_MA_OVERRIDE).toLowerCase());
  $("#set-use-params").checked = enabled;
  $("#use-params").checked = enabled;
  $("#use-params").disabled = !s.ANALYSIS_MA_PERIODS;
  $("#settings-status").textContent = "当前 MA 周期：" + state.boot.effective_ma_periods.join(", ");
}

function toggleSecrets() {
  const show = $("#set-show-secret").checked;
  ["set-tushare", "set-llm-key", "set-mairui", "set-biying"].forEach((id) => {
    $(`#${id}`).type = show ? "text" : "password";
  });
}

async function saveSettings() {
  try {
    const res = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        tushare_token: $("#set-tushare").value,
        llm_api_key: $("#set-llm-key").value,
        llm_base_url: $("#set-base-url").value,
        llm_model: $("#set-model").value,
        llm_model_deep: $("#set-deep-model").value,
        mairui_licence: $("#set-mairui").value,
        biyingapi_appcode: $("#set-biying").value,
        analysis_ma_periods: $("#set-ma-periods").value,
        use_analysis_ma_override: $("#set-use-params").checked ? "1" : "0",
      }),
    });
    state.boot = await api("/api/bootstrap");
    renderSettings();
    $("#settings-status").textContent = "已保存 · 当前 MA 周期：" + res.effective_ma_periods.join(", ");
  } catch (err) {
    $("#settings-status").textContent = "保存失败：" + err.message;
  }
}

/* ---------------- 策略编辑 ---------------- */
async function loadStrategyEditor() {
  const data = await api("/api/strategy");
  const s = data.strategy;
  const sourceLabel = s.source === "user" ? "用户策略" : "内置策略（默认）";
  $("#strategy-info").innerHTML =
    `<b>${sourceLabel}</b> · ${s.name} — ${s.description}<br>` +
    `文件：${escapeHtml(data.user_file || "（内置，无用户文件）")}<br>` +
    `格式：Python 模块（非 YAML）——信号计算需要代码；参数在 DEFAULTS / PARAMETERS 中声明。`;
  $("#strategy-src").value = data.source;
}

async function saveStrategy() {
  try {
    const res = await api("/api/strategy", {
      method: "POST",
      body: JSON.stringify({ action: "save", source_code: $("#strategy-src").value }),
    });
    state.boot = await api("/api/bootstrap");
    renderStrategyControls();
    await loadStrategyEditor();
    $("#strategy-status").textContent = "策略已保存并生效：" + res.strategy.name;
  } catch (err) {
    $("#strategy-status").textContent = "保存失败：" + err.message;
  }
}

async function resetStrategy() {
  if (!confirm("恢复内置策略？用户自定义文件将被删除。")) return;
  try {
    await api("/api/strategy", {
      method: "POST",
      body: JSON.stringify({ action: "reset" }),
    });
    state.boot = await api("/api/bootstrap");
    renderStrategyControls();
    await loadStrategyEditor();
    $("#strategy-status").textContent = "已恢复内置策略";
  } catch (err) {
    $("#strategy-status").textContent = "操作失败：" + err.message;
  }
}

/* ---------------- 帮助 ---------------- */
async function renderHelp() {
  const data = await api("/api/help");
  $("#help-guide").innerHTML = renderMarkdown(data.guide);
}

async function checkUpdate() {
  try {
    const data = await api("/api/check_update");
    const lines = ["## 检查更新结果", `- 当前版本：**${data.local}**`, ""];
    data.results.forEach((r) => lines.push("- " + r));
    const hasNew = data.results.some((r) => r.includes("发现新版本"));
    state.updateUrl = hasNew ? data.download_url || null : null;
    if (hasNew && state.updateUrl) {
      lines.push("");
      lines.push(`安装包：[${decodeURIComponent(state.updateUrl.split("/").pop())}](${state.updateUrl})`);
      $("#install-update").hidden = false;
    } else {
      $("#install-update").hidden = true;
    }
    $("#update-md").innerHTML = renderMarkdown(lines.join("\n"));
    $("#update-result").classList.remove("hidden");
    $("#update-status").textContent = hasNew ? "发现新版本：可点击「下载并安装更新」" : "";
  } catch (err) {
    $("#update-md").innerHTML = renderMarkdown("## 检查更新结果\n\n检查失败：" + escapeHtml(err.message));
    $("#update-result").classList.remove("hidden");
    $("#install-update").hidden = true;
  }
}

async function installUpdate() {
  if (!state.updateUrl) return;
  if (!confirm("将下载并静默安装新版本。更新仅覆盖程序目录，您的设置/缓存/策略等用户数据不受影响。\n安装完成后请重新打开应用。继续？")) return;
  $("#install-update").disabled = true;
  $("#update-status").textContent = "正在下载安装包…（下载完成后会自动安装并退出）";
  try {
    await api("/api/update/install", { method: "POST", body: JSON.stringify({ url: state.updateUrl }) });
  } catch (err) {
    $("#update-status").textContent = "启动更新失败：" + err.message;
    $("#install-update").disabled = false;
  }
}

function onUpdateProgress(msg) {
  const total = msg.total || 0;
  const mb = (n) => (n / 1048576).toFixed(1) + " MB";
  $("#update-status").textContent = total
    ? `正在下载安装包… ${mb(msg.received)} / ${mb(total)}`
    : `正在下载安装包… ${mb(msg.received)}`;
}

function onUpdateDone() {
  $("#update-status").textContent = "下载完成，正在静默安装… 应用即将退出，请稍后重新打开。";
  setTimeout(() => window.close(), 1200);
}

function onUpdateError(msg) {
  $("#update-status").textContent = "更新失败：" + msg.payload;
  $("#install-update").disabled = false;
}

/* ---------------- 启动 ---------------- */
document.addEventListener("DOMContentLoaded", () => {
  init().catch((err) => {
    setStatus($("#status-line"), "初始化失败：" + err.message, "error");
  });
});
