"""Turn a pytest JUnit XML report into GitHub annotations and a step summary.

Raw job logs need a signed-in GitHub session; annotations (the REST API and the run
page) and the step summary do not, so a failing test is visible without opening the log.

    python .github/scripts/summarize_junit.py pytest.xml
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

MAX_ANNOTATIONS = 10  # GitHub shows at most 10 error annotations per step


def _clean(text: str, limit: int) -> str:
    # Workflow commands are single-line; keep them short and escape the delimiters.
    one_line = " | ".join(line.strip() for line in text.strip().splitlines() if line.strip())[:limit]
    return one_line.replace("%", "%25").replace("\r", "").replace(":", " -").encode("ascii", "replace").decode("ascii")


def main(path: str) -> int:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    failures: list[tuple[str, str, str]] = []
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
        for case in suite.iter("testcase"):
            for kind in ("failure", "error"):
                node = case.find(kind)
                if node is not None:
                    name = f"{case.get('classname', '')}::{case.get('name', '')}"
                    failures.append((name, node.get("message", ""), node.text or ""))

    lines = [f"### pytest: {totals['tests']} tests, {totals['failures']} failed, {totals['errors']} errors, {totals['skipped']} skipped", ""]
    for name, message, body in failures:
        lines += [f"<details><summary><code>{name}</code></summary>", "", "```", (message + "\n" + body)[:4000], "```", "</details>", ""]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf8") as fh:
            fh.write("\n".join(lines) + "\n")

    for name, message, body in failures[:MAX_ANNOTATIONS]:
        print(f"::error title=FAILED {_clean(name, 150)}::{_clean(message + ' ' + body[-600:], 700)}")
    if len(failures) > MAX_ANNOTATIONS:
        print(f"::warning::{len(failures) - MAX_ANNOTATIONS} more failing tests are listed in the job summary")
    print(f"{len(failures)} failing test(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
