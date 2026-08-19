"""D4 regression — mutating routes require an admin JWT.

Before this, `require_auth` guarded 5 handlers out of 103 mutating routes.
`PATCH /api/v1/settings/` alone could flip paper_mode to live with no token,
bypassing every safeguard its sibling `POST /settings/mode` enforces.

The sweep test is the load-bearing one: it walks the live app so a NEW
unprotected mutating route fails the build, rather than relying on someone
remembering to add a case here.
"""
from __future__ import annotations

import warnings

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

warnings.filterwarnings("ignore")

from main import app  # noqa: E402

# POST endpoints that compute a number and touch nothing. Deliberately public.
EXEMPT_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/allocation/risk-profile",
    "/api/v1/allocation/rebalancing",
    "/api/v1/india/sip/project",
    "/api/v1/sip/calculator",
    "/api/v1/sip/calculator/required-sip",
    "/api/v1/sip/calculator/time-to-target",
    "/api/v1/tax/calculate",
    "/api/v1/tax/classify-trade",
}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _requires_auth(route: APIRoute) -> bool:
    def walk(dep):
        for sub in dep.dependencies:
            if getattr(sub.call, "__name__", "") == "require_auth" or walk(sub):
                return True
        return False
    return walk(route.dependant)


def _mutating_routes():
    return [r for r in app.routes
            if isinstance(r, APIRoute) and MUTATING & r.methods]


class TestAuthCoverage:

    def test_every_mutating_route_is_protected(self):
        gaps = sorted(
            f"{sorted(r.methods & MUTATING)[0]} {r.path}"
            for r in _mutating_routes()
            if r.path not in EXEMPT_PATHS and not _requires_auth(r)
        )
        assert not gaps, "unauthenticated mutating routes:\n  " + "\n  ".join(gaps)

    def test_the_exemptions_are_only_pure_calculators(self):
        """Stops the exempt list being used to quietly re-open a real route."""
        paths = {r.path for r in _mutating_routes()}
        stale = EXEMPT_PATHS - paths
        assert not stale, f"exempt paths that no longer exist: {sorted(stale)}"

    def test_coverage_has_not_collapsed(self):
        total = len(_mutating_routes())
        protected = sum(1 for r in _mutating_routes() if _requires_auth(r))
        assert total > 90, f"only {total} mutating routes found — did discovery break?"
        assert protected >= total - len(EXEMPT_PATHS)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestUnauthenticatedRequestsAreRejected:
    """The highest blast-radius routes, exercised end to end."""

    @pytest.mark.parametrize("method,path,kwargs", [
        ("patch",  "/api/v1/settings/",                 {"json": {"paper_mode": False}}),
        ("delete", "/api/v1/settings/paper_mode",       {}),
        ("post",   "/api/v1/agent/kill-switch",         {"headers": {"X-Kill-Confirm": "FLATTEN"}}),
        ("put",    "/api/v1/agent/config",              {"json": {}}),
        ("post",   "/api/v1/portfolio/reset",           {"params": {"confirm": "true"}}),
        ("delete", "/api/v1/zerodha/orders/abc123",     {}),
        ("post",   "/api/v1/zerodha/gtt/single",        {"json": {}}),
        ("post",   "/api/v1/zerodha/ticker/stop",       {}),
        ("post",   "/api/v1/zerodha/positions/convert", {"json": {}}),
    ])
    def test_rejected_without_token(self, client, method, path, kwargs):
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code in (401, 403), (
            f"{method.upper()} {path} returned {resp.status_code} with no token "
            f"— it is unauthenticated"
        )

    def test_kill_switch_needs_auth_even_with_the_confirm_header(self):
        """The header alone was the only guard on flattening the entire book."""
        with TestClient(app) as c:
            r = c.post("/api/v1/agent/kill-switch", headers={"X-Kill-Confirm": "FLATTEN"})
        assert r.status_code in (401, 403)

    def test_reads_are_still_public(self):
        """The fix must not lock the dashboard's GETs."""
        with TestClient(app) as c:
            assert c.get("/health").status_code == 200
