export const shelves = [
  {
    key: "outcomes",
    title: "整理成果",
    description: "研究、复盘与学习成果，方便反复查阅与使用。",
  },
  {
    key: "workbench",
    title: "待打磨",
    description: "笔记、候选认识与研究线索，逐步整理成清晰的成果。",
  },
  {
    key: "references",
    title: "资料档案",
    description: "原话、外部来源与交易记录，为成果保留真实依据。",
  },
  {
    key: "classics",
    title: "经典书架",
    description: "阅读原意、理解与适用边界，再决定如何吸收。",
  },
];
export const topics: Record<string, string> = {
  general: "未分类",
  principles: "交易认知",
  risk: "风险与仓位",
  execution: "交易执行",
  review: "复盘方法",
  market: "市场研究",
  mindset: "心态与习惯",
};
export const shelfName = (key: string) =>
  shelves.find((s) => s.key === key)?.title || "资料档案";
export const money = (value: number | null | undefined) =>
  value == null
    ? "—"
    : Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
export const percent = (value: number | null | undefined) =>
  value == null ? "未提供账户总资产" : (Number(value) * 100).toFixed(1) + "%";
