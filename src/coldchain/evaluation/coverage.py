"""E12 functional coverage (Table 3.10): the proportion of functional requirements with a passing test.

Runs the whole test suite (the development stack must be running) and scores tests/requirements.toml: a
requirement is covered when it has at least one test and every test listed for it passed. Requirements
marked dropped or partial are reported with their reason and are not counted as covered.
"""
import subprocess
import tomllib
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAP = ROOT / "tests" / "requirements.toml"
OUT = ROOT / "runs" / "eval" / "e12"


def run_tests() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    junit = OUT / "junit.xml"
    subprocess.run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
                   check=False, cwd=ROOT)   # failures are part of the result
    return junit


def outcomes(junit: Path) -> dict[str, str]:
    """'file.py::test_name' -> passed | failed | skipped (a parametrised test fails if any case fails)."""
    result: dict[str, str] = defaultdict(lambda: "passed")
    for case in ET.parse(junit).getroot().iter("testcase"):
        module = case.get("classname", "").split(".")
        file = next((m for m in module if m.startswith("test_")), module[-1]) + ".py"
        key = f"{file}::{case.get('name', '').split('[')[0]}"
        if case.find("failure") is not None or case.find("error") is not None:
            result[key] = "failed"
        elif case.find("skipped") is not None and result.get(key) != "failed":
            result[key] = "skipped"
        else:
            result.setdefault(key, "passed")
    return dict(result)


def score(results: dict[str, str]) -> dict:
    spec = tomllib.loads(MAP.read_text())
    rows = []
    for key, req in spec.items():
        fr = key.replace("_", ".")
        tests = {t: results.get(t, "missing") for t in req["tests"]}
        status = req.get("status", "")
        covered = bool(tests) and all(v == "passed" for v in tests.values()) and not status
        rows.append({"requirement": fr, "covered": covered, "status": status, "tests": tests})
    in_scope = [r for r in rows if not r["status"].startswith("dropped")]
    covered = sum(r["covered"] for r in in_scope)
    return {"requirements": len(rows), "in_scope": len(in_scope), "covered": covered,
            "coverage": round(covered / len(in_scope), 4), "criterion_100pct": covered == len(in_scope),
            "rows": rows}
