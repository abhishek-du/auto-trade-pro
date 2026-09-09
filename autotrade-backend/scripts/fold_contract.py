"""The Phase 2 fold contract — manifest-authoritative, immutable, fail-closed.

WHY THIS MODULE EXISTS
----------------------
The fold boundaries used to live as a literal dict in `scripts/phase2r_r1.py`,
and four other Phase 2 scripts imported it from there. On 2026-09-09 a helper
(`autotrade-backend/patch_folds.py`) rewrote that literal with

    re.sub(r"FOLDS = \\{.*?\\}", <replacement>, text, flags=re.DOTALL)

DOTALL made `.*?` span the whole dict, so all four folds were replaced by one,
and the 2025 validation end moved from 2025-12-31 to 2025-12-30. Every script
that imported FOLDS silently inherited that. It was caught only because a run
printed `248 -> 248` where `249 -> 248` was expected.

Two structural defences follow from that:

1. **The manifests are the source of truth**, not a Python literal. They are
   written by the runs themselves and are the only record of what actually
   executed. `phase2r_manifest.json` is authoritative;
   `phase2r1_manifest.json` must agree with it exactly.
2. **No Phase 2 script contains a fold literal any more.** A regex looking for
   `FOLDS = {` in `phase2r_r1.py` now matches nothing, so that exact accident
   cannot recur.

Validation is FAIL-CLOSED and happens at import: a missing, malformed, or
divergent manifest raises rather than returning a plausible-looking default.
A wrong fold is far worse than a crash, because it produces numbers.

The exposed mapping is read-only (`MappingProxyType` over tuples), so a
consumer cannot mutate the contract in memory either.
"""
from __future__ import annotations

import json
import os
from types import MappingProxyType

# repo root -> datasets/v1_baseline
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
DEFAULT_DATASET_DIR = os.path.join(_REPO, "datasets", "v1_baseline")

AUTHORITATIVE_MANIFEST = "phase2r/phase2r_manifest.json"
CROSS_CHECK_MANIFEST = "phase2r1/phase2r1_manifest.json"

# The frozen contract, confirmed against phase2r_manifest.json,
# phase2r1_manifest.json and phase2r_model_results.json. Each fold's validation
# runs to the LAST TRADING SESSION of its year — 2022 and 2023 end on the 29th
# or 30th only because 31 December fell on a weekend.
EXPECTED_FOLDS = {
    2022: ("2017-06-28", "2021-12-30", "2021-12-31", "2022-01-03", "2022-12-30"),
    2023: ("2017-06-28", "2022-12-29", "2022-12-30", "2023-01-02", "2023-12-29"),
    2024: ("2017-06-28", "2023-12-28", "2023-12-29", "2024-01-01", "2024-12-31"),
    2025: ("2017-06-28", "2024-12-30", "2024-12-31", "2025-01-01", "2025-12-31"),
}
FIELDS = ("train_start", "train_end", "purge", "validation_start", "validation_end")
EXPECTED_YEARS = (2022, 2023, 2024, 2025)


class FoldContractError(RuntimeError):
    """Raised when the manifests are missing, malformed, or off-contract."""


def _read(path: str) -> dict:
    if not os.path.exists(path):
        raise FoldContractError(f"fold manifest not found: {path}")
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except json.JSONDecodeError as exc:
        raise FoldContractError(f"fold manifest is not valid JSON: {path}: {exc}") from exc
    folds = doc.get("folds")
    if not isinstance(folds, dict):
        raise FoldContractError(f"manifest has no 'folds' mapping: {path}")
    out = {}
    for k, v in folds.items():
        try:
            year = int(k)
        except (TypeError, ValueError) as exc:
            raise FoldContractError(f"non-integer fold key {k!r} in {path}") from exc
        for field in ("train", "purge", "validation"):
            if field not in v:
                raise FoldContractError(f"fold {year} missing '{field}' in {path}")
        tr, va = v["train"], v["validation"]
        if not (isinstance(tr, list) and len(tr) == 2):
            raise FoldContractError(f"fold {year} 'train' is not a 2-element range in {path}")
        if not (isinstance(va, list) and len(va) == 2):
            raise FoldContractError(f"fold {year} 'validation' is not a 2-element range in {path}")
        out[year] = (tr[0], tr[1], v["purge"], va[0], va[1])
    return out


def load_folds(dataset_dir: str = DEFAULT_DATASET_DIR) -> MappingProxyType:
    """Read, cross-check and validate the fold contract. Fails closed."""
    auth = _read(os.path.join(dataset_dir, AUTHORITATIVE_MANIFEST))
    cross = _read(os.path.join(dataset_dir, CROSS_CHECK_MANIFEST))

    if set(auth) != set(EXPECTED_YEARS):
        raise FoldContractError(
            f"expected folds {sorted(EXPECTED_YEARS)}, manifest has {sorted(auth)}")

    for year in EXPECTED_YEARS:
        if auth[year] != cross[year]:
            diff = [f"{FIELDS[i]}: authoritative={auth[year][i]} cross-check={cross[year][i]}"
                    for i in range(5) if auth[year][i] != cross[year][i]]
            raise FoldContractError(
                f"fold {year} disagrees between manifests: " + "; ".join(diff))
        if auth[year] != EXPECTED_FOLDS[year]:
            diff = [f"{FIELDS[i]}: manifest={auth[year][i]} contract={EXPECTED_FOLDS[year][i]}"
                    for i in range(5) if auth[year][i] != EXPECTED_FOLDS[year][i]]
            raise FoldContractError(
                f"fold {year} is off-contract: " + "; ".join(diff))

    return MappingProxyType({y: auth[y] for y in EXPECTED_YEARS})


#: Read-only {year: (train_start, train_end, purge, validation_start, validation_end)}.
#: Loaded and validated at import — an off-contract manifest stops every Phase 2
#: script before it can produce a number.
FOLDS = load_folds()
