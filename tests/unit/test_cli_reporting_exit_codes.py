"""Regression tests for CLI reporting and exit codes (round 20).

Batch 2 of the §9 triage: M65, M66, M67, M68, M69, M70, M71, M74, M75, L39.

Where a fix introduced a pure helper, that helper is tested directly. Where the
defect sits inside a command closure — which builds a runtime and talks to a
store — the test asserts on the command's source, which is weaker but still
fails if the fix is reverted. The M75 path is exercised end to end through the
CLI, because it only touches the filesystem.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from typer.testing import CliRunner

from research_harness.cli.main import app

MAIN = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src"
    / "research_harness"
    / "cli"
    / "main.py"
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# M65 — the newest verification, not the oldest
# ---------------------------------------------------------------------------


class _Status:
    def __init__(self, value: str) -> None:
        self.value = value


class _Payload:
    def __init__(self, proposition_id: str, status: str) -> None:
        self.proposition_id = proposition_id
        self.status = _Status(status)


class _Env:
    def __init__(self, payload: _Payload, created_at: float) -> None:
        self._payload = payload
        self.created_at = created_at
        self.parse_calls = 0

    def parse_payload(self, cls):  # noqa: ANN201
        self.parse_calls += 1
        return self._payload


def test_newest_by_proposition_id_returns_the_newest():
    """M65: `store.list` is ASC, so the first match was the OLDEST record.

    A proposition re-verified after a fix kept reporting the original failed
    attempt.
    """
    from research_harness.cli.main import _newest_by_proposition_id

    old = _Env(_Payload("p1", "failed"), created_at=1.0)
    new = _Env(_Payload("p1", "verified"), created_at=2.0)
    other = _Env(_Payload("p2", "failed"), created_at=3.0)
    got = _newest_by_proposition_id([old, new, other], _Payload)
    assert got["p1"].status.value == "verified", "reported the oldest verification"
    assert got["p2"].status.value == "failed"


def test_newest_by_proposition_id_is_order_independent():
    from research_harness.cli.main import _newest_by_proposition_id

    old = _Env(_Payload("p1", "failed"), created_at=1.0)
    new = _Env(_Payload("p1", "verified"), created_at=2.0)
    assert _newest_by_proposition_id([new, old], _Payload)["p1"].status.value == "verified"


def test_newest_by_proposition_id_parses_each_envelope_once():
    """M65: the old form parsed every envelope twice — condition and value."""
    from research_harness.cli.main import _newest_by_proposition_id

    envs = [_Env(_Payload("p1", "verified"), created_at=float(i)) for i in range(4)]
    _newest_by_proposition_id(envs, _Payload)
    assert [e.parse_calls for e in envs] == [1, 1, 1, 1]


# ---------------------------------------------------------------------------
# M66 — a str compared against provenance links
# ---------------------------------------------------------------------------


def test_enrichment_line_compares_link_ids():
    """M66: `new_id in await store.get_children(...)` was never true.

    get_children returns ProvenanceLink objects, so the membership test
    compared a str against links and the line could never print.
    """
    src = MAIN.read_text()
    # Match the buggy membership test specifically: the fixed code also contains
    # "for link in await store.get_children(ex.plan_id)".
    assert "new_id in await store.get_children(ex.plan_id)" not in src
    assert "link.target_artifact_id == new_id" in src


# ---------------------------------------------------------------------------
# M67 — documents locate processes the whole set
# ---------------------------------------------------------------------------


def test_documents_locate_does_not_truncate_the_set():
    """M67: it processed 10 identities but reported found/total over all."""
    src = MAIN.read_text()
    assert "included_identity_ids[:10]" not in src
    assert "for pi_id in screened_set.included_identity_ids:" in src


# ---------------------------------------------------------------------------
# M68 — unguarded benchmark lookup
# ---------------------------------------------------------------------------


def test_live_quality_run_guards_the_benchmark_lookup():
    """M68: `long_context` is a role elsewhere, so this raised a bare KeyError."""
    src = MAIN.read_text()
    assert "BENCHMARK_BY_ROLE[role]" not in src
    assert "BENCHMARK_BY_ROLE.get(role)" in src


# ---------------------------------------------------------------------------
# M69 — novelty report/gate handle failures like validate does
# ---------------------------------------------------------------------------


def _function(tree: ast.Module, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in cli/main.py")


def _calls_named(fn: ast.AST, attr: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
    ]


@pytest.mark.parametrize("func_name", ["novelty_report", "novelty_gate"])
def test_novelty_report_and_gate_handle_failures(func_name: str):
    """M69: a provider or search failure surfaced as a raw traceback."""
    fn = _function(ast.parse(MAIN.read_text()), func_name)
    calls = _calls_named(fn, "create_report") + _calls_named(fn, "create_gate")
    assert calls, f"no create_report/create_gate call found in {func_name}"
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    for call in calls:
        assert any(call in list(ast.walk(t)) for t in tries), (
            f"{func_name}: a provider call at line {call.lineno} is not in a try block"
        )


# ---------------------------------------------------------------------------
# M70 — the campaign's decisions, not every decision
# ---------------------------------------------------------------------------


def test_campaign_decisions_filters_to_the_campaign():
    """M70: `list_decisions()` returns every decision ever persisted."""
    from research_harness.cli.main import _campaign_decisions

    class _D:
        def __init__(self, id: str) -> None:
            self.id = id

    class _Campaign:
        decision_ids = ["d-2", "d-1"]

    decisions = [_D("d-1"), _D("d-2"), _D("from-an-earlier-campaign")]
    got = _campaign_decisions(decisions, _Campaign())
    assert [d.id for d in got] == ["d-2", "d-1"], "earlier campaigns leak into the table"


def test_campaign_decisions_skips_missing_ids():
    from research_harness.cli.main import _campaign_decisions

    class _D:
        def __init__(self, id: str) -> None:
            self.id = id

    class _Campaign:
        decision_ids = ["d-1", "d-deleted"]

    got = _campaign_decisions([_D("d-1")], _Campaign())
    assert [d.id for d in got] == ["d-1"]


# ---------------------------------------------------------------------------
# M71 — eval run / calibration exit codes
# ---------------------------------------------------------------------------


def test_eval_exit_code_is_non_zero_unless_passed():
    """M71: `eval run` printed the status but always exited 0."""
    from research_harness.cli.main import _eval_exit_code

    assert _eval_exit_code("passed") == 0
    assert _eval_exit_code("failed") == 1
    assert _eval_exit_code("error") == 1


def test_calibration_exit_code_is_non_zero_if_any_audit_fails():
    from research_harness.cli.main import _calibration_exit_code

    assert _calibration_exit_code(["ok", "ok"]) == 0
    assert _calibration_exit_code([]) == 0
    assert _calibration_exit_code(["ok", "defective"]) == 1


# ---------------------------------------------------------------------------
# M74 — no silently swallowed exceptions
# ---------------------------------------------------------------------------


def test_no_bare_pass_exception_handlers():
    """M74: three handlers swallowed everything, hiding dangling references."""
    tree = ast.parse(MAIN.read_text())
    bad = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.ExceptHandler)
        and len(n.body) == 1
        and isinstance(n.body[0], ast.Pass)
    ]
    assert not bad, f"exceptions swallowed with a bare pass at lines {bad}"


# ---------------------------------------------------------------------------
# M75 — session inspect path containment and encoding
# ---------------------------------------------------------------------------


def test_session_inspect_rejects_a_traversal_id(tmp_path: pathlib.Path):
    """M75: session_id was interpolated into a path with no containment check.

    The escaping directory is real and contains a readable events.jsonl, so
    before the fix this command read it and exited 0 — it was not merely
    rejected by accident for lacking the file.
    """
    root = tmp_path / "sessions"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "events.jsonl").write_text('{"leaked": true}\n', encoding="utf-8")

    result = runner.invoke(app, ["session", "inspect", "../outside", "--root", str(root)])
    assert result.exit_code == 1, f"traversal was accepted:\n{result.output}"
    assert "leaked" not in result.output


def test_session_inspect_reads_a_real_session(tmp_path: pathlib.Path):
    root = tmp_path / "sessions"
    session_dir = root / "abc123"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")

    result = runner.invoke(app, ["session", "inspect", "abc123", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert "2 events" in result.output


def test_session_inspect_reads_with_an_explicit_encoding():
    """M75: `read_text()` without `encoding=` depends on the platform locale."""
    src = MAIN.read_text()
    assert 'events_path.read_text(encoding="utf-8")' in src
    assert 'meta_path.read_text(encoding="utf-8")' in src


# ---------------------------------------------------------------------------
# L39 — equilibrium derive exit code
# ---------------------------------------------------------------------------


def test_equilibrium_exit_code():
    """L39: only `not_solvable` exited non-zero; `failed` printed a green tick."""
    from research_harness.cli.main import _equilibrium_exit_code

    assert _equilibrium_exit_code("derived") == 0
    # partially_derived is a real, usable outcome (a partially verified
    # candidate), not a failure — it deliberately stays 0.
    assert _equilibrium_exit_code("partially_derived") == 0
    assert _equilibrium_exit_code("failed") == 1
    assert _equilibrium_exit_code("not_solvable") == 1
