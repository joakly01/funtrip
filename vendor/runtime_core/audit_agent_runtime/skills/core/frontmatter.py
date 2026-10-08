from __future__ import annotations


def parse_frontmatter(document: str) -> tuple[dict, str]:
    if not document.startswith("---\n"):
        return {}, document

    parts = document.split("\n---\n", maxsplit=1)
    if len(parts) != 2:
        return {}, document

    raw_frontmatter = parts[0][4:]
    content = parts[1]
    parsed: dict[str, object] = {}
    current_list_key: str | None = None

    for raw_line in raw_frontmatter.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("  - ") and current_list_key:
            parsed.setdefault(current_list_key, [])
            parsed[current_list_key].append(line[4:])
            continue

        current_list_key = None
        if ":" not in line:
            continue
        key, value = line.split(":", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if value == "":
            parsed[key] = []
            current_list_key = key
        else:
            parsed[key] = value

    return parsed, content
