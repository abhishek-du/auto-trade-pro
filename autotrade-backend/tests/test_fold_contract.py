"""The Phase 2 fold contract — static and structural tests only.

No model is trained, no evaluation is run, no dataset is read beyond the
manifests. These tests exist because on 2026-09-09 a DOTALL regex in
`autotrade-backend/patch_folds.py` replaced the whole fold literal in
`scripts/phase2r_r1.py`, dropping three of four folds and moving the 2025
boundary by one session — and four other scripts inherited it silently.

The defences these tests pin down:
  * the manifests, not a literal, are the source of truth
  * the two manifests must agree with each other
  * the contract is validated at import and fails closed
  * no Phase 2 consumer imports FOLDS from the mutable module any more
  * no Phase 2 script contains a fold literal for a regex to find
  * nothing imports or executes patch_folds.py or run_correction.sh
"""
from __future__ import annotations

import ast
import json
import os

import pytest

from scripts import fold_contract as FC

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts")
CONSUMERS = ["phase2r_r1.py", "phase2r1_liquidity.py", "phase2s_economics.py",
             "phase2s_execution_gap.py", "phase2t0_reversal.py"]

AUTHORITATIVE = {
    2022: ("2017-06-28", "2021-12-30", "2021-12-31", "2022-01-03", "2022-12-30"),
    2023: ("2017-06-28", "2022-12-29", "2022-12-30", "2023-01-02", "2023-12-29"),
    2024: ("2017-06-28", "2023-12-28", "2023-12-29", "2024-01-01", "2024-12-31"),
    2025: ("2017-06-28", "2024-12-30", "2024-12-31", "2025-01-01", "2025-12-31"),
}


def _src(name):
    return open(os.path.join(SCRIPTS, name)).read()


def _imports_of(name, module):
    """Names imported from `module` in `name`, via the parsed AST."""
    out = set()
    for n in ast.walk(ast.parse(_src(name))):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.endswith(module):
            out.update(a.name for a in n.names)
    return out


# ── 1. four folds load, and match the manifests exactly ─────────────────────

class TestContractLoads:

    def test_exactly_four_folds(self):
        assert set(FC.FOLDS) == {2022, 2023, 2024, 2025}

    def test_boundaries_match_the_authoritative_contract(self):
        for year, want in AUTHORITATIVE.items():
            assert FC.FOLDS[year] == want, f"fold {year}"

    def test_2024_ends_on_the_last_session_of_2024(self):
        """The field the remediation brief had as a typo. 2024-12-31 was a
        Tuesday and is a real session; 2022/2023 end earlier only because
        31 December fell on a weekend."""
        assert FC.FOLDS[2024][4] == "2024-12-31"
        assert FC.FOLDS[2022][4] == "2022-12-30"
        assert FC.FOLDS[2023][4] == "2023-12-29"
        assert FC.FOLDS[2025][4] == "2025-12-31"

    def test_folds_come_from_the_manifest_not_a_literal(self):
        man = json.load(open(os.path.join(FC.DEFAULT_DATASET_DIR,
                                          FC.AUTHORITATIVE_MANIFEST)))["folds"]
        for year, v in FC.FOLDS.items():
            m = man[str(year)]
            assert (m["train"][0], m["train"][1], m["purge"],
                    m["validation"][0], m["validation"][1]) == v

    def test_the_two_manifests_agree(self):
        a = json.load(open(os.path.join(FC.DEFAULT_DATASET_DIR,
                                        FC.AUTHORITATIVE_MANIFEST)))["folds"]
        b = json.load(open(os.path.join(FC.DEFAULT_DATASET_DIR,
                                        FC.CROSS_CHECK_MANIFEST)))["folds"]
        for y in a:
            assert a[y]["train"] == b[y]["train"]
            assert a[y]["purge"] == b[y]["purge"]
            assert a[y]["validation"] == b[y]["validation"]

    def test_exposed_mapping_is_read_only(self):
        with pytest.raises(TypeError):
            FC.FOLDS[2026] = ("x", "x", "x", "x", "x")
        with pytest.raises(TypeError):
            FC.FOLDS[2025] = ("x", "x", "x", "x", "x")


# ── 2. fail-closed behaviour ────────────────────────────────────────────────

class TestFailsClosed:

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FC.FoldContractError, match="not found"):
            FC.load_folds(str(tmp_path))

    def test_malformed_json_raises(self, tmp_path):
        p = tmp_path / "phase2r"
        p.mkdir()
        (p / "phase2r_manifest.json").write_text("{not json")
        with pytest.raises(FC.FoldContractError, match="not valid JSON"):
            FC.load_folds(str(tmp_path))

    def test_missing_folds_key_raises(self, tmp_path):
        p = tmp_path / "phase2r"
        p.mkdir()
        (p / "phase2r_manifest.json").write_text('{"outputs": []}')
        with pytest.raises(FC.FoldContractError, match="no 'folds' mapping"):
            FC.load_folds(str(tmp_path))

    def _write(self, tmp_path, folds_a, folds_b=None):
        for sub, name, f in (("phase2r", "phase2r_manifest.json", folds_a),
                             ("phase2r1", "phase2r1_manifest.json",
                              folds_b if folds_b is not None else folds_a)):
            d = tmp_path / sub
            d.mkdir(exist_ok=True)
            (d / name).write_text(json.dumps({"folds": f}))

    def _folds(self, **override):
        out = {}
        for y, v in AUTHORITATIVE.items():
            out[str(y)] = {"train": [v[0], v[1]], "purge": v[2],
                           "validation": [v[3], v[4]]}
        out.update(override)
        return out

    def test_wrong_fold_count_raises(self, tmp_path):
        f = self._folds()
        del f["2022"]
        self._write(tmp_path, f)
        with pytest.raises(FC.FoldContractError, match="expected folds"):
            FC.load_folds(str(tmp_path))

    def test_off_contract_boundary_raises(self, tmp_path):
        """The exact mutation that occurred: 2025 validation moved to 12-30."""
        f = self._folds()
        f["2025"]["validation"][1] = "2025-12-30"
        self._write(tmp_path, f)
        with pytest.raises(FC.FoldContractError, match="off-contract"):
            FC.load_folds(str(tmp_path))

    def test_manifests_disagreeing_raises(self, tmp_path):
        a = self._folds()
        b = self._folds()
        b["2024"]["validation"][1] = "2024-12-30"
        self._write(tmp_path, a, b)
        with pytest.raises(FC.FoldContractError, match="disagrees between manifests"):
            FC.load_folds(str(tmp_path))

    def test_malformed_range_raises(self, tmp_path):
        f = self._folds()
        f["2023"]["validation"] = ["2023-01-02"]
        self._write(tmp_path, f)
        with pytest.raises(FC.FoldContractError, match="2-element range"):
            FC.load_folds(str(tmp_path))


# ── 3. every consumer takes FOLDS from the contract ─────────────────────────

class TestConsumersMigrated:

    @pytest.mark.parametrize("name", CONSUMERS)
    def test_imports_folds_from_the_contract(self, name):
        assert "FOLDS" in _imports_of(name, "fold_contract"), name

    @pytest.mark.parametrize("name", CONSUMERS)
    def test_does_not_import_folds_from_the_mutable_module(self, name):
        assert "FOLDS" not in _imports_of(name, "phase2r_r1"), name

    @pytest.mark.parametrize("name", CONSUMERS)
    def test_contains_no_fold_literal(self, name):
        """A `FOLDS = {...}` literal is what the DOTALL regex matched. With no
        literal anywhere, that exact accident cannot recur."""
        for n in ast.walk(ast.parse(_src(name))):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    assert getattr(t, "id", None) != "FOLDS", \
                        f"{name} defines a FOLDS literal again"

    def test_all_five_consumers_are_covered(self):
        assert len(CONSUMERS) == 5


# ── 4. the mutation hazard cannot run ───────────────────────────────────────

class TestMutationHazardContained:

    @pytest.mark.parametrize("name", CONSUMERS + ["fold_contract.py"])
    def test_nothing_imports_or_execs_the_patcher(self, name):
        src = _src(name)
        for banned in ("patch_folds", "run_correction"):
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    names = ([a.name for a in n.names] +
                             ([n.module] if isinstance(n, ast.ImportFrom) and n.module else []))
                    assert not any(banned in (x or "") for x in names), \
                        f"{name} imports {banned}"
            # not invoked through a shell either
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, ast.Constant) and isinstance(n.value, str):
                    assert banned not in n.value or "#" in src, name

    def test_patcher_regex_would_now_match_nothing(self):
        """`re.sub(r"FOLDS = \\{.*?\\}", ...)` against phase2r_r1.py."""
        import re
        assert re.search(r"FOLDS = \{.*?\}", _src("phase2r_r1.py"), re.DOTALL) is None


# ── 5. firewall semantics unchanged ─────────────────────────────────────────

class TestFirewallUnchanged:

    def test_held_out_year_is_still_2026(self):
        from scripts.phase2r_r1 import HELD_OUT_YEAR
        assert HELD_OUT_YEAR == 2026

    @pytest.mark.parametrize("name", CONSUMERS)
    def test_consumers_still_reference_the_firewall(self, name):
        src = _src(name)
        assert "HELD_OUT_YEAR" in src or "HARD_MAX_DATE" in src, name

    def test_hard_max_date_unchanged(self):
        from scripts.phase2t0_reversal import HARD_MAX_DATE
        import datetime as dt
        assert HARD_MAX_DATE == dt.date(2025, 12, 31)

    def test_no_fold_validation_reaches_2026(self):
        for year, v in FC.FOLDS.items():
            assert v[4] < "2026-01-01", f"fold {year} validation ends {v[4]}"
