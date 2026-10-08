"use strict";
const $ = (id) => document.getElementById(id);
const kinds = {
  note: "心得",
  plan: "交易计划",
  trade: "成交记录",
  position: "持仓记录",
  learning: "学习笔记",
  review: "交易复盘",
  analysis: "分析结论",
  research: "研究记录",
  raw: "我的原话",
  profile: "目标与背景",
  hypothesis: "候选认识",
  evolution: "认识变化",
  strategy: "已确认纪律",
  proposal: "规则提案",
  agent_run: "处理留痕",
  agent_result: "研究汇总",
  source: "外部来源",
  market_snapshot: "行情资料",
  emotion: "情绪记录",
  observation: "观察记录",
};
const groupKinds = {
  all: [
    "note",
    "learning",
    "plan",
    "trade",
    "position",
    "review",
    "analysis",
    "research",
    "hypothesis",
    "evolution",
    "profile",
    "emotion",
    "observation",
    "proposal",
    "strategy",
  ],
  practice: ["plan", "trade", "position", "review", "emotion", "observation"],
  knowledge: [
    "note",
    "learning",
    "hypothesis",
    "evolution",
    "profile",
    "proposal",
    "strategy",
  ],
  research: ["research", "analysis"],
  originals: ["raw", "source", "market_snapshot", "agent_run", "agent_result"],
};
const groupDescriptions = {
  all: "个人经历、学习与研究",
  practice: "计划、成交、持仓与复盘",
  knowledge: "心得、学习与待验证的认识",
  research: "研究过程与分析结论",
  originals: "原话、外部来源、行情与处理留痕",
};
const layerInfo = {
  dao: ["道", "方向与认识", "为什么交易，怎样取舍"],
  fa: ["法", "规则与计划", "用什么条件约束行动"],
  shu: ["术", "方法与实践", "怎样判断，如何执行"],
  qi: ["器", "资料与工具", "判断所需的证据与资源"],
};
const stateNames = {
  source: {
    user_report: "来自个人原话",
    original_checked: "原始出处已核对",
    attributed_retelling: "有出处的转述",
    publisher_description: "出版介绍",
    unverified: "出处待核对",
  },
  adoption: {
    pending: "待形成认同",
    agreed: "个人已认同",
    questioned: "仍有疑问",
    rejected: "个人不认同",
  },
  evidence: {
    unverified: "效果待验证",
    observing: "实践观察中",
    limited_support: "已有有限支持",
    contradicted: "存在反例",
    needs_review: "需要复查",
  },
  rule: {
    not_applicable: "知识与记录",
    draft: "待确认提案",
    active: "已确认生效",
  },
};
const fieldNames = {
  quantity: "持仓数量",
  cost_price: "成本价格",
  position_fraction: "持仓比例",
  position_level_reported: "自述仓位",
  account_equity: "账户总资产",
  original_horizon: "原持有周期",
  capital: "资金情况",
  market: "交易市场",
  risk_limits: "风险限制",
  scope: "适用范围",
  author: "作者",
  source_type: "来源类型",
  time_scope: "时间范围",
  original_claims: "原始观点",
  assistant_supplements: "补充理解",
  validation_candidate: "待验证内容",
  needs_operational_definition: "待明确的定义",
  verification_needed: "需要核实",
  status: "记录状态",
  execution_status: "执行状态",
  entry_condition: "入场条件",
  exit_condition: "退出条件",
  invalid_condition: "失效条件",
  price: "价格",
  security_id: "证券代码",
  reason: "依据",
  rationale: "理由",
  summary: "要点",
  unknowns: "尚不清楚",
  counter_evidence: "反面证据",
  evidence_refs: "证据引用",
  support: "支持依据",
  oppose: "反对依据",
  effective_at: "生效时间",
  verified: "已核实",
  text: "内容",
};
let session = uuid(),
  busy = false,
  activeModel = "",
  currentView = "chat",
  currentGroup = "all",
  libraryPage = 1,
  libraryTotal = 0,
  reviewOnly = false,
  libraryTab = "personal",
  libraryVersion = 0,
  classicVersion = 0,
  overviewVersion = 0,
  detailVersion = 0,
  sessionVersion = 0,
  currentDetail = null,
  systemData = null,
  currentLayer = "dao",
  currentLayerPage = 1,
  toastTimer,
  searchTimer,
  draftTimer;
function uuid() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 15) | 64;
  b[8] = (b[8] & 63) | 128;
  const h = Array.from(b, (n) => n.toString(16).padStart(2, "0")).join("");
  return [
    h.slice(0, 8),
    h.slice(8, 12),
    h.slice(12, 16),
    h.slice(16, 20),
    h.slice(20),
  ].join("-");
}
function el(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}
function text(node, value) {
  node.textContent = value ?? "";
}
function toast(value) {
  clearTimeout(toastTimer);
  text($("toast"), value);
  $("toast").hidden = false;
  toastTimer = setTimeout(() => ($("toast").hidden = true), 3300);
}
async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      ...options,
      headers: {
        ...(options.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new Error("暂时连接不上，请检查网络后重试。");
  }
  if (!response.ok) {
    if (response.status === 401 && url !== "/api/login") showLogin();
    let error;
    try {
      error = await response.json();
    } catch {
      error = { detail: "连接失败，请稍后再试。" };
    }
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : "这次请求未能完成，请重试。",
    );
  }
  return response;
}
async function data(url, options) {
  return (await api(url, options)).json();
}
let modelProfiles = [];
const attachments = new ChatAttachments({
  $,
  el,
  icon,
  toast,
  data,
  onText: (value) => {
    const combined = [$("input").value.trim(), value]
      .filter(Boolean)
      .join("\n");
    $("input").value = combined.slice(0, 16000);
    if (combined.length > 16000)
      toast("输入文字已达到上限，录音完整转写仍可在附件中查看。");
    resizeInput();
    saveDraft();
    syncSend();
  },
  onChange: () => syncSend(),
});
function syncSend() {
  $("send").disabled =
    busy ||
    attachments.pending() ||
    (!$("input").value.trim() && !attachments.ready().length);
}
function showLogin() {
  setSidebar(false);
  $("login").hidden = false;
  $("workspace").hidden = true;
}
function date(value, full = false) {
  if (!value) return "时间未提供";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(0, full ? 16 : 10);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(full ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(parsed);
}
function cleanText(value) {
  return String(value || "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/[*`_]/g, "")
    .trim();
}
function recordText(row) {
  const b = row.body || {};
  if (b.text || b.summary) return b.text || b.summary;
  if (row.text) return row.text;
  if (row.kind === "market_snapshot")
    return [
      b.security_id,
      b.interval === "day" ? "日线行情" : b.interval,
      b.data_as_of ? "数据时间 " + date(b.data_as_of, true) : "",
      b.source ? "来源 " + b.source : "",
    ]
      .filter(Boolean)
      .join(" · ");
  if (row.kind === "agent_run")
    return b.request?.text || "一次资料整理与研究的过程留痕。";
  if (b.rules) return b.rules.map((r) => r.text).join("\n");
  return "查看完整资料与关联来源。";
}
function titleOf(row) {
  const b = row.body || {};
  if (b.title) return b.title;
  const value = cleanText(recordText(row));
  const first = (
    value.split("\n").find((x) => x.trim()) ||
    kinds[row.kind] ||
    "资料"
  ).replace(/^[-•]\s*/, "");
  const named = first.match(/《([^》]+)》/);
  const title = named?.[1] || first.split(/[。！？]/)[0];
  return title.slice(0, 48) + (title.length > 48 ? "…" : "");
}
function summaryOf(row) {
  const b = row.body || {},
    value = cleanText(recordText(row));
  if (b.summary && b.title) return cleanText(b.summary);
  const lines = value.split("\n").filter(Boolean);
  if (lines.length > 1) return lines.slice(1).join(" ").slice(0, 300);
  const sentences = value.split("。").filter(Boolean);
  if (sentences.length > 1) return sentences.slice(1).join("。").slice(0, 300);
  if (value.length > 80) return value.slice(0, 300);
  const parts = [
    ...(b.tags || []).slice(0, 3),
    b.excerpt && b.excerpt !== b.text ? "原话：" + b.excerpt : "",
  ];
  return (
    parts.filter(Boolean).join(" · ") ||
    "保留完整内容、资料状态与可追溯的关联。"
  );
}
function recordState(row) {
  if (row.status === "needs_recheck" || row.states?.evidence === "needs_review")
    return ["待复查", "amber"];
  if (row.states?.rule === "active") return ["已确认纪律", "green"];
  if (row.kind === "proposal") return ["待确认提案", "amber"];
  if (row.kind === "hypothesis") return ["待验证认识", "amber"];
  if (row.kind === "learning" || row.kind === "note") {
    const a = row.states?.adoption;
    return [
      a === "agreed"
        ? "个人已认同"
        : a === "questioned"
          ? "仍有疑问"
          : a === "rejected"
            ? "个人不认同"
            : "待吸收",
      "",
    ];
  }
  if (["analysis", "research", "agent_result"].includes(row.kind))
    return ["研究资料", ""];
  if (row.kind === "raw") return ["保留原话", ""];
  if (row.kind === "source") return ["外部资料", ""];
  if (row.kind === "position") return ["来自个人记录", ""];
  return [kinds[row.kind] || "已记录", ""];
}
/* Small safe Markdown renderer; all user text becomes text nodes. */
function inline(node, value) {
  const pattern = /(!?\[[^\]]*\]\([^\s)]+\)|\*\*[^*]+\*\*|`[^`]+`)/g;
  for (const part of String(value || "").split(pattern)) {
    if (part.startsWith("**") && part.endsWith("**")) {
      node.append(el("strong", "", part.slice(2, -2)));
      continue;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      node.append(el("code", "", part.slice(1, -1)));
      continue;
    }
    const m = part.match(/^(!?)\[([^\]]*)\]\(([^)]+)\)$/);
    if (m && /^(https?:\/\/|\/api\/)/.test(m[3])) {
      if (
        m[1] &&
        /^\/api\/charts\/market_snapshot_[a-zA-Z0-9_]+\.svg$/.test(m[3])
      ) {
        const img = el("img");
        img.src = m[3];
        img.alt = m[2];
        img.loading = "lazy";
        node.append(img);
      } else if (!m[1]) {
        const a = el("a", "", m[2]);
        a.href = m[3];
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        const record = m[3].match(/^\/api\/records\/([a-zA-Z0-9_]+)$/);
        if (record)
          a.onclick = (e) => {
            e.preventDefault();
            detail(record[1]);
          };
        node.append(a);
      } else node.append(document.createTextNode(part));
    } else node.append(document.createTextNode(part));
  }
}
function renderBody(node, value) {
  node.replaceChildren();
  node.classList.add("markdown");
  const lines = String(value || "")
    .replace(/\r\n/g, "\n")
    .split("\n");
  let i = 0;
  const special = (line) =>
    /^(#{1,6}\s|\s*[-*+]\s|\s*\d+[.)]\s|>\s?|```|---+$)/.test(line);
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.startsWith("```")) {
      const pre = el("pre"),
        code = el("code");
      let content = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```"))
        content.push(lines[i++]);
      text(code, content.join("\n"));
      pre.append(code);
      node.append(pre);
      i++;
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)/);
    if (heading) {
      const h = el("h" + Math.min(4, Math.max(2, heading[1].length)));
      inline(h, heading[2]);
      node.append(h);
      i++;
      continue;
    }
    if (/^---+$/.test(line.trim())) {
      node.append(el("hr"));
      i++;
      continue;
    }
    if (
      line.includes("|") &&
      i + 1 < lines.length &&
      /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])
    ) {
      const wrap = el("div", "table-wrap"),
        table = el("table"),
        thead = el("thead"),
        tbody = el("tbody");
      const cells = (l) =>
        l
          .trim()
          .replace(/^\||\|$/g, "")
          .split("|")
          .map((x) => x.trim());
      const tr = el("tr");
      cells(line).forEach((c) => {
        const th = el("th");
        inline(th, c);
        tr.append(th);
      });
      thead.append(tr);
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        const r = el("tr");
        cells(lines[i++]).forEach((c) => {
          const td = el("td");
          inline(td, c);
          r.append(td);
        });
        tbody.append(r);
      }
      table.append(thead, tbody);
      wrap.append(table);
      node.append(wrap);
      continue;
    }
    if (/^>\s?/.test(line)) {
      const q = el("blockquote");
      let items = [];
      while (i < lines.length && /^>\s?/.test(lines[i]))
        items.push(lines[i++].replace(/^>\s?/, ""));
      inline(q, items.join("\n"));
      node.append(q);
      continue;
    }
    if (/^\s*([-*+]\s|\d+[.)]\s)/.test(line)) {
      const ordered = /^\s*\d+[.)]/.test(line),
        list = el(ordered ? "ol" : "ul"),
        re = ordered ? /^\s*\d+[.)]\s/ : /^\s*[-*+]\s/;
      while (i < lines.length && re.test(lines[i])) {
        const item = el("li");
        inline(item, lines[i++].replace(re, ""));
        list.append(item);
      }
      node.append(list);
      continue;
    }
    const p = el("p");
    let parts = [line];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !special(lines[i]) &&
      !(lines[i].includes("|") && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || ""))
    )
      parts.push(lines[i++]);
    inline(p, parts.join("\n"));
    node.append(p);
  }
}
function welcome() {
  const existing = $("welcome");
  if (existing) return;
  $("messages").replaceChildren(welcomeTemplate.cloneNode(true));
  bindSuggestions();
}
const welcomeTemplate = $("welcome").cloneNode(true);
function bindSuggestions() {
  document
    .querySelectorAll(".suggestions button")
    .forEach((b) => (b.onclick = () => startPrompt(b.dataset.prompt)));
  $("browse-library")?.addEventListener("click", () => view("records"));
}
function startPrompt(value) {
  view("chat");
  $("input").value = value;
  resizeInput();
  saveDraft();
  syncSend();
  $("input").focus();
}
function scrollBottom(force = false) {
  const box = $("messages");
  if (force || box.scrollHeight - box.scrollTop - box.clientHeight < 180)
    box.scrollTop = box.scrollHeight;
}
function message(role, value, model = "", files = []) {
  if ($("messages").querySelector(".welcome")) $("messages").replaceChildren();
  const node = el("article", "message " + role),
    head = el("div", "message-head"),
    avatar = el("span", "message-avatar");
  if (role === "user") text(avatar, "我");
  else avatar.append(icon("leaf"));
  head.append(avatar, el("span", "", role === "user" ? "我" : "交易伙伴"));
  const body = el("div", "body");
  if (role === "user") text(body, value);
  else renderBody(body, value);
  node.append(head, body);
  if (role === "user") attachments.renderMessage(node, files);
  if (role === "assistant") {
    const actions = el("div", "message-actions"),
      copy = el("button", "icon-button");
    copy.type = "button";
    copy.title = "复制回复";
    copy.setAttribute("aria-label", "复制回复");
    copy.append(icon("copy"));
    copy.onclick = () => copyText(body.innerText);
    actions.append(copy);
    if (model) actions.append(el("span", "message-model", model));
    node.append(actions);
  }
  $("messages").append(node);
  scrollBottom(true);
  return body;
}
async function copyText(value) {
  try {
    if (navigator.clipboard && window.isSecureContext)
      await navigator.clipboard.writeText(value);
    else {
      const temp = el("textarea");
      temp.value = value;
      temp.style.cssText = "position:fixed;opacity:0";
      document.body.append(temp);
      temp.select();
      const ok = document.execCommand("copy");
      temp.remove();
      if (!ok) throw new Error();
    }
    toast("回复已复制");
  } catch {
    toast("暂时无法复制，可以选中文字复制。");
  }
}
async function refreshSessions() {
  const rows = await data("/api/sessions");
  $("sessions").replaceChildren();
  if (!rows.length) {
    $("sessions").append(
      el("p", "session-empty", "从一段对话开始，留下思考的足迹。"),
    );
    return;
  }
  let lastGroup = "";
  for (const row of rows) {
    const today = date(new Date().toISOString()),
      group = date(row.updated_at) === today ? "今天" : "更早的对话";
    if (group !== lastGroup) {
      $("sessions").append(el("div", "session-date", group));
      lastGroup = group;
    }
    const button = el("button", "", row.title || "一次对话");
    button.title = row.title || "一次对话";
    button.classList.toggle("active", row.id === session);
    button.onclick = () => loadSession(row);
    $("sessions").append(button);
  }
}
async function loadSession(row) {
  if (busy) {
    toast("回复完成后可以切换对话。");
    return;
  }
  const version = ++sessionVersion;
  const previous = session;
  session = row.id;
  view("chat");
  setSidebar(false);
  text($("page-title"), row.title || "对话");
  $("messages").replaceChildren(el("div", "empty-state", "正在找回对话…"));
  try {
    const history = await data("/api/sessions/" + encodeURIComponent(session));
    if (version !== sessionVersion) return;
    $("messages").replaceChildren();
    for (const m of history) message(m.role, m.text, "", m.attachments);
    if (!history.length) welcome();
    await refreshSessions();
  } catch (error) {
    if (version !== sessionVersion) return;
    session = previous;
    errorState($("messages"), error.message, () => loadSession(row));
  }
}
function newChat() {
  if (busy) {
    toast("回复完成后可以开始新对话。");
    return;
  }
  sessionVersion++;
  session = uuid();
  text($("page-title"), "对话");
  welcome();
  view("chat");
  setSidebar(false);
  refreshSessions().catch(() => {});
  $("input").focus();
}
async function refreshModels() {
  const result = await data("/api/models");
  modelProfiles = result.models;
  activeModel = result.active;
  $("model-select").replaceChildren();
  for (const m of result.models) {
    const option = el(
      "option",
      "",
      m.label + (m.configured ? (m.vision ? " · 可读图" : "") : " · 未配置"),
    );
    option.value = m.id;
    option.disabled = !m.configured;
    option.selected = m.id === activeModel;
    $("model-select").append(option);
  }
}
async function enter() {
  const status = await data("/api/status");
  $("login").hidden = true;
  $("workspace").hidden = false;
  $("connection").hidden = status.model_configured;
  text($("connection"), "模型尚未配置。你可以先阅读资料，配置后就能开始对话。");
  await Promise.all([refreshModels(), refreshSessions()]);
  restoreDraft();
  await attachments.enter();
  syncSend();
  bindSuggestions();
}
$("model-select").onchange = async () => {
  const id = $("model-select").value;
  $("model-select").disabled = true;
  try {
    await data("/api/models/select", {
      method: "POST",
      body: JSON.stringify({ model: id }),
    });
    activeModel = id;
    $("connection").hidden = true;
    toast("已切换模型，下一条消息生效");
    if (
      attachments.hasImages() &&
      !modelProfiles.find((model) => model.id === id)?.vision
    )
      toast("这个模型不支持图片，请选择标有“可读图”的模型后发送。");
  } catch (error) {
    $("model-select").value = activeModel;
    toast(error.message);
  } finally {
    $("model-select").disabled = busy;
  }
};
$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("login-submit").disabled = true;
  text($("login-error"), "");
  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ password: $("password").value }),
    });
    $("password").value = "";
    await enter();
  } catch (error) {
    text($("login-error"), error.message);
  } finally {
    $("login-submit").disabled = false;
  }
};
$("logout").onclick = async () => {
  if (busy) {
    toast("回复完成后可以退出。");
    return;
  }
  try {
    await api("/api/logout", { method: "POST" });
    sessionVersion++;
    detailVersion++;
    $("detail").close();
    $("messages").replaceChildren();
    welcome();
    session = uuid();
    attachments.stop(true);
    attachments.clear();
    showLogin();
  } catch (error) {
    toast(error.message);
  }
};
$("new-chat").onclick = newChat;
const progressLabels = {
  memory_search: "正在找回相关历史",
  partner_process: "正在整理原话与资料",
  partner_finalize: "正在汇总核对结果",
  market_quote: "正在读取行情",
  market_bars: "正在读取价格走势",
  features_compute: "正在分析价格特征",
  chart_render: "正在生成走势图",
  Skill: "正在读取相关工作方法",
  load_skill: "正在读取相关工作方法",
  review_save: "正在保存复盘",
  system_overview: "正在读取交易体系",
};
$("chat-form").onsubmit = async (e) => {
  e.preventDefault();
  if (busy) return;
  const value = $("input").value.trim();
  if (attachments.pending()) {
    toast("请先完成附件读取或录音，失败的附件可重试或移除。");
    return;
  }
  const files = attachments.ready();
  if (!value && !files.length) return;
  if (
    attachments.hasImages() &&
    !modelProfiles.find((model) => model.id === activeModel)?.vision
  ) {
    toast("当前模型不支持图片，请切换到标有“可读图”的模型后发送。");
    $("model-select").focus();
    return;
  }
  busy = true;
  attachments.setBusy(true);
  $("send").disabled = true;
  $("model-select").disabled = true;
  $("new-chat").disabled = true;
  text($("progress"), "正在理解你的问题…");
  message("user", value, "", files);
  $("input").value = "";
  resizeInput();
  saveDraft();
  const target = message("assistant", ""),
    modelLabel = $("model-select").selectedOptions[0]?.textContent || "",
    body = {
      session_id: session,
      message_id: uuid(),
      text: value,
      attachment_ids: files.map((item) => item.id),
      remember: $("remember").checked,
      model: activeModel,
    };
  target.closest(".message").querySelector(".message-actions").hidden = true;
  let content = "",
    hadError = false;
  try {
    const response = await api("/api/router/ask", {
      method: "POST",
      body: JSON.stringify(body),
    });
    attachments.clear();
    const reader = response.body.getReader(),
      decoder = new TextDecoder();
    let buffer = "",
      frame = 0;
    const update = () => {
      frame = 0;
      const follow =
        $("messages").scrollHeight -
          $("messages").scrollTop -
          $("messages").clientHeight <
        160;
      renderBody(target, content);
      if (follow) scrollBottom(true);
    };
    const consume = (block) => {
      const line = block.split("\n").find((x) => x.startsWith("data: "));
      if (!line) return;
      const event = JSON.parse(line.slice(6));
      if (event.type === "assistant") {
        content += event.payload.text || "";
        text($("progress"), "正在组织回复…");
        if (!frame) frame = requestAnimationFrame(update);
      } else if (event.type === "tool_use")
        text(
          $("progress"),
          progressLabels[event.payload.tool_name] || "正在核对相关资料…",
        );
      else if (event.type === "error") {
        hadError = true;
        content += "\n\n" + (event.payload.text || "这次处理未能完成。");
      }
    };
    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value || new Uint8Array(), {
        stream: !chunk.done,
      });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop();
      blocks.forEach(consume);
      if (chunk.done) {
        if (buffer.trim()) consume(buffer);
        break;
      }
    }
    if (frame) cancelAnimationFrame(frame);
    renderBody(
      target,
      content || "本轮没有生成完整回复。可以稍后查看历史，或补充问题继续。",
    );
    if (hadError) toast("本次处理未完整完成，请先查看已保存的资料。");
  } catch (error) {
    if (!$("input").value && !content) {
      $("input").value = value;
      resizeInput();
      saveDraft();
    }
    renderBody(
      target,
      (content ? content + "\n\n" : "") +
        error.message +
        "\n\n可以继续补充问题。",
    );
    toast("本次回复未完成，输入区仍可继续使用。");
  } finally {
    busy = false;
    attachments.setBusy(false);
    syncSend();
    $("model-select").disabled = false;
    $("new-chat").disabled = false;
    text($("progress"), "");
    const actions = target
      .closest(".message")
      .querySelector(".message-actions");
    actions.hidden = false;
    actions.append(el("span", "message-model", modelLabel));
    refreshSessions().catch(() => {});
  }
};
$("input").onkeydown = (e) => {
  if (
    e.key === "Enter" &&
    !e.shiftKey &&
    !e.isComposing &&
    window.matchMedia("(min-width:761px)").matches
  ) {
    e.preventDefault();
    $("chat-form").requestSubmit();
  }
};
function resizeInput() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height =
    Math.min(
      input.scrollHeight,
      window.matchMedia("(max-width:760px)").matches ? 130 : 180,
    ) + "px";
}
function saveDraft() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    try {
      sessionStorage.setItem("trading-draft", $("input").value);
    } catch {}
  }, 180);
}
function restoreDraft() {
  try {
    const draft = sessionStorage.getItem("trading-draft");
    if (draft && !$("input").value) $("input").value = draft;
  } catch {}
  resizeInput();
}
$("input").oninput = () => {
  resizeInput();
  saveDraft();
  syncSend();
};
$("remember").onchange = () =>
  text(
    $("memory-hint"),
    $("remember").checked
      ? "附件保存在对话中 · 每次最多 6 个，单个 20 MB。"
      : "本次只保存聊天和附件，不写入交易资料。",
  );
$("messages").onscroll = () => {
  $("jump-latest").hidden =
    $("messages").scrollHeight -
      $("messages").scrollTop -
      $("messages").clientHeight <
    220;
};
$("jump-latest").onclick = () => {
  $("messages").scrollTo({
    top: $("messages").scrollHeight,
    behavior: window.matchMedia("(prefers-reduced-motion:reduce)").matches
      ? "instant"
      : "smooth",
  });
};
function setSidebar(open) {
  $("workspace").classList.toggle("sidebar-open", open);
  $("sidebar-backdrop").hidden = !open;
  $("open-sidebar").setAttribute("aria-expanded", String(open));
  if (
    !open &&
    document.activeElement &&
    $("sidebar").contains(document.activeElement) &&
    window.matchMedia("(max-width:760px)").matches
  )
    $("open-sidebar").focus();
}
$("open-sidebar").onclick = () => setSidebar(true);
$("close-sidebar").onclick = () => setSidebar(false);
$("sidebar-backdrop").onclick = () => setSidebar(false);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") setSidebar(false);
  if (
    (e.metaKey || e.ctrlKey) &&
    e.key.toLowerCase() === "k" &&
    !$("workspace").hidden
  ) {
    e.preventDefault();
    newChat();
  }
});
function view(name) {
  const changed = currentView !== name;
  currentView = name;
  document.querySelectorAll("[data-view]").forEach((b) => {
    b.classList.toggle("selected", b.dataset.view === name);
    if (b.dataset.view === name) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  ["chat", "records", "system"].forEach(
    (key) => ($(key + "-view").hidden = key !== name),
  );
  text(
    $("page-title"),
    name === "chat" ? "对话" : name === "records" ? "知识库" : "交易体系",
  );
  text(
    $("page-eyebrow"),
    name === "chat"
      ? "想法与判断"
      : name === "records"
        ? "经历与知识"
        : "方向与方法",
  );
  if (name === "records" && changed) {
    if (libraryTab === "personal") search();
    else classics();
  }
  if (name === "system" && changed) overview();
}
document.querySelectorAll("[data-view]").forEach(
  (b) =>
    (b.onclick = () => {
      view(b.dataset.view);
      setSidebar(false);
    }),
);
function skeleton(node, n = 4) {
  node.replaceChildren();
  for (let i = 0; i < n; i++) node.append(el("div", "skeleton"));
}
function empty(node, title, description, action, label = "开始一段对话") {
  const wrap = el("div", "empty-state");
  wrap.append(icon("book"), el("h3", "", title), el("p", "", description));
  if (action) {
    const b = el("button", "", label);
    b.onclick = action;
    wrap.append(b);
  }
  node.replaceChildren(wrap);
}
function errorState(node, value, retry) {
  const wrap = el("div", "error-state", value);
  const button = el("button", "", "重新读取");
  button.onclick = retry;
  wrap.append(button);
  node.replaceChildren(wrap);
}
function recordCard(row) {
  const node = el("article", "record-card"),
    top = el("div", "card-top"),
    type = el("span", "kind-label");
  type.append(
    icon(
      row.kind === "learning"
        ? "book"
        : row.kind === "review"
          ? "history"
          : "note",
    ),
    document.createTextNode(kinds[row.kind] || "资料"),
  );
  const time = el("time", "", date(row.updated_at || row.created_at));
  time.dateTime = row.updated_at || row.created_at;
  top.append(type, time);
  node.append(
    top,
    el("h3", "", titleOf(row)),
    el("p", "record-summary", summaryOf(row)),
  );
  const tags = [
    ...(row.body?.security_ids || []),
    ...(row.body?.tags || []),
  ].filter((v) => typeof v === "string");
  if (tags.length) {
    const group = el("div", "record-tags");
    [...new Set(tags)]
      .slice(0, 3)
      .forEach((t) => group.append(el("span", "tag", t)));
    node.append(group);
  }
  const bottom = el("div", "card-bottom"),
    state = recordState(row);
  bottom.append(el("span", "state-badge " + state[1], state[0]));
  const button = el("button", "record-open", "阅读资料");
  button.setAttribute("aria-label", "阅读资料：" + titleOf(row));
  button.append(icon("arrow"));
  button.onclick = () => detail(row.id);
  bottom.append(button);
  node.append(bottom);
  return node;
}
function populateKinds() {
  const old = $("kind").value;
  $("kind").replaceChildren(el("option", "", "所有类型"));
  $("kind").firstChild.value = "";
  for (const k of groupKinds[currentGroup]) {
    const option = el("option", "", kinds[k]);
    option.value = k;
    $("kind").append(option);
  }
  $("kind").value = groupKinds[currentGroup].includes(old) ? old : "";
}
async function search() {
  const version = ++libraryVersion;
  const target = $("records-list");
  skeleton(target);
  $("pagination").hidden = true;
  text($("result-count"), "正在读取资料…");
  const params = new URLSearchParams({
    query: $("search").value.trim(),
    group: currentGroup,
    kind: $("kind").value,
    state: reviewOnly ? "needs_review" : "",
    page: libraryPage,
    size: 12,
  });
  try {
    const result = await data("/api/library?" + params);
    if (version !== libraryVersion) return;
    libraryTotal = result.total;
    text($("library-count"), result.groups.all);
    text($("classic-count"), result.classic_count);
    document
      .querySelectorAll("[data-group]")
      .forEach((b) =>
        text(b.querySelector("span"), result.groups[b.dataset.group] || 0),
      );
    text($("collection-description"), groupDescriptions[currentGroup]);
    text(
      $("result-count"),
      (currentGroup === "originals" ? "原始资料" : "资料") +
        " · " +
        result.total +
        " 份" +
        ($("search").value.trim() ? "匹配结果" : ""),
    );
    target.replaceChildren();
    for (const row of result.records) target.append(recordCard(row));
    if (!result.records.length)
      empty(
        target,
        reviewOnly
          ? "暂时没有待复查资料"
          : $("search").value
            ? "没有找到匹配资料"
            : "这一类积累，正等待开始",
        reviewOnly
          ? "来源更正后，需要回看的相关认识会出现在这里。"
          : "换一个主题试试，或在对话中记录新的想法。",
        () => {
          if ($("search").value || reviewOnly || $("kind").value) {
            $("search").value = "";
            $("kind").value = "";
            reviewOnly = false;
            syncFilters();
            libraryPage = 1;
            search();
          } else
            startPrompt(
              "我想开始积累" +
                groupDescriptions[currentGroup] +
                "，请帮助我整理真实的想法和经历。",
            );
        },
        $("search").value || reviewOnly || $("kind").value
          ? "清除筛选"
          : "开始记录",
      );
    const pages = Math.max(1, Math.ceil(result.total / result.size));
    $("pagination").hidden = pages <= 1;
    text($("page-number"), libraryPage + " / " + pages);
    $("prev-page").disabled = libraryPage <= 1;
    $("next-page").disabled = libraryPage >= pages;
  } catch (error) {
    if (version !== libraryVersion) return;
    text($("result-count"), "资料暂时未能读取");
    errorState(target, error.message, search);
  }
}
function syncFilters() {
  document
    .querySelectorAll("[data-group]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.group === currentGroup)),
    );
  $("review-filter").setAttribute("aria-pressed", String(reviewOnly));
  $("clear-search").hidden = !$("search").value;
}
document.querySelectorAll("[data-group]").forEach(
  (b) =>
    (b.onclick = () => {
      currentGroup = b.dataset.group;
      libraryPage = 1;
      populateKinds();
      syncFilters();
      search();
    }),
);
$("search-form").onsubmit = (e) => {
  e.preventDefault();
  clearTimeout(searchTimer);
  libraryPage = 1;
  syncFilters();
  search();
};
$("search").oninput = () => {
  syncFilters();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    libraryPage = 1;
    search();
  }, 360);
};
$("kind").onchange = () => {
  libraryPage = 1;
  search();
};
$("review-filter").onclick = () => {
  reviewOnly = !reviewOnly;
  libraryPage = 1;
  syncFilters();
  search();
};
$("clear-search").onclick = () => {
  $("search").value = "";
  clearTimeout(searchTimer);
  libraryPage = 1;
  syncFilters();
  search();
  $("search").focus();
};
$("prev-page").onclick = () => {
  libraryPage--;
  search();
  $("records-view").scrollTo({ top: 180, behavior: "smooth" });
};
$("next-page").onclick = () => {
  libraryPage++;
  search();
  $("records-view").scrollTo({ top: 180, behavior: "smooth" });
};
function selectLibrary(tab) {
  libraryTab = tab;
  $("personal-tab").setAttribute("aria-selected", String(tab === "personal"));
  $("classics-tab").setAttribute("aria-selected", String(tab === "classics"));
  $("personal-library").hidden = tab !== "personal";
  $("classic-library").hidden = tab !== "classics";
  if (tab === "personal") search();
  else classics();
}
$("personal-tab").onclick = () => selectLibrary("personal");
$("classics-tab").onclick = () => selectLibrary("classics");
[$("personal-tab"), $("classics-tab")].forEach(
  (b) =>
    (b.onkeydown = (e) => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        const next =
          e.key === "Home"
            ? "personal"
            : e.key === "End"
              ? "classics"
              : libraryTab === "personal"
                ? "classics"
                : "personal";
        selectLibrary(next);
        $(next === "personal" ? "personal-tab" : "classics-tab").focus();
      }
    }),
);
async function classics() {
  const version = ++classicVersion,
    target = $("classics-list");
  skeleton(target);
  try {
    const result = await data(
      "/api/classics?" +
        new URLSearchParams({ query: $("classic-search").value.trim() }),
    );
    if (version !== classicVersion) return;
    text($("classic-count"), result.catalog_size);
    target.replaceChildren();
    for (const entry of result.entries) {
      const node = el("article", "record-card"),
        top = el("div", "card-top");
      top.append(
        el("span", "kind-label", "经典心法"),
        el("span", "classic-index", entry.id),
      );
      const summary =
        entry.content
          .split("\n")
          .find(
            (l) =>
              l.includes("本项目理解") ||
              l.includes("本项目应用") ||
              l.includes("原意语境"),
          ) || "";
      node.append(
        top,
        el("h3", "", entry.title),
        el("p", "record-summary", cleanText(summary.replace(/^[-*]\s*/, ""))),
      );
      const tags = el("div", "record-tags");
      entry.tags.slice(0, 3).forEach((t) => tags.append(el("span", "tag", t)));
      const bottom = el("div", "card-bottom"),
        button = el("button", "record-open", "展开阅读");
      button.setAttribute("aria-label", "展开经典：" + entry.title);
      button.append(icon("arrow"));
      button.onclick = () => classicDetail(entry);
      bottom.append(el("span", "state-badge", "经典资料 · 待个人吸收"), button);
      node.append(tags, bottom);
      target.append(node);
    }
    if (!result.entries.length)
      empty(target, "没有找到这个主题", "试试风险、执行、情绪等主题词。");
  } catch (error) {
    if (version === classicVersion) errorState(target, error.message, classics);
  }
}
$("classic-search-form").onsubmit = (e) => {
  e.preventDefault();
  classics();
};
/* Progressive reading: summary -> context -> original -> history. */
function section(target, title, content) {
  const group = el("section", "detail-section");
  group.append(el("h3", "", title), content);
  target.append(group);
  return group;
}
function readable(value) {
  if (value === null || value === undefined || value === "unknown")
    return "尚未提供";
  if (
    typeof value === "string" &&
    {
      short: "短线",
      medium_long: "中长线",
      reported_executed: "自述已执行",
      pending: "待确认",
      unverified: "待验证",
      user_report: "个人自述",
      hypothesis: "待验证假设",
      plan: "计划",
      not_defined: "尚未形成",
    }[value]
  )
    return {
      short: "短线",
      medium_long: "中长线",
      reported_executed: "自述已执行",
      pending: "待确认",
      unverified: "待验证",
      user_report: "个人自述",
      hypothesis: "待验证假设",
      plan: "计划",
      not_defined: "尚未形成",
    }[value];
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map(readable).join("\n");
  if (typeof value === "object")
    return Object.entries(value)
      .map(([k, v]) => (fieldNames[k] || k) + "：" + readable(v))
      .join("\n");
  return String(value);
}
function fieldList(fields) {
  const list = el("dl", "field-list");
  for (const [k, v] of Object.entries(fields || {})) {
    const item = el("div", "field-item");
    item.append(
      el("dt", "", fieldNames[k] || k.replaceAll("_", " ")),
      el("dd", "", readable(v)),
    );
    list.append(item);
  }
  return list;
}
function statesPanel(row) {
  const block = el("div", "knowledge-states");
  for (const [key, label, hint] of [
    ["source", "资料来源", "从哪里得来"],
    ["adoption", "个人认同", "自己的理解与取舍"],
    ["evidence", "实践效果", "实际经历的验证"],
  ]) {
    const card = el("div", "knowledge-state");
    card.append(
      el("small", "", label),
      el(
        "strong",
        "",
        stateNames[key][row.states?.[key]] ||
          stateNames[key].unverified ||
          "待形成认同",
      ),
      el("p", "", hint),
    );
    block.append(card);
  }
  return block;
}
function openDrawer() {
  if (!$("detail").open) $("detail").showModal();
  $("detail-scroll").scrollTop = 0;
  $("discuss-detail").disabled = true;
}
async function detail(id) {
  const version = ++detailVersion;
  currentDetail = null;
  openDrawer();
  text($("detail-category"), "资料详情");
  text($("detail-title"), "正在找回资料…");
  $("detail-content").replaceChildren(el("div", "skeleton"));
  try {
    const row = await data("/api/records/" + encodeURIComponent(id));
    if (version !== detailVersion || !$("detail").open) return;
    currentDetail = { type: "record", row };
    text($("detail-title"), titleOf(row));
    text($("detail-category"), kinds[row.kind] || "资料详情");
    const target = $("detail-content");
    target.replaceChildren();
    const meta = el("div", "detail-meta");
    meta.append(
      el("span", "", date(row.created_at, true)),
      el("span", "", "第 " + row.version + " 版"),
    );
    const state = recordState(row);
    meta.append(el("span", "state-badge " + state[1], state[0]));
    target.append(meta);
    if (
      [
        "learning",
        "hypothesis",
        "note",
        "proposal",
        "strategy",
        "evolution",
      ].includes(row.kind)
    )
      target.append(statesPanel(row));
    const body = el("div", "detail-body");
    renderBody(body, recordText(row));
    target.append(body);
    if (row.body?.excerpt && row.body.excerpt !== row.body.text) {
      const quote = el("blockquote", "");
      inline(quote, row.body.excerpt);
      section(target, "关联的个人原话", quote);
    }
    if (row.body?.fields && Object.keys(row.body.fields).length)
      section(target, "记录中的关键信息", fieldList(row.body.fields));
    if (row.kind === "market_snapshot") {
      const b = row.body;
      section(
        target,
        "数据来处与时间",
        fieldList({
          证券代码: b.security_id,
          数据时间: b.data_as_of,
          获取时间: b.fetched_at,
          来源: b.source,
          周期: b.interval,
          复权方式: b.adjustment,
          质量提醒: b.warnings || b.quality,
        }),
      );
      if (Array.isArray(b.bars)) {
        section(
          target,
          "最近的行情数据",
          fieldList(
            Object.fromEntries(
              b.bars
                .slice(-5)
                .map((bar, i) => [
                  bar.time || bar.date || "数据 " + (i + 1),
                  bar,
                ]),
            ),
          ),
        );
      }
    }
    if (row.body?.claims?.length) {
      const claims = el("div", "markdown");
      renderBody(
        claims,
        row.body.claims
          .map((c) =>
            typeof c === "string" ? c : c.text || c.claim || readable(c),
          )
          .join("\n\n"),
      );
      section(target, "结论与依据", claims);
    }
    if (row.body?.follow_up && Object.keys(row.body.follow_up).length)
      section(target, "后续回看", fieldList(row.body.follow_up));
    if (row.kind === "source") {
      const b = row.body,
        fields = {
          来源: b.origin || b.url,
          发布时间: b.published_at,
          采集时间: b.retrieved_at,
          核验说明: b.verification,
        };
      section(target, "来源信息", fieldList(fields));
      if (b.url && /^https?:\/\//.test(b.url)) {
        const a = el("a", "", "打开来源页面");
        a.href = b.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        target.lastChild.append(a);
      }
    }
    const references = [...new Set(row.reference_ids || [])];
    if (references.length) {
      const group = el("div");
      for (const ref of references) {
        const b = el("button", "reference-button");
        b.append(
          icon("note"),
          el(
            "span",
            "",
            (ref === row.body?.source_id ? "关联的个人原话" : "关联资料") +
              " · " +
              ref.slice(-8),
          ),
          icon("chevron"),
        );
        b.onclick = () => detail(ref);
        group.append(b);
      }
      section(target, "可追溯的关联资料", group);
    }
    if (row.dependent_ids?.length) {
      const group = el("div");
      for (const ref of row.dependent_ids) {
        const b = el("button", "reference-button");
        b.append(
          icon("compass"),
          el("span", "", "引用记录 · " + ref.slice(-8)),
          icon("chevron"),
        );
        b.onclick = () => detail(ref);
        group.append(b);
      }
      section(target, "引用这份资料的记录", group);
    }
    if (row.history?.length) {
      const history = el("div");
      for (const revision of [...row.history].reverse()) {
        const item = el("div", "revision"),
          head = el("div", "revision-title");
        head.append(
          el("strong", "", "第 " + revision.version + " 版"),
          el("span", "", date(revision.recorded_at)),
        );
        item.append(head, el("p", "", revision.reason || "首次记录"));
        const fold = el("details"),
          summary = el("summary", "", "查看这一版的内容"),
          content = el("div", "markdown");
        renderBody(
          content,
          revision.body?.text ||
            revision.body?.summary ||
            readable(revision.body),
        );
        fold.append(summary, content);
        item.append(fold);
        history.append(item);
      }
      section(target, "原文与版本", history);
    }
    target.append(
      el(
        "p",
        "detail-note",
        row.kind === "position"
          ? "持仓来自个人记录，按记录时间阅读。"
          : row.states?.rule === "active"
            ? "这是已确认的纪律版本；当前生效版本可以在「交易体系」查看。"
            : "资料状态、个人认同和实践效果分别保留。",
      ),
    );
    $("discuss-detail").disabled = false;
  } catch (error) {
    if (version !== detailVersion) return;
    text($("detail-title"), "资料未能读取");
    errorState($("detail-content"), error.message, () => detail(id));
  }
}
function renderClassic(target, entry) {
  const groups = { origin: [], understanding: [], boundary: [], source: [] };
  const content = entry.content.replace(/^##[^\n]+\n?/, "");
  const parts = content.split(/\n(?=- \*\*)/).filter((p) => p.trim());
  let parsed = true;
  for (const part of parts) {
    const m = part.trim().match(/^- \*\*([^*]+)\*\*[:：]?\s*([\s\S]*)$/);
    if (!m) {
      parsed = false;
      break;
    }
    let label = m[1],
      body = m[2];
    const group = /来源|关联|认同/.test(label)
      ? "source"
      : /边界/.test(label)
        ? "boundary"
        : /本项目|助手|个人吸收|应用|理解/.test(label)
          ? "understanding"
          : "origin";
    if (label === "个人认同")
      body = body
        .replaceAll("pending", "待个人吸收")
        .replaceAll("unverified", "实践效果待验证");
    if (label.includes("关联层级"))
      body = body
        .replace(/dao/g, "道 · 方向")
        .replace(/fa/g, "法 · 规则")
        .replace(/shu/g, "术 · 方法")
        .replace(/qi/g, "器 · 资料");
    groups[group].push([label, body]);
  }
  if (!parsed) {
    const body = el("div", "detail-body");
    renderBody(body, content);
    target.append(body);
    return;
  }
  for (const [key, title] of [
    ["origin", "原意与观点"],
    ["understanding", "理解与应用"],
    ["boundary", "适用边界"],
    ["source", "来源与关联"],
  ]) {
    if (!groups[key].length) continue;
    const block = el("div", "classic-sections " + key);
    for (const [label, body] of groups[key]) {
      const item = el("div", "classic-item");
      item.append(el("h4", "", label));
      const copy = el(/原文/.test(label) ? "blockquote" : "div", "markdown");
      renderBody(copy, body);
      item.append(copy);
      block.append(item);
    }
    section(target, title, block);
  }
}
function classicDetail(entry) {
  detailVersion++;
  openDrawer();
  currentDetail = { type: "classic", row: entry };
  text($("detail-title"), entry.title);
  text($("detail-category"), "经典书架 · " + entry.id);
  const target = $("detail-content");
  target.replaceChildren();
  target.append(
    statesPanel({
      states: {
        source: entry.source_status,
        adoption: "pending",
        evidence: "unverified",
      },
    }),
  );
  renderClassic(target, entry);
  target.append(
    el(
      "p",
      "detail-note",
      "这里显示经典资料本身。你的理解、认同与实践记录，可在对话中单独积累。",
    ),
  );
  $("discuss-detail").disabled = false;
}
$("close-detail").onclick = () => $("detail").close();
$("detail").addEventListener("close", () => {
  detailVersion++;
});
$("detail").onclick = (e) => {
  if (e.target === $("detail")) {
    const r = $("detail").getBoundingClientRect();
    if (
      e.clientX < r.left ||
      e.clientX > r.right ||
      e.clientY < r.top ||
      e.clientY > r.bottom
    )
      $("detail").close();
  }
};
$("discuss-detail").onclick = () => {
  if (!currentDetail) return;
  const { type, row } = currentDetail;
  const prompt =
    type === "classic"
      ? "一起读懂这条经典：" +
        row.id +
        "「" +
        row.title +
        "」。请区分原意、我的理解与实际适用边界。"
      : "请找回资料 " +
        row.id +
        "「" +
        titleOf(row) +
        "」，结合原文与关联资料，帮我继续梳理和回看。";
  $("detail").close();
  startPrompt(prompt);
  toast("资料已带入输入框，补充想法后发送。");
};
async function overview() {
  const version = ++overviewVersion;
  skeleton($("system-content"), 2);
  try {
    const result = await data("/api/overview");
    if (version !== overviewVersion) return;
    systemData = result;
    if (!(result.layers[currentLayer] || []).length)
      currentLayer =
        Object.keys(layerInfo).find((k) => (result.layers[k] || []).length) ||
        "dao";
    currentLayerPage = 1;
    renderSystem();
  } catch (error) {
    if (version === overviewVersion)
      errorState($("system-content"), error.message, overview);
  }
}
function renderSystem() {
  const target = $("system-content");
  target.replaceChildren();
  const data = systemData,
    strategy = data.strategy || {},
    panel = el("article", "strategy-panel"),
    head = el("div", "strategy-heading");
  head.append(
    icon("shield"),
    el("h3", "", "已确认的交易纪律"),
    el(
      "span",
      "state-badge " + (strategy.id ? "green" : ""),
      strategy.id ? "当前生效版本" : "尚未形成",
    ),
  );
  panel.append(head);
  if (strategy.id) {
    const list = el("ul");
    for (const r of strategy.body?.rules || [])
      list.append(el("li", "", r.text));
    panel.append(
      el("p", "", date(strategy.body?.effective_at, true) + " 生效"),
      list,
    );
    const b = el("button", "record-open", "阅读完整纪律");
    b.append(icon("arrow"));
    b.onclick = () => detail(strategy.id);
    panel.append(b);
  } else
    panel.append(
      el(
        "p",
        "",
        "从实际经历中提炼规则，再由你明确确认。学习心得和候选认识会保留在各层资料中，等待实践检验。",
      ),
    );
  target.append(panel);
  const heading = el("div", "section-heading");
  heading.append(
    el("h3", "", "四层积累"),
    el("span", "", "同一份资料可关联多个层级"),
  );
  target.append(heading);
  const layers = el("div", "layer-grid");
  for (const [k, [character, title, description]] of Object.entries(
    layerInfo,
  )) {
    const b = el("button", "layer-card");
    b.setAttribute("aria-pressed", String(k === currentLayer));
    b.append(
      el("span", "layer-character", character),
      el("strong", "", title),
      el("small", "", description),
      el("span", "layer-count", (data.layers[k] || []).length + " 份关联资料"),
    );
    b.onclick = () => {
      currentLayer = k;
      currentLayerPage = 1;
      renderSystem();
    };
    layers.append(b);
  }
  target.append(layers);
  const rows = data.layers[currentLayer] || [],
    info = layerInfo[currentLayer],
    sub = el("div", "layer-heading"),
    left = el("div");
  left.append(el("h3", "", info[0] + " · " + info[1]), el("p", "", info[2]));
  sub.append(left, el("span", "", rows.length + " 份资料"));
  target.append(sub);
  const list = el("div", "system-list");
  for (const row of rows.slice(0, currentLayerPage * 10)) {
    const b = el("button", "system-row"),
      copy = el("div");
    copy.append(
      el("h4", "", cleanText(row.text).slice(0, 150)),
      el(
        "p",
        "",
        (kinds[row.kind] || "资料") +
          " · " +
          date(row.recorded_at) +
          " · " +
          (row.layers || []).map((k) => layerInfo[k]?.[0]).join(" / "),
      ),
    );
    const s = recordState(row);
    b.append(copy, el("span", "state-badge " + s[1], s[0]), icon("chevron"));
    b.onclick = () => detail(row.id);
    list.append(b);
  }
  if (!rows.length)
    empty(
      list,
      "这一层，还在慢慢形成",
      "从一次真实经历开始，整理方向、规则与方法。",
      () =>
        startPrompt(
          "我们一起梳理我的" +
            info[1] +
            "。请先找回相关记录，再问清尚缺的信息。",
        ),
      "一起梳理",
    );
  if (rows.length > currentLayerPage * 10) {
    const more = el("button", "secondary", "继续展开更多资料");
    more.onclick = () => {
      currentLayerPage++;
      const scroll = $("system-view").scrollTop;
      renderSystem();
      $("system-view").scrollTop = scroll;
    };
    list.append(more);
  }
  target.append(
    list,
    el(
      "p",
      "system-footer",
      "按现有资料关联展示。个人认同、实践效果与纪律生效分别记录，复查时从真实原文和经历出发。",
    ),
  );
}
populateKinds();
syncFilters();
enter().catch(() => showLogin());
