const startButton = document.getElementById("start-button");
const runBadge = document.getElementById("run-badge");
const runId = document.getElementById("run-id");
const runDate = document.getElementById("run-date");
const reportTimezone = document.getElementById("report-timezone");
const progressSummary = document.getElementById("progress-summary");
const progressPercent = document.getElementById("progress-percent");
const currentStepTitle = document.getElementById("current-step-title");
const currentStepStatus = document.getElementById("current-step-status");
const statusMessage = document.getElementById("status-message");
const healthSummary = document.getElementById("health-summary");
const artifactCount = document.getElementById("artifact-count");
const artifactCards = document.getElementById("artifact-cards");
const activityFeed = document.getElementById("activity-feed");
const stepsList = document.getElementById("steps");
const monitorView = document.getElementById("monitor-view");
const readerView = document.getElementById("reader-view");
const monitorModeButton = document.getElementById("monitor-mode-button");
const readerModeButton = document.getElementById("reader-mode-button");
const backToMonitor = document.getElementById("back-to-monitor");
const reportEmpty = document.getElementById("report-empty");
const reportContent = document.getElementById("report-content");
const reportOutline = document.getElementById("report-outline");
const copyReport = document.getElementById("copy-report");
const downloadReport = document.getElementById("download-report");

const countFields = {
  raw: document.getElementById("count-raw"),
  cleaned: document.getElementById("count-cleaned"),
  relevant: document.getElementById("count-relevant"),
  validated: document.getElementById("count-validated"),
};

let activeRunId = window.INITIAL_RUN_ID || null;
let pollTimer = null;
let currentStatus = null;
let currentMode = "monitor";
let reportMarkdown = "";
let loadedReportPath = null;
let autoOpenedReader = false;
let outlineObserver = null;

const statusLabels = {
  pending: "等待",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
  idle: "未运行",
};

const statusTone = {
  pending: "status-pending",
  running: "status-running",
  succeeded: "status-succeeded",
  failed: "status-failed",
  idle: "status-idle",
};

function fileUrl(path) {
  return `/files?path=${encodeURIComponent(path)}`;
}

function setMode(mode) {
  currentMode = mode;
  const isReader = mode === "reader";
  monitorView.classList.toggle("active", !isReader);
  readerView.classList.toggle("active", isReader);
  monitorModeButton.classList.toggle("active", !isReader);
  readerModeButton.classList.toggle("active", isReader);
}

function setBadge(status) {
  const normalized = status || "idle";
  runBadge.className = `status-badge ${statusTone[normalized] || "status-idle"}`;
  runBadge.textContent = statusLabels[normalized] || normalized;
}

function renderStatus(status) {
  const runStatus = status || {};
  currentStatus = runStatus;
  const statusName = runStatus.status || "idle";
  setBadge(statusName);

  runId.textContent = runStatus.run_id || "-";
  runDate.textContent = runStatus.run_date || "-";
  reportTimezone.textContent = runStatus.report_timezone || "-";

  const progress = runStatus.progress || {};
  progressSummary.textContent = `${progress.completed || 0}/${progress.total || 9}`;
  progressPercent.textContent = `${progress.percent || 0}%`;

  renderCounts(runStatus.counts || {});
  renderCurrentStep(runStatus);
  renderSteps(runStatus.steps || []);
  renderArtifacts(runStatus.artifact_cards || []);
  renderActivity(runStatus.activity || []);
  renderHealth(runStatus.health || {});
  updateReportLinks(runStatus);

  startButton.disabled = statusName === "pending" || statusName === "running";

  if (statusName === "succeeded" && runStatus.report_path) {
    loadReport(runStatus.report_path);
    if (!autoOpenedReader) {
      autoOpenedReader = true;
      setMode("reader");
    }
  }
}

function renderCounts(counts) {
  Object.entries(countFields).forEach(([name, element]) => {
    element.textContent = counts[name] ?? "-";
  });
}

function renderCurrentStep(runStatus) {
  const steps = runStatus.steps || [];
  const current = steps.find((step) => step.name === runStatus.current_step);
  const failed = steps.find((step) => step.status === "failed");
  const active = current || failed || steps.find((step) => step.status === "succeeded");

  if (runStatus.error) {
    currentStepTitle.textContent = `${runStatus.error.step_name || "unknown"} 失败`;
    currentStepStatus.textContent = "failed";
    currentStepStatus.className = "mini-badge failed";
    statusMessage.textContent = runStatus.error.message || "运行失败。";
    return;
  }

  if (runStatus.status === "succeeded") {
    currentStepTitle.textContent = "日报已生成";
    currentStepStatus.textContent = "succeeded";
    currentStepStatus.className = "mini-badge succeeded";
    statusMessage.textContent = "主链路完成，可以切换到阅读器查看日报。";
    return;
  }

  if (current) {
    currentStepTitle.textContent = `${current.label || current.name} · ${current.name}`;
    currentStepStatus.textContent = current.status || "pending";
    currentStepStatus.className = `mini-badge ${current.status || "pending"}`;
    statusMessage.textContent = current.message || `正在执行 ${current.name}。`;
    return;
  }

  currentStepTitle.textContent = active ? `${active.label || active.name} · ${active.name}` : "等待启动";
  currentStepStatus.textContent = runStatus.status || "idle";
  currentStepStatus.className = `mini-badge ${runStatus.status || "idle"}`;
  statusMessage.textContent = runStatus.status === "failed" ? "运行失败。" : "等待开始。";
}

function renderSteps(steps) {
  stepsList.replaceChildren();
  steps.forEach((step, index) => {
    const item = document.createElement("li");
    item.className = `stepper-item ${step.status || "pending"}`;

    const node = document.createElement("span");
    node.className = "step-node";
    node.textContent = String(index + 1);

    const text = document.createElement("span");
    text.className = "step-text";

    const label = document.createElement("strong");
    label.textContent = step.label || step.name;

    const name = document.createElement("small");
    name.textContent = step.name;

    text.append(label, name);
    item.append(node, text);
    stepsList.append(item);
  });
}

function renderArtifacts(cards) {
  artifactCards.replaceChildren();
  const available = cards.filter((card) => card.available);
  artifactCount.textContent = String(available.length);

  cards.forEach((card) => {
    const element = document.createElement(card.available ? "a" : "div");
    element.className = `artifact-card ${card.available ? "available" : "pending"}`;
    if (card.available) {
      element.href = fileUrl(card.path);
      element.target = "_blank";
      element.rel = "noreferrer";
    }

    const icon = document.createElement("span");
    icon.className = "artifact-icon";
    icon.textContent = artifactIcon(card.kind);

    const body = document.createElement("span");
    body.className = "artifact-body";

    const label = document.createElement("strong");
    label.textContent = card.label;

    const meta = document.createElement("small");
    meta.textContent = card.available
      ? `${card.kind} · ${card.filename || "已生成"}`
      : `${card.step_label || card.step_name} 后生成`;

    body.append(label, meta);
    element.append(icon, body);
    artifactCards.append(element);
  });
}

function artifactIcon(kind) {
  if (kind === "PNG") {
    return "IMG";
  }
  if (kind === "MD") {
    return "MD";
  }
  if (kind === "LOG") {
    return "LOG";
  }
  return "JSON";
}

function renderActivity(events) {
  activityFeed.replaceChildren();
  if (events.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-row";
    empty.textContent = "等待 trace 写入。";
    activityFeed.append(empty);
    return;
  }

  events.slice().reverse().forEach((event) => {
    const item = document.createElement("li");
    item.className = `activity-item ${event.level || "info"}`;

    const time = document.createElement("time");
    time.textContent = compactTime(event.timestamp);

    const message = document.createElement("p");
    message.textContent = event.message || "";

    item.append(time, message);
    activityFeed.append(item);
  });
}

function renderHealth(health) {
  if (!health.status) {
    healthSummary.textContent = "-";
    return;
  }
  const warnings = Array.isArray(health.warnings) ? health.warnings.length : 0;
  healthSummary.textContent = warnings > 0 ? `${health.status} · ${warnings} warning` : health.status;
}

function updateReportLinks(runStatus) {
  const reportPath = runStatus.report_path;
  if (!reportPath) {
    downloadReport.href = "#";
    downloadReport.classList.add("disabled-link");
    return;
  }
  downloadReport.href = fileUrl(reportPath);
  downloadReport.classList.remove("disabled-link");
}

async function loadReport(path) {
  if (!path || loadedReportPath === path) {
    return;
  }
  try {
    const response = await fetch(fileUrl(path));
    if (!response.ok) {
      throw new Error("报告读取失败");
    }
    reportMarkdown = await response.text();
    loadedReportPath = path;
    renderReport(reportMarkdown, path);
  } catch (error) {
    reportEmpty.classList.remove("hidden");
    reportEmpty.querySelector("h2").textContent = "报告读取失败";
    reportEmpty.querySelector("p").textContent = error.message || "无法读取 Markdown。";
  }
}

function renderReport(markdown, reportPath) {
  reportEmpty.classList.add("hidden");
  const rewritten = rewriteChartPaths(markdown, reportPath);
  const rawHtml = window.marked ? window.marked.parse(rewritten) : `<pre>${escapeHtml(rewritten)}</pre>`;
  reportContent.innerHTML = window.DOMPurify ? window.DOMPurify.sanitize(rawHtml) : rawHtml;
  buildOutline();
}

function rewriteChartPaths(markdown, reportPath) {
  const base = reportPath.split("/").slice(0, -1).join("/");
  return markdown.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, src) => {
    if (/^(https?:|data:|\/files)/i.test(src)) {
      return match;
    }
    const normalized = src.startsWith("/") ? src.slice(1) : src;
    const path = normalized.startsWith("charts/") && base
      ? `${base}/${normalized}`
      : normalized;
    return `![${alt}](${fileUrl(path)})`;
  });
}

function buildOutline() {
  reportOutline.replaceChildren();
  if (outlineObserver) {
    outlineObserver.disconnect();
    outlineObserver = null;
  }
  const headings = [...reportContent.querySelectorAll("h2, h3")].slice(0, 24);
  if (headings.length === 0) {
    const empty = document.createElement("span");
    empty.className = "empty-row";
    empty.textContent = "暂无目录。";
    reportOutline.append(empty);
    return;
  }

  headings.forEach((heading, index) => {
    const id = heading.id || `section-${index + 1}`;
    heading.id = id;
    const link = document.createElement("a");
    link.href = `#${id}`;
    link.className = heading.tagName === "H3" ? "sub" : "";
    link.textContent = heading.textContent || `章节 ${index + 1}`;
    reportOutline.append(link);
  });
  observeOutline(headings);
}

function observeOutline(headings) {
  const links = new Map(
    [...reportOutline.querySelectorAll("a")].map((link) => [link.hash.slice(1), link])
  );
  outlineObserver = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible.length === 0) {
        return;
      }
      const activeId = visible[0].target.id;
      links.forEach((link, id) => {
        link.classList.toggle("active", id === activeId);
      });
    },
    { rootMargin: "-15% 0px -70% 0px", threshold: 0.01 }
  );
  headings.forEach((heading) => outlineObserver.observe(heading));
}

async function startRun() {
  startButton.disabled = true;
  statusMessage.textContent = "正在启动 pipeline。";
  autoOpenedReader = false;
  setMode("monitor");
  try {
    const response = await fetch("/api/runs/start", { method: "POST" });
    if (!response.ok) {
      const payload = await response.json();
      const detail = payload.detail || {};
      if (response.status === 409 && detail.run_id) {
        activeRunId = detail.run_id;
        startPolling();
        return;
      }
      throw new Error(detail.message || "启动失败");
    }
    const payload = await response.json();
    activeRunId = payload.run_id;
    loadedReportPath = null;
    reportMarkdown = "";
    startPolling();
  } catch (error) {
    statusMessage.textContent = error.message || "启动失败";
    startButton.disabled = false;
  }
}

async function fetchStatus() {
  if (!activeRunId) {
    return;
  }
  const response = await fetch(`/api/runs/${encodeURIComponent(activeRunId)}/status`);
  if (!response.ok) {
    throw new Error("状态读取失败");
  }
  const status = await response.json();
  renderStatus(status);
  if (status.status === "succeeded" || status.status === "failed") {
    stopPolling();
  }
}

function startPolling() {
  stopPolling();
  fetchStatus().catch((error) => {
    statusMessage.textContent = error.message || "状态读取失败";
  });
  pollTimer = window.setInterval(() => {
    fetchStatus().catch((error) => {
      statusMessage.textContent = error.message || "状态读取失败";
    });
  }, 2000);
}

function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

function compactTime(value) {
  if (!value) {
    return "--:--:--";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value.slice(11, 19) || value;
  }
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

startButton.addEventListener("click", startRun);
monitorModeButton.addEventListener("click", () => setMode("monitor"));
readerModeButton.addEventListener("click", () => {
  if (currentStatus?.report_path) {
    loadReport(currentStatus.report_path);
  }
  setMode("reader");
});
backToMonitor.addEventListener("click", () => setMode("monitor"));
copyReport.addEventListener("click", async () => {
  if (!reportMarkdown) {
    return;
  }
  await navigator.clipboard.writeText(reportMarkdown);
  copyReport.textContent = "已复制";
  window.setTimeout(() => {
    copyReport.textContent = "复制全文";
  }, 1200);
});

if (window.INITIAL_STATUS) {
  renderStatus(window.INITIAL_STATUS);
  if (window.INITIAL_STATUS.status === "running") {
    startPolling();
  }
} else {
  renderStatus(null);
}
