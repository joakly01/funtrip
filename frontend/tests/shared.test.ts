import test from "node:test";
import assert from "node:assert/strict";
import { EventReader } from "../src/services/events";
import { markdown, inline } from "../src/domain/markdown";
import {
  badge,
  classicSections,
  brief,
  kindLabel,
} from "../src/domain/records";
test("中文和 emoji 在任意字节切分后保持完整", () => {
  const data = new TextEncoder().encode(
    "data: " +
      JSON.stringify({
        type: "assistant",
        payload: { text: "依据与边界 🌿" },
      }) +
      "\n\n",
  );
  for (let split = 1; split < data.length; split++) {
    const got: any[] = [];
    const r = new EventReader((e) => got.push(e));
    r.push(data.slice(0, split).buffer);
    r.push(data.slice(split).buffer);
    r.push(null, true);
    assert.equal(got[0].payload.text, "依据与边界 🌿");
    assert.equal(got.length, 1);
  }
});
test("资料文本支持表格、代码和标题，HTML 仅作为文字", () => {
  const blocks = markdown(
    "# 依据\n\n| 条件 | 行动 |\n| --- | --- |\n| 失败 | 回查 |\n\n```\n原话\n```",
  );
  assert.equal(blocks[0].kind, "heading");
  assert.equal(blocks[1].rows?.[1][1], "回查");
  assert.equal(blocks[2].text, "原话");
  assert.deepEqual(inline("<script>test</script>"), [
    { type: "text", text: "<script>test</script>" },
  ]);
});
test("候选认识和普通笔记不能显示为确认纪律", () => {
  const r: any = { kind: "hypothesis", body: {}, states: { rule: "draft" } };
  assert.equal(badge(r).tone, "pending");
  assert.equal(
    badge({ ...r, kind: "note", states: { rule: "not_applicable" } }).tone,
    "neutral",
  );
  assert.equal(
    badge({ ...r, kind: "strategy", states: { rule: "active" } }).tone,
    "confirmed",
  );
});
test("经典详情按原意、理解、边界和来源分层", () => {
  const groups = classicSections({
    content:
      "## 示例\n- **原文**：观点\n- **本项目理解**：理解\n- **边界**：边界\n- **来源**：原书",
  });
  assert.deepEqual(
    groups.map((g) => g.label),
    ["原意与观点", "理解与应用", "适用边界", "来源与关联"],
  );
});
test("成果优先显示摘要，归档不能隐去候选与反例状态", () => {
  const r: any = {
    kind: "hypothesis",
    body: { summary: "一句摘要", text: "长正文" },
    collection: { shelf: "outcomes" },
    states: { evidence: "unverified", rule: "not_applicable" },
  };
  assert.equal(brief(r), "一句摘要");
  assert.equal(kindLabel(r), "候选方法");
  assert.equal(badge(r).tone, "pending");
  assert.equal(
    badge({ ...r, states: { evidence: "contradicted" } }).text,
    "出现反例",
  );
  assert.equal(
    badge({ ...r, states: { evidence: "needs_review", rule: "active" } }).tone,
    "review",
  );
});
import { NativeRecorder } from "../src/platform/native-recorder";
import {
  mergeDraft,
  editorDraftKey,
  textMatches,
  editorGroups,
  fields,
} from "../src/domain/growth";
test("另一端更新与设备草稿按字段合并，真正冲突才需要选择", () => {
  const merged = mergeDraft(
    { title: "原题", understanding: "原理解", boundary: "旧边界" },
    { title: "我的新题", understanding: "我的理解", boundary: "旧边界" },
    { title: "原题", understanding: "另一端理解", boundary: "新边界" },
  );
  assert.equal(merged.body.title, "我的新题");
  assert.equal(merged.body.boundary, "新边界");
  assert.deepEqual(merged.conflicts, ["understanding"]);
  assert.deepEqual(
    mergeDraft(
      { understanding: "原理解" },
      { understanding: "" },
      { understanding: "另一端理解" },
    ).conflicts,
    ["understanding"],
  );
  assert.deepEqual(
    mergeDraft({}, { title: "相同" }, { title: "相同" }).conflicts,
    [],
  );
});
test("不同来源的派生草稿相互独立，搜索不命中内部字段和凭据", () => {
  assert.notEqual(
    editorDraftKey("method", undefined, { topic_id: "same" }, ["note1"]),
    editorDraftKey("method", undefined, { topic_id: "same" }, ["note2"]),
  );
  assert.equal(
    editorDraftKey("method", undefined, {}, ["b", "a"]),
    editorDraftKey("method", undefined, {}, ["a", "b"]),
  );
  const row = {
    kind: "insight",
    body: {
      title: "风险边界",
      understanding: "观察执行",
      origin: { label: "内部信息" },
      sources: [{ id: "abc123" }],
    },
  };
  assert.equal(textMatches(row, "风险 执行"), true);
  assert.equal(textMatches(row, "内部信息"), false);
  assert.equal(textMatches(row, "abc123"), false);
});
test("逐步编辑覆盖全部正文字段，事前与事后记录分开", () => {
  for (const kind of ["topic", "insight", "method", "handbook"])
    assert.deepEqual(
      editorGroups(kind)
        .flatMap((g) => g.fields)
        .sort(),
      fields[kind].filter((k) => k !== "title").sort(),
    );
  assert.equal(
    editorGroups("practice").some((g) => g.fields.includes("actual")),
    false,
  );
  assert.equal(
    editorGroups("practice", "observing").some((g) =>
      g.fields.includes("judgment"),
    ),
    false,
  );
});
test("取消录音后迟到的完成回调不会上传，正常完成只提交一次", () => {
  let start: any, stop: any;
  const uploaded: any[] = [];
  const manager = {
    onStart: (f: any) => (start = f),
    onStop: (f: any) => (stop = f),
    onError: () => {},
    start: () => {},
    stop: () => {},
  };
  const voice = new NativeRecorder({
    manager,
    onState: () => {},
    onComplete: (f: any) => uploaded.push(f),
    onError: () => {},
    now: () => 1000,
    setTimer: () => 1,
    clearTimer: () => {},
  });
  voice.start();
  start();
  voice.cancel();
  stop({ tempFilePath: "test.mp3", fileSize: 32, duration: 1200 });
  assert.equal(uploaded.length, 0);
  voice.start();
  start();
  voice.finish();
  stop({ tempFilePath: "test.mp3", fileSize: 32, duration: 1200 });
  assert.equal(uploaded.length, 1);
  voice.dispose();
});
