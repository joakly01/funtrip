export interface Block {
  kind: string;
  text?: string;
  rows?: string[][];
  level?: number;
  nodes?: any[];
}
export function inline(text: string): any[] {
  return text
    .split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g)
    .filter(Boolean)
    .map((t) => {
      if (t.startsWith("**") && t.endsWith("**"))
        return {
          name: "strong",
          children: [{ type: "text", text: t.slice(2, -2) }],
        };
      if (t.startsWith("`") && t.endsWith("`"))
        return {
          name: "code",
          children: [{ type: "text", text: t.slice(1, -1) }],
        };
      const link = t.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
      return { type: "text", text: link ? link[1] + "（" + link[2] + "）" : t };
    });
}
export function markdown(source: string): Block[] {
  const lines = source.replace(/\r/g, "").split("\n"),
    out: Block[] = [];
  let code: string[] | null = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("```")) {
      if (code) {
        out.push({ kind: "code", text: code.join("\n") });
        code = null;
      } else code = [];
      continue;
    }
    if (code) {
      code.push(line);
      continue;
    }
    if (!line.trim()) continue;
    if (
      /^\s*\|.+\|\s*$/.test(line) &&
      i + 1 < lines.length &&
      /^[\s|:-]+$/.test(lines[i + 1])
    ) {
      const rows = [line];
      i += 2;
      while (i < lines.length && /^\s*\|.+\|\s*$/.test(lines[i]))
        rows.push(lines[i++]);
      i--;
      out.push({
        kind: "table",
        rows: rows.map((r) =>
          r
            .trim()
            .slice(1, -1)
            .split("|")
            .map((c) => c.trim()),
        ),
      });
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/),
      list = line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)$/),
      text = heading
        ? heading[2]
        : list
          ? "• " + list[1]
          : line.replace(/^>\s?/, "");
    out.push({
      kind: heading ? "heading" : line.startsWith(">") ? "quote" : "paragraph",
      level: heading?.[1].length,
      nodes: inline(text),
    });
  }
  if (code) out.push({ kind: "code", text: code.join("\n") });
  return out;
}
