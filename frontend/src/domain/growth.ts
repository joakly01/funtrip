export const kindNames: Record<string, string> = {
  topic: "专题",
  insight: "知识笔记",
  method: "交易方法",
  practice: "实践与复盘",
  handbook: "交易理念",
};
export const labels: Record<string, string> = {
  title: "标题",
  category: "专题分类",
  question: "我正在解决的问题",
  summary: "我目前的认识",
  open_questions: "还没弄清的地方",
  next_step: "下一步想做什么",
  source_text: "资料摘录",
  understanding: "我的理解",
  boundary: "适用边界",
  example: "我的例子",
  scope: "参与范围与周期",
  conditions: "观察条件",
  steps: "准备怎样做",
  invalidation: "什么情况使判断失效",
  risk: "风险怎样处理",
  unknowns: "仍需验证的地方",
  judgment: "当时的判断",
  plan: "这次计划怎样做",
  actual: "实际做了什么",
  result: "后来发生了什么",
  review: "判断、执行与结果分别怎样",
  lesson: "下次如何改进",
  goals: "目标与方向",
  boundaries: "生活、时间与资金边界",
  principles: "我选择的原则",
  review_rhythm: "怎样回顾与修订",
};
export const fields: Record<string, string[]> = {
  topic: [
    "title",
    "category",
    "question",
    "summary",
    "open_questions",
    "next_step",
  ],
  insight: [
    "title",
    "understanding",
    "boundary",
    "example",
    "source_text",
    "next_step",
  ],
  method: [
    "title",
    "scope",
    "conditions",
    "steps",
    "invalidation",
    "risk",
    "unknowns",
    "next_step",
  ],
  practice: [
    "title",
    "judgment",
    "conditions",
    "invalidation",
    "plan",
    "actual",
    "result",
    "review",
    "lesson",
    "next_step",
  ],
  handbook: [
    "title",
    "goals",
    "boundaries",
    "scope",
    "principles",
    "review_rhythm",
  ],
};
export const stages: Record<string, string[][]> = {
  topic: [
    ["active", "正在推进"],
    ["paused", "暂时放下"],
    ["archived", "归档查阅"],
  ],
  insight: [
    ["draft", "还在理解"],
    ["understood", "已有自己的理解"],
    ["revisit", "值得再想"],
  ],
  method: [
    ["draft", "构思中"],
    ["trial", "观察试用"],
    ["adopted", "我在采用"],
    ["paused", "已暂停"],
  ],
};
export const evidenceNames: Record<string, string> = {
  uncertain: "暂难判断",
  support: "提供支持",
  counter: "出现反例",
};
export function stageName(row: any) {
  return (
    stages[row.kind]?.find((s) => s[0] === row.body.stage)?.[1] ||
    (
      {
        draft: "事前草稿",
        observing: "已锁定，待回顾",
        reviewed: "已复盘",
      } as any
    )[row.body.phase] ||
    "我的选择"
  );
}
export function summary(row: any) {
  return (
    row.body.summary ||
    row.body.understanding ||
    row.body.question ||
    row.body.review ||
    row.body.scope ||
    row.body.goals ||
    "打开后继续完善"
  );
}
export function isGrowth(kind: string) {
  return kind in fields;
}
export function editableFields(row: any) {
  return fields[row.kind].filter(
    (k) =>
      row.kind !== "practice" ||
      (row.body.phase === "draft"
        ? !["actual", "result", "review", "lesson"].includes(k)
        : !["judgment", "conditions", "invalidation", "plan"].includes(k)),
  );
}
export function contextPrompt(row: any) {
  return (
    {
      topic:
        "结合这个专题的已有资料，帮我梳理当前认识和一个最值得继续的问题，提交可审阅的整理建议。",
      insight:
        "帮我消化这张知识笔记。先找出理解中最关键的模糊点，再形成我可以审阅的修改建议。",
      method:
        "检查这套候选方法的适用范围、条件、失效与风险，指出缺口并提交可审阅的修改建议。",
      practice:
        row.body.phase === "draft"
          ? "请检查这份事前观察，指出条件和计划中的缺口。不要补造实际结果。"
          : "对照锁定的事前记录和我提供的实际情况，区分判断、执行和结果，提交复盘建议。",
      handbook:
        "结合我的目标与已有方法，帮我找出交易理念中一个最值得明确的缺口。",
    } as any
  )[row.kind];
}

export function displayTitle(row: any) {
  // Keep the original record intact; only the untouched, generated default has a new display name.
  if (
    row.kind === "handbook" &&
    row.body.title === "我的交易手册" &&
    row.body.curation_key === "curation:handbook:1" &&
    row.body.origin?.status === "pending"
  )
    return "我的交易理念";
  return row.body.title;
}

export function editorDraftKey(
  kind: string,
  row?: any,
  seed?: any,
  refs: string[] = [],
) {
  return (
    "growth-editor-" +
    (row?.id ||
      [
        kind,
        seed?.method_id || "",
        seed?.topic_id || "",
        [...refs].sort().join(",") || "new",
      ].join("-"))
  );
}
export function textMatches(row: any, query: string) {
  const tokens = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const text = (fields[row.kind] || [])
    .map((k) => row.body[k] || "")
    .join("\n")
    .toLocaleLowerCase();
  return tokens.every((t) => text.includes(t));
}
export function mergeDraft(base: any, draft: any, latest: any) {
  const body = { ...latest },
    conflicts: string[] = [];
  for (const k of Object.keys(draft)) {
    if (typeof draft[k] !== "string") continue;
    const before = base[k] || "",
      mine = draft[k] || "",
      current = latest[k] || "";
    if (mine === before) continue;
    if (current !== before && current !== mine) conflicts.push(k);
    body[k] = mine;
  }
  return { body, conflicts };
}
export function editorGroups(kind: string, phase = "draft") {
  const definitions: Record<string, [string, string, string[]][]> = {
    topic: [
      ["从问题开始", "写下现在最想弄清的一件事。", ["question", "summary"]],
      [
        "继续积累",
        "分类和未解问题可以慢慢补。",
        ["category", "open_questions", "next_step"],
      ],
    ],
    insight: [
      [
        "先读懂",
        "用自己的话说明原意，再想想适用边界。",
        ["understanding", "boundary"],
      ],
      [
        "留下依据与例子",
        "保留原文，补一个自己的例子。",
        ["source_text", "example", "next_step"],
      ],
    ],
    method: [
      ["何时使用", "明确参与范围和能观察到的条件。", ["scope", "conditions"]],
      [
        "怎样执行与退出",
        "写清行动、失效条件和风险处理。",
        ["steps", "invalidation", "risk"],
      ],
      ["继续检验", "保留不确定性，留一个下一步。", ["unknowns", "next_step"]],
    ],
    practice:
      phase === "draft"
        ? [
            [
              "留下事前判断",
              "只写当时知道什么，不必为了完整而猜测。",
              ["judgment", "conditions"],
            ],
            [
              "行动与退出",
              "只观察也可以，先说明计划与失效条件。",
              ["plan", "invalidation", "next_step"],
            ],
          ]
        : [
            [
              "回看实际情况",
              "实际行动和结果分开写，原事前记录会保留。",
              ["actual", "result"],
            ],
            [
              "形成复盘",
              "区分判断、执行和结果，留下一次的改进。",
              ["review", "lesson", "next_step"],
            ],
          ],
    handbook: [
      ["目标与边界", "先从自己的生活出发。", ["goals", "boundaries"]],
      ["范围与原则", "说清自己参与什么、选择怎样做。", ["scope", "principles"]],
      ["回顾与修订", "约定一个能坚持的回顾节奏。", ["review_rhythm"]],
    ],
  };
  return (definitions[kind] || []).map(([title, hint, keys]) => ({
    title,
    hint,
    fields: keys,
  }));
}
export const fieldHints: Record<string, string> = {
  question: "比如：我怎样区分机会与一时的情绪？",
  summary: "先写目前能说清的一点，后续可以修订",
  category: "比如：风险管理、执行纪律、市场理解",
  understanding: "这段资料在说什么？用自己的话解释",
  boundary: "它适用于什么情境？什么情况下不能照搬？",
  source_text: "保留关键原文；完整资料也可以在保存后关联",
  example: "写一个亲历或观察到的例子，并说明它是例子还是证据",
  scope: "适用的市场、情境与时间尺度是什么？",
  conditions: "出现哪些可以观察、核对的条件才考虑行动？",
  steps: "把行动写成按顺序可以做的几步",
  invalidation: "出现什么变化，就说明原判断需要停止或重查？",
  risk: "行动前怎样控制损失、仓位或投入？不确定处请明确写出",
  unknowns: "目前还缺什么证据？哪些解释也可能成立？",
  judgment: "当时看见了什么，依据是什么，哪里仍不确定？",
  plan: "这次准备做什么？只观察也请写清观察方式",
  actual: "如实写实际做了什么，与原计划有何不同",
  result: "后来发生了什么？暂未有结果可以稍后补充",
  review: "判断是否有依据？执行是否符合计划？结果受哪些因素影响？",
  lesson: "下一次具体保留或改变哪一步？",
  next_step: "留一个下次可以继续的小问题；也可以留空",
  goals: "交易希望服务于怎样的生活和长期目标？",
  boundaries: "可以投入多少时间和资金？哪些生活安排需要优先保留？",
  principles: "写下自己选择遵循的原则及理由；正式纪律另行审阅",
  review_rhythm: "什么时候回看？遇到什么变化时需要修订？",
  open_questions: "写下还没弄清的地方，允许暂时没有答案",
};
