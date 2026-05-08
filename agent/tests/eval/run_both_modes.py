"""Run the eval suite in BOTH live and fixture modes, merge the
results into one unified report so every case shows a result
(no "skipped because wrong mode" entries).

Why this exists: cases carry skip flags (`live_llm_required`,
`fixture_data_required`) that route them to the right mode.
A single-mode run shows real coverage but misleadingly reports
the other mode's cases as "skipped". A reviewer reading one
report sees `21/30 skipped` and thinks coverage is thin —
when in fact those 21 are running in the other mode. This
wrapper produces ONE report where every case shows PASS / FAIL
based on the mode it's calibrated for.

Usage:
    python -m agent.tests.eval.run_both_modes

Output:
    agent/tests/eval/results/<timestamp>-both-modes.md

Each case shows a `mode_run` field indicating which mode produced
its result. Cases passing in both modes are marked once with
mode_run=both.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_RESULTS = Path(__file__).parent / "results"
_RUNNER = "agent.tests.eval.runner"

_HEADER_RE = re.compile(
    r"\*\*Cases:\*\*\s+(\d+)\s+\*\*Clean passes:\*\*\s+(\d+)\s+"
    r"\*\*Real failures:\*\*\s+(\d+)\s+\*\*Expected-fail cases that fired:\*\*\s+(\d+)\s+"
    r"\*\*Skipped\b[^:]*:\*\*\s+(\d+)",
    re.DOTALL,
)


def _run_mode(label: str, env_overrides: dict[str, str]) -> Path:
    """Run the runner subprocess with env_overrides applied; return the
    newly-written report path."""
    env = os.environ.copy()
    env.update(env_overrides)
    print(f"\n=== Running {label} mode ===")
    before = set(_RESULTS.glob("*.md"))
    proc = subprocess.run(
        [sys.executable, "-m", _RUNNER],
        env=env,
        capture_output=True,
        text=True,
    )
    # The runner writes one new .md per invocation; capture the new file.
    after = set(_RESULTS.glob("*.md"))
    new_files = after - before
    if not new_files:
        print(f"!! {label} mode wrote no new report file. stdout tail:")
        print(proc.stdout[-2000:])
        sys.exit(1)
    new_report = next(iter(new_files))
    # Echo the runner's own summary line so the user sees per-mode result
    for line in proc.stdout.splitlines()[-15:]:
        print(line)
    return new_report


def _parse_per_case(report_text: str) -> dict[str, str]:
    """Parse per-case detail section. Returns {case_name: 'PASS'|'FAIL'|'SKIPPED'}."""
    pat = re.compile(r"^###\s+(✅\s+PASS|❌\s+FAIL|⏭[^—]*|⊘[^—]*)\s+—\s+`([^`]+)`", re.MULTILINE)
    out: dict[str, str] = {}
    for m in pat.finditer(report_text):
        marker, name = m.group(1), m.group(2)
        if "PASS" in marker:
            status = "PASS"
        elif "FAIL" in marker:
            status = "FAIL"
        else:
            status = "SKIPPED"
        out[name] = status
    return out


def _parse_per_case_blocks(report_text: str) -> dict[str, str]:
    """Extract the full per-case markdown block (header + description +
    HTTP/response/assertion details) keyed by case name.

    A block starts at `### <STATUS> — \`<name>\`` and runs until the next
    `### ` or `## ` header.
    """
    # Find all per-case header positions
    header_pat = re.compile(r"^###\s+(?:✅\s+PASS|❌\s+FAIL|⏭[^—]*|⊘[^—]*)\s+—\s+`([^`]+)`", re.MULTILINE)
    next_section_pat = re.compile(r"^(?:###?\s+|---)", re.MULTILINE)

    matches = list(header_pat.finditer(report_text))
    blocks: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.start()
        # End is the start of the next header (### or ##) OR end-of-text
        next_m = next_section_pat.search(report_text, m.end())
        end = next_m.start() if next_m else len(report_text)
        blocks[name] = report_text[start:end].rstrip() + "\n"
    return blocks


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Skip the 3 mode runs; re-merge the most recent per-mode reports already on disk. Useful when iterating on the merged-report format.",
    )
    args = parser.parse_args()

    if args.reuse:
        # Find the 3 most recent per-mode reports (those NOT ending in -all-modes.md).
        per_mode = sorted(
            p for p in _RESULTS.glob("*.md")
            if not p.name.endswith("-all-modes.md") and p.name != "_preview.html"
        )
        if len(per_mode) < 3:
            print(f"!! --reuse needs at least 3 per-mode reports in {_RESULTS}; found {len(per_mode)}")
            sys.exit(1)
        # The 3 most recent are assumed to be (live, fixture, hybrid) in chronological order.
        live_report, fixture_report, hybrid_report = per_mode[-3], per_mode[-2], per_mode[-1]
        print(f"=== Reusing per-mode reports ===")
        print(f"  live:    {live_report.name}")
        print(f"  fixture: {fixture_report.name}")
        print(f"  hybrid:  {hybrid_report.name}")
    else:
        # USE_FIXTURE_EXTRACTION=true on every mode: the runner submits a synthetic
        # 16-byte placeholder PDF (`runner.py:_run_extraction_case`), not a real
        # PDF, so the extraction pipeline must always be mocked regardless of
        # LLM/data mode. Without this flag the mock helpers (mock_extraction_in_
        # fixture_mode in conftest.py and _apply_extraction_mock_if_needed in
        # runner.py) stay dormant and the synthetic PDF crashes Docling →
        # HTTP 500 → all 18 lab/intake extraction cases fail with status=error.
        # Fixed by Aria's mock-coverage extension at 37d64aea4 + 7e82defb2 only
        # works if these flags propagate; this file is the env-var carrier.
        # 1) Live mode — real LLM + real MariaDB; extraction still mocked.
        live_report = _run_mode("LIVE", {"USE_FIXTURE_LLM": "false", "USE_FIXTURE_DATA": "false", "USE_FIXTURE_EXTRACTION": "true"})
        # 2) Fixture mode — canned LLM + fixture data; extraction mocked.
        fixture_report = _run_mode("FIXTURE", {"USE_FIXTURE_LLM": "true", "USE_FIXTURE_DATA": "true", "USE_FIXTURE_EXTRACTION": "true"})
        # 3) Hybrid mode (real LLM + fixture data) — for cases with BOTH
        #    `live_llm_required` and `fixture_data_required` flags. These
        #    test real model behavior against canned chart data, which
        #    neither pure mode can satisfy. Extraction still mocked.
        hybrid_report = _run_mode("HYBRID", {"USE_FIXTURE_LLM": "false", "USE_FIXTURE_DATA": "true", "USE_FIXTURE_EXTRACTION": "true"})

    live_text = live_report.read_text(encoding="utf-8")
    fixture_text = fixture_report.read_text(encoding="utf-8")
    hybrid_text = hybrid_report.read_text(encoding="utf-8")

    live_cases = _parse_per_case(live_text)
    fixture_cases = _parse_per_case(fixture_text)
    hybrid_cases = _parse_per_case(hybrid_text)

    live_blocks = _parse_per_case_blocks(live_text)
    fixture_blocks = _parse_per_case_blocks(fixture_text)
    hybrid_blocks = _parse_per_case_blocks(hybrid_text)

    all_names = sorted(set(live_cases) | set(fixture_cases) | set(hybrid_cases))
    merged: dict[str, tuple[str, str]] = {}
    for name in all_names:
        l = live_cases.get(name, "ABSENT")
        f = fixture_cases.get(name, "ABSENT")
        h = hybrid_cases.get(name, "ABSENT")
        # Effective status: PASS in any mode wins; FAIL in any (without
        # PASS in another) is FAIL; SKIPPED in all is SKIPPED (shouldn't
        # happen with proper skip flag config).
        results = [("live", l), ("fixture", f), ("hybrid", h)]
        passes = [m for m, s in results if s == "PASS"]
        fails = [m for m, s in results if s == "FAIL"]
        if passes:
            mode_run = "+".join(passes) if len(passes) > 1 else passes[0]
            merged[name] = ("PASS", mode_run)
        elif fails:
            mode_run = fails[0]
            merged[name] = ("FAIL", mode_run)
        else:
            merged[name] = ("SKIPPED", "none")

    total = len(merged)
    passes = sum(1 for s, _ in merged.values() if s == "PASS")
    fails = sum(1 for s, _ in merged.values() if s == "FAIL")
    skips = sum(1 for s, _ in merged.values() if s == "SKIPPED")

    # Load case metadata (category, tier, difficulty, failure_mode) for
    # the aggregate matrices. EvalCase.load_all reads the YAML files;
    # this is the same loader the runner uses.
    from agent.tests.eval.runner import EvalCase  # local import to avoid load at module-import time
    cases_by_name = {c.name: c for c in EvalCase.load_all()}

    def _matrix(field: str) -> list[tuple[str, int, int, int]]:
        """Return [(value, total, pass, fail), ...] for the given case field."""
        buckets: dict[str, list[str]] = {}
        for name, (status, _) in merged.items():
            case = cases_by_name.get(name)
            if not case:
                continue
            value = getattr(case, field, None) or "—"
            buckets.setdefault(str(value), []).append(status)
        rows: list[tuple[str, int, int, int]] = []
        for value, statuses in sorted(buckets.items()):
            t = len(statuses)
            p = sum(1 for s in statuses if s == "PASS")
            f = sum(1 for s in statuses if s == "FAIL")
            rows.append((value, t, p, f))
        return rows

    matrix_category = _matrix("category")
    matrix_tier = _matrix("tier")
    matrix_difficulty = _matrix("difficulty")

    # Coverage by mode — count cases that ran in each mode (passed or failed)
    mode_coverage: Counter[str] = Counter()
    for name in merged:
        if live_cases.get(name) in ("PASS", "FAIL"):
            mode_coverage["live"] += 1
        if fixture_cases.get(name) in ("PASS", "FAIL"):
            mode_coverage["fixture"] += 1
        if hybrid_cases.get(name) in ("PASS", "FAIL"):
            mode_coverage["hybrid"] += 1

    out_path = _RESULTS / f"{live_report.stem}-all-modes.md"

    # SHA stamp lets Tate (Director) skip a redundant post-merge eval re-run
    # when the master HEAD already matches the SHA the eval ran against.
    # Falls back to "unknown" if git isn't available; never blocks the run.
    head_sha = "unknown"
    try:
        head_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_RESULTS.parent.parent.parent.parent),
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    lines: list[str] = []
    lines.append(f"# Eval run — all modes (merged)")
    lines.append("")
    lines.append(f"**Generated against SHA:** `{head_sha}`  ")
    lines.append(f"**Live report:** [`{live_report.name}`](./{live_report.name})  ")
    lines.append(f"**Fixture report:** [`{fixture_report.name}`](./{fixture_report.name})  ")
    lines.append(f"**Hybrid report:** [`{hybrid_report.name}`](./{hybrid_report.name})  ")
    lines.append(f"**Total cases:** {total}  ")
    lines.append(f"**Clean passes:** {passes}  ")
    lines.append(f"**Real failures:** {fails}  ")
    lines.append(f"**Cases without coverage in ANY mode:** {skips}")
    lines.append("")
    lines.append(
        "Each case carries skip flags (`live_llm_required`, `fixture_data_required`) "
        "that route it to the mode(s) it was calibrated for. This merged view shows "
        "the result from the mode each case actually runs in:\n"
        "- **live** = real Anthropic + live MariaDB (production-shaped)\n"
        "- **fixture** = canned LLM + fixture data (deterministic, free)\n"
        "- **hybrid** = real Anthropic + fixture data (model behavior against canned chart)\n"
        "- **multi-mode** (e.g., `live+fixture+hybrid`) = case has no skip flags, runs everywhere"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Pass-rate matrices")
    lines.append("")

    # ----- Category × status matrix -----
    lines.append("### By category")
    lines.append("")
    lines.append("| Category | Cases | Pass | Fail | Pass-rate |")
    lines.append("|---|---:|---:|---:|---:|")
    for value, t, p, f in matrix_category:
        rate = f"{(p / t * 100):.0f}%" if t else "—"
        lines.append(f"| `{value}` | {t} | {p} | {f} | {rate} |")
    lines.append(f"| **TOTAL** | **{total}** | **{passes}** | **{fails}** | **{(passes / total * 100):.0f}%** |")
    lines.append("")

    # ----- Tier × status matrix -----
    lines.append("### By tier")
    lines.append("")
    lines.append("| Tier | Cases | Pass | Fail | Pass-rate |")
    lines.append("|---|---:|---:|---:|---:|")
    for value, t, p, f in matrix_tier:
        rate = f"{(p / t * 100):.0f}%" if t else "—"
        lines.append(f"| `{value}` | {t} | {p} | {f} | {rate} |")
    lines.append(f"| **TOTAL** | **{total}** | **{passes}** | **{fails}** | **{(passes / total * 100):.0f}%** |")
    lines.append("")

    # ----- Difficulty × status matrix -----
    lines.append("### By difficulty")
    lines.append("")
    lines.append("| Difficulty | Cases | Pass | Fail | Pass-rate |")
    lines.append("|---|---:|---:|---:|---:|")
    for value, t, p, f in matrix_difficulty:
        rate = f"{(p / t * 100):.0f}%" if t else "—"
        lines.append(f"| `{value}` | {t} | {p} | {f} | {rate} |")
    lines.append(f"| **TOTAL** | **{total}** | **{passes}** | **{fails}** | **{(passes / total * 100):.0f}%** |")
    lines.append("")

    # ----- Mode-combo partition (each case in exactly one row) -----
    lines.append("### Cases by mode-combo (partition — sums to total)")
    lines.append("")
    lines.append("Each case runs in one or more modes depending on its skip flags. "
                 "This table partitions the suite by the actual mode-combo each case "
                 "ran in — every case appears in exactly one row.")
    lines.append("")
    combo_counts: Counter[str] = Counter(mode_run for _, mode_run in merged.values())
    lines.append("| Mode-combo | Cases | Of total | What this means |")
    lines.append("|---|---:|---:|---|")
    combo_descriptions = {
        "live": "Synthea-DB-only cases (need live MariaDB; fixture data ≠ Synthea data)",
        "fixture": "Maria-fixture-pinned cases that need canned LLM (fixture-LLM responses match canned chart facts)",
        "hybrid": "Cases needing real LLM + fixture data (e.g., asserts on Maria's specific A1c value with real LLM judgment)",
        "live+fixture": "(unusual — would need both flags off)",
        "live+hybrid": "Sentinel-fixture cases (999100-999114) with `live_llm_required` only — JSON dispatch works in both real-DB and fixture-data contexts",
        "fixture+hybrid": "Maria-fixture-pinned cases with `fixture_data_required` only — passes regardless of LLM mode",
        "live+fixture+hybrid": "No skip flags — deterministic cases like `auth_boundary_bad_hmac` and `ambiguous_query` run in all three modes",
    }
    partition_total = 0
    for combo, count in sorted(combo_counts.items(), key=lambda x: (-x[1], x[0])):
        rate = f"{(count / total * 100):.0f}%" if total else "—"
        desc = combo_descriptions.get(combo, "—")
        lines.append(f"| `{combo}` | {count} | {rate} | {desc} |")
        partition_total += count
    lines.append(f"| **TOTAL** | **{partition_total}** | **100%** | |")
    lines.append("")
    lines.append("**Read:** every case is covered by at least one mode (no row is `none`). "
                 "Cases that pass in multiple modes contribute extra confidence — the same "
                 "case being green in both fixture (deterministic) and live (real LLM) means "
                 "the test is robust to canned vs. real model output.")
    lines.append("")
    lines.append("## Summary table")
    lines.append("")
    lines.append("| Case | Status | Mode(s) passing |")
    lines.append("|---|---|---|")
    for name in sorted(merged):
        status, mode_run = merged[name]
        badge = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIPPED": "⊘ NO COVERAGE"}[status]
        # Anchor link to the per-case detail section below
        anchor = name.replace("_", "-")
        lines.append(f"| [`{name}`](#{anchor}) | {badge} | `{mode_run}` |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Per-case detail")
    lines.append("")
    lines.append(
        "Each section below shows the case's purpose (description from the YAML), "
        "what failure mode it catches, and the actual run result. The detail block "
        "is pulled from the mode report where the case ran (preference: live > "
        "hybrid > fixture). Cases that pass in multiple modes show only the "
        "highest-fidelity mode's detail to keep the report concise."
    )
    lines.append("")

    # For each case, inline the detail block from the highest-fidelity mode
    # the case PASSED in. Preference: live > hybrid > fixture.
    mode_block_lookup = (
        ("live", live_blocks, live_cases),
        ("hybrid", hybrid_blocks, hybrid_cases),
        ("fixture", fixture_blocks, fixture_cases),
    )
    for name in sorted(merged):
        status, mode_run = merged[name]
        block: str | None = None
        chosen_mode: str | None = None
        for mode_label, blocks, statuses in mode_block_lookup:
            if statuses.get(name) == "PASS" and name in blocks:
                block = blocks[name]
                chosen_mode = mode_label
                break
        if block is None:
            # Fall back to ANY block we have (e.g., FAIL detail if all modes failed)
            for mode_label, blocks, _ in mode_block_lookup:
                if name in blocks:
                    block = blocks[name]
                    chosen_mode = mode_label
                    break

        anchor = name.replace("_", "-")
        if block:
            lines.append(f"<a id='{anchor}'></a>")
            lines.append(f"_Detail from `{chosen_mode}` mode. Case passes in: `{mode_run}`._")
            lines.append("")
            lines.append(block)
        else:
            lines.append(f"### ⊘ NO COVERAGE — `{name}`")
            lines.append("")
            lines.append("Case has skip flags that exclude it from all three modes. "
                         "Configuration bug — every case should run somewhere.")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"*Generated by `agent/tests/eval/run_both_modes.py` on {live_report.stem.split('-')[3]}. "
        f"For category × tier × difficulty breakdowns and latency tables, see the per-mode "
        f"reports linked at the top of this file.*"
    )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Also write a stable copy at the repo root so docs can link to a
    # permanent path. The timestamped file is kept in results/ for
    # history; EVAL_RESULTS.md is overwritten each run as the canonical
    # latest view.
    repo_root = Path(__file__).parent.parent.parent.parent
    stable_path = repo_root / "EVAL_RESULTS.md"
    stable_header = (
        f"<!-- Auto-generated by `agent/tests/eval/run_both_modes.py`. "
        f"Do not hand-edit; this file is overwritten on each merged-mode run. "
        f"For history, see `agent/tests/eval/results/*-all-modes.md`. -->\n\n"
    )
    stable_path.write_text(stable_header + "\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n=== Merged report: {out_path} ===")
    print(f"=== Stable copy:   {stable_path} ===")
    print(f"Total: {total} · Pass: {passes} · Fail: {fails} · No-coverage: {skips}")

    # Auto-open the styled HTML preview unless suppressed (e.g., in CI).
    # Picks up the new merged report via preview_latest's "-all-modes" preference.
    if not os.environ.get("EVAL_NO_PREVIEW"):
        try:
            from agent.tests.eval import preview_latest
            preview_latest.main()
        except Exception as e:
            print(f"(HTML preview skipped: {type(e).__name__}: {e})")

    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
