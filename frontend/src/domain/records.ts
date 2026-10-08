import {
  kindNames,
  fields as growthFields,
  labels as growthLabels,
} from "./growth";
import type { RecordRow } from "./types";
export const kinds: Record<string, string> = {
  ...kindNames,
  plan: "交易计划",
  trade: "交易记录",
  position: "持仓记录",
  review: "复盘",
  emotion: "心态记录",
  observation: "市场观察",
  note: "随手笔记",
  learning: "学习笔记",
  hypothesis: "候选认识",
  evolution: "体系演变",
  profile: "个人背景",
  proposal: "修改提案",
  strategy: "交易纪律",
  research: "研究资料",
  analysis: "分析记录",
  raw: "个人原话",
  source: "来源资料",
  market_snapshot: "行情快照",
  agent_run: "处理过程",
  agent_result: "处理结果",
};
export const layers = [
  {
    key: "dao",
    mark: "道",
    title: "方向与认识",
    description: "为什么交易，怎样取舍",
  },
  {
    key: "fa",
    mark: "法",
    title: "规则与计划",
    description: "用什么条件约束行动",
  },
  {
    key: "shu",
    mark: "术",
    title: "方法与实践",
    description: "怎样判断，如何执行",
  },
  {
    key: "qi",
    mark: "器",
    title: "具体工具",
    description: "可直接使用的计算、查询与记录工具",
  },
];
export const states: Record<string, Record<string, string>> = {
  source: {
    user_report: "个人自述",
    original_checked: "原始来源已核验",
    attributed_retelling: "转述资料",
    publisher_description: "发布者说明",
    unverified: "来源待核验",
  },
  adoption: {
    pending: "待个人吸收",
    agreed: "个人已认同",
    questioned: "保留疑问",
    rejected: "暂不采纳",
  },
  evidence: {
    unverified: "实践效果待验证",
    observing: "持续观察",
    limited_support: "有限证据支持",
    contradicted: "出现反例",
    needs_review: "需要回看",
  },
  rule: {
    not_applicable: "未作为纪律",
    draft: "候选规则",
    active: "已确认生效",
  },
};
export const stateNames: Record<string, string> = {
  source: "来源",
  adoption: "个人认同",
  evidence: "实践证据",
  rule: "纪律状态",
};
export function title(row: RecordRow) {
  const explicit = row.body.title || row.body.symbol || row.body.name;
  if (explicit) return explicit;
  const line =
    String(row.body.text || row.body.summary || "")
      .split("\n")
      .find((v) => v.trim()) || "";
  return (
    line
      .replace(/^#{1,6}\s*/, "")
      .replace(/^[【\[]|[】\]]$/g, "")
      .slice(0, 52) ||
    kinds[row.kind] ||
    "资料"
  );
}
export function brief(row: RecordRow) {
  return String(
    row.body.summary || row.body.text || row.body.reason || readable(row.body),
  )
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .trim();
}

export function date(value: string) {
  if (!value) return "";
  const d = new Date(value);
  return isNaN(d.getTime()) ? value : d.toLocaleDateString("zh-CN");
}
export function readable(value: any): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value !== "object") return String(value);
  if (Array.isArray(value))
    return value.map(readable).filter(Boolean).join("\n");
  return Object.entries(value)
    .filter(([k]) => !["metadata"].includes(k))
    .map(([k, v]) => `${fields[k] || "补充说明"}：${readable(v)}`)
    .join("\n");
}
export const fields: Record<string, string> = {
  text: "原文与内容",
  title: "标题",
  summary: "摘要",
  reason: "判断依据",
  rules: "行动规则",
  claims: "认识与依据",
  follow_up: "后续回看",
  symbol: "标的",
  name: "名称",
  origin: "来源",
  url: "来源链接",
  source_id: "关联原话",
  published_at: "发布时间",
  retrieved_at: "采集时间",
  verification: "核验说明",
  effective_at: "生效时间",
  price: "价格",
  quantity: "数量",
  side: "交易方向",
  status: "状态",
  notes: "备注",
  risk: "风险",
  evidence: "证据",
  conditions: "触发条件",
  failure: "失败路径",
  cycle: "周期",
  recorded_at: "记录时间",
  understanding: "个人理解",
  boundary: "适用边界",
  plan: "计划",
  amount: "金额",
  decision: "决定",
  result: "结果",
  action: "行动",
  tags: "标签",
  references: "关联资料",
  source: "来源",
  claim: "认识",
  description: "说明",
  confidence: "把握程度",
  reasoning: "推理依据",
  entry: "进入条件",
  exit: "退出条件",
  stop_loss: "止损条件",
  timeframe: "交易周期",
  fields: "补充信息",
  rationale: "修改依据",
  refs: "依据引用",
};
export function badge(row: RecordRow) {
  if (row.states?.evidence === "needs_review")
    return { text: "需要回看", tone: "review" };
  if (row.states?.evidence === "contradicted")
    return { text: "出现反例", tone: "review" };
  if (row.states?.rule === "active")
    return { text: "已确认纪律", tone: "confirmed" };
  if (row.states?.adoption === "questioned")
    return { text: "保留疑问", tone: "pending" };
  if (
    ["hypothesis", "proposal"].includes(row.kind) ||
    row.states?.rule === "draft"
  )
    return { text: "候选 · 待验证", tone: "pending" };
  if (
    row.collection?.shelf === "outcomes" &&
    row.states?.evidence === "unverified"
  )
    return { text: "证据待补充", tone: "pending" };
  return { text: kinds[row.kind] || "个人资料", tone: "neutral" };
}
export function kindLabel(row: RecordRow) {
  const outcomeKinds: Record<string, string> = {
    analysis: "研究分析",
    learning: "学习总结",
    review: "案例复盘",
    evolution: "体系回顾",
    note: "整理笔记",
    hypothesis: "候选方法",
    proposal: "规则提案",
  };
  return (
    (row.collection?.shelf === "outcomes" && outcomeKinds[row.kind]) ||
    kinds[row.kind] ||
    "资料"
  );
}
export function sections(row: RecordRow) {
  if (growthFields[row.kind])
    return growthFields[row.kind]
      .filter((k) => k !== "title" && row.body[k])
      .map((k) => ({
        key: k,
        label: growthLabels[k],
        text: String(row.body[k]),
      }));
  return Object.entries(row.body)
    .filter(
      ([k, v]) =>
        ![
          "metadata",
          "source_id",
          "title",
          "states",
          "layers",
          "item_key",
          "kind",
          "created_by",
          "tool_version",
          "content_hash",
          "confirmation_source_id",
          "digest",
          "base_id",
          "refs",
          "security_ids",
          "nature",
          "excerpt",
        ].includes(k) &&
        v != null &&
        readable(v) !== "",
    )
    .map(([key, value]) => ({
      key,
      label:
        key === "text" && row.collection?.shelf === "outcomes"
          ? "成果正文"
          : fields[key] || "补充说明",
      text: readable(value),
    }));
}
export function classicSections(entry: any) {
  const chunks = (entry.content || "")
    .replace(/^##[^\n]+\n?/, "")
    .split(/\n(?=- \*\*)/)
    .filter((p: string) => p.trim());
  const groups: any = {
    origin: [],
    understanding: [],
    boundary: [],
    source: [],
  };
  for (const part of chunks) {
    const m = part.trim().match(/^- \*\*([^*]+)\*\*[:：]?\s*([\s\S]*)$/);
    if (!m)
      return [
        {
          label: "原文与观点",
          items: [{ label: entry.title, text: entry.content }],
        },
      ];
    const key = /来源|关联|认同/.test(m[1])
      ? "source"
      : /边界/.test(m[1])
        ? "boundary"
        : /本项目|助手|个人吸收|应用|理解/.test(m[1])
          ? "understanding"
          : "origin";
    groups[key].push({
      label: m[1],
      text: m[2]
        .replace(/pending/g, "待个人吸收")
        .replace(/unverified/g, "实践效果待验证"),
    });
  }
  return Object.entries({
    origin: "原意与观点",
    understanding: "理解与应用",
    boundary: "适用边界",
    source: "来源与关联",
  })
    .filter(([k]) => groups[k].length)
    .map(([k, label]) => ({ label, items: groups[k] }));
}
