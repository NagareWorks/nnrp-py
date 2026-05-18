from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HUNK_PATTERN = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enforce incremental line coverage for changed production lines.")
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--threshold", type=float, required=True)
    parser.add_argument("--coverage-xml", required=True)
    return parser.parse_args()


def load_coverage(coverage_xml: Path) -> dict[str, dict[int, bool]]:
    tree = ET.parse(coverage_xml)
    root = tree.getroot()

    source_roots = [Path(source.text) for source in root.findall("./sources/source") if source.text]
    if not source_roots:
        raise ValueError(f"coverage xml does not contain any source roots: {coverage_xml}")

    repo_root = Path.cwd().resolve()
    coverage_by_file: dict[str, dict[int, bool]] = {}

    for class_node in root.findall(".//class"):
        filename = class_node.get("filename")
        if not filename:
            continue

        relative_path = None
        for source_root in source_roots:
            candidate = (source_root / filename).resolve()
            try:
                relative_path = candidate.relative_to(repo_root).as_posix()
                break
            except ValueError:
                continue

        if relative_path is None:
            continue

        line_hits: dict[int, bool] = {}
        for line_node in class_node.findall("./lines/line"):
            number_text = line_node.get("number")
            hits_text = line_node.get("hits")
            if number_text is None or hits_text is None:
                continue
            line_hits[int(number_text)] = int(hits_text) > 0

        coverage_by_file[relative_path] = line_hits

    return coverage_by_file


def load_changed_lines(base_sha: str, head_sha: str) -> dict[str, set[int]]:
    command = [
        "git",
        "diff",
        "--unified=0",
        base_sha,
        head_sha,
        "--",
        "src/nnrp",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    changed_lines: dict[str, set[int]] = {}
    current_file: str | None = None
    new_line_number = 0

    for raw_line in result.stdout.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:]
            changed_lines.setdefault(current_file, set())
            continue
        if raw_line.startswith("+++ "):
            current_file = None
            continue
        if raw_line.startswith("@@"):
            match = HUNK_PATTERN.match(raw_line)
            if not match:
                raise ValueError(f"unsupported diff hunk header: {raw_line}")
            new_line_number = int(match.group(1))
            continue
        if current_file is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            changed_lines[current_file].add(new_line_number)
            new_line_number += 1
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        new_line_number += 1

    return changed_lines


def main() -> int:
    args = parse_args()
    if not args.base_sha or not args.head_sha or args.base_sha == "0000000000000000000000000000000000000000":
        print("No comparable git range was provided; skipping incremental coverage gate.")
        return 0

    coverage_by_file = load_coverage(Path(args.coverage_xml))
    changed_lines = load_changed_lines(args.base_sha, args.head_sha)

    executable_total = 0
    covered_total = 0
    uncovered_entries: list[str] = []

    for relative_path, changed_file_lines in changed_lines.items():
        file_coverage = coverage_by_file.get(relative_path)
        if not file_coverage:
            continue

        executable_lines = sorted(line for line in changed_file_lines if line in file_coverage)
        executable_total += len(executable_lines)
        covered_lines = sum(1 for line in executable_lines if file_coverage[line])
        covered_total += covered_lines

        for line in executable_lines:
            if not file_coverage[line]:
                uncovered_entries.append(f"{relative_path}:{line}")

    if executable_total == 0:
        print("No changed executable production lines were found under src/nnrp; skipping incremental coverage gate.")
        return 0

    ratio = covered_total / executable_total * 100.0
    print(f"Incremental coverage: {covered_total}/{executable_total} changed executable lines covered ({ratio:.2f}%).")
    if ratio + 1e-9 < args.threshold:
        print(f"Required incremental coverage threshold: {args.threshold:.2f}%.")
        if uncovered_entries:
            print("Uncovered changed executable lines:")
            for entry in uncovered_entries:
                print(f"- {entry}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
