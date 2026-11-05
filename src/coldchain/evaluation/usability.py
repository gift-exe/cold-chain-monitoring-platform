"""E9 usability (Table 3.10, Section 3.4.7): merge the evaluators' problem logs.

Each evaluator fills a copy of docs/e9/problem-log.csv and saves it as runs/eval/e9/logs/<initials>.csv.
The researcher then adds a `merged_id` column to every row (the same id for rows describing the same
problem; this is a judgement and is not automated). This script groups rows by merged_id, computes the mean
severity, lists the heuristics and evaluators, and flags every problem with a mean of 3 (major) or more,
which must be fixed and re-checked (NFR8).

    uv run python -m coldchain.evaluation.usability
"""
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[3]
LOGS = ROOT / "runs" / "eval" / "e9" / "logs"
OUT = ROOT / "runs" / "eval" / "e9"


def merge() -> dict:
    groups = defaultdict(list)
    for f in sorted(LOGS.glob("*.csv")):
        with f.open() as fh:
            for row in csv.DictReader(fh):
                if not row.get("merged_id"):
                    raise ValueError(f"{f.name}: every row needs a merged_id ({row.get('problem')!r})")
                groups[row["merged_id"]].append(row)
    problems = []
    for pid, rows in sorted(groups.items()):
        ratings = [int(r["severity"]) for r in rows]
        problems.append({"id": pid, "mean_severity": round(mean(ratings), 2), "ratings": ratings,
                         "evaluators": sorted({r["evaluator"] for r in rows}),
                         "heuristics": sorted({r["heuristic"] for r in rows}),
                         "tasks": sorted({r["task"] for r in rows}), "problem": rows[0]["problem"],
                         "must_fix": mean(ratings) >= 3})
    problems.sort(key=lambda p: -p["mean_severity"])
    doc = {"evaluators": sorted({e for p in problems for e in p["evaluators"]}), "problems": len(problems),
           "must_fix": sum(p["must_fix"] for p in problems), "list": problems}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "merged.json").write_text(json.dumps(doc, indent=2))
    return doc


if __name__ == "__main__":
    d = merge()
    print(f"{d['problems']} problems from {len(d['evaluators'])} evaluators; {d['must_fix']} rated 3 or more")
    for p in d["list"]:
        print(f"{p['id']:>5}  {p['mean_severity']:.2f}  {','.join(p['heuristics']):10} {p['problem'][:80]}")
