"""Turn a JUnit XML file (pytest or Playwright) into Markdown for $GITHUB_STEP_SUMMARY.

Usage: python3 junit_summary.py "<title>" <junit.xml>
"""

from __future__ import annotations

import html
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_DETAIL = 3000


def outcome(case: ET.Element) -> tuple[str, ET.Element | None]:
    for tag in ("failure", "error"):
        node = case.find(tag)
        if node is not None:
            return tag, node
    if case.find("skipped") is not None:
        return "skipped", None
    return "passed", None


def render(title: str, path: Path) -> str:
    if not path.exists():
        return f"### {title}\n\nNo results: `{path}` was not written (the run stopped before the tests).\n"
    cases = ET.parse(path).getroot().iter("testcase")
    counts = {"passed": 0, "failure": 0, "error": 0, "skipped": 0}
    seconds = 0.0
    problems: list[tuple[str, str, ET.Element]] = []
    for case in cases:
        kind, node = outcome(case)
        counts[kind] += 1
        seconds += float(case.get("time") or 0)
        if node is not None:
            name = ".".join(part for part in (case.get("classname"), case.get("name")) if part)
            problems.append((kind, name, node))

    total = sum(counts.values())
    status = "✅" if not problems else "❌"
    lines = [
        f"### {status} {title}",
        "",
        "| Total | Passed | Failed | Errors | Skipped | Time |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {total} | {counts['passed']} | {counts['failure']} | {counts['error']} "
        f"| {counts['skipped']} | {seconds:.1f}s |",
        "",
    ]
    for kind, name, node in problems:
        detail = "\n".join(part for part in (node.get("message"), node.text) if part).strip()
        if len(detail) > MAX_DETAIL:
            detail = detail[:MAX_DETAIL] + "\n…"
        lines += [
            f"<details><summary>{kind}: <code>{html.escape(name)}</code></summary>",
            "",
            f"<pre>{html.escape(detail)}</pre>",
            "</details>",
            "",
        ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(sys.argv[1], Path(sys.argv[2])))
