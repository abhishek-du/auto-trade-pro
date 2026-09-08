"""STEP 2M §7 — training may never consume a session after its own cutoff.

Phase 2L found `ml_predictor.train_all_models` reading daily bars with no
`as_of`: the reader's default is a property of WHEN THE CODE RUNS, and a
training cutoff has to be a property of the DATA. Run the same job twice a week
apart and the same historical row is built from two different information sets.

These tests hold the cutoff to the contract at three levels: the reader drops
post-cutoff sessions, the trainer passes the cutoff it computed rather than
letting the reader default, and live inference — which owns no reader — is
untouched by any of it.

Real-DB tests skip under pytest (DATABASE_URL points at the empty
`autotrade_test`); the reader is exercised here against a synthetic series.
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect

import pytest

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# ── a synthetic candle table ─────────────────────────────────────────────────

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Answers the two queries daily_series issues, from an in-memory series.

    `rows` are (timestamp, open, high, low, close, volume) at 03:45 canonical.
    """

    def __init__(self, rows):
        self.rows = rows
        self.queries: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.queries.append(sql)
        params = params or {}
        if "GROUP BY" in sql:                       # _preferred_times coverage
            latest = max(r[0] for r in self.rows).date()
            return _Result([("03:45", latest)])
        keep = [r for r in self.rows
                if r[0].strftime("%H:%M") in set(params.get("t", ()))]
        lo, hi = params.get("lo"), params.get("hi")
        if lo is not None:
            keep = [r for r in keep if lo <= r[0] <= hi]
        if "close FROM candles" in sql or "timestamp, close" in sql:
            keep.sort(key=lambda r: r[0], reverse=True)
            keep = keep[: params.get("n", len(keep))]
            return _Result([(r[0], r[4]) for r in keep])
        keep.sort(key=lambda r: r[0])
        return _Result(keep)


def _series(start: dt.date, n: int) -> list[tuple]:
    """n consecutive WEEKDAY sessions at canonical 03:45, close = day number."""
    out, d, i = [], start, 0
    while len(out) < n:
        if d.weekday() < 5:
            i += 1
            ts = dt.datetime.combine(d, dt.time(3, 45))
            out.append((ts, float(i), float(i) + 1, float(i) - 1, float(i), 1000.0))
        d += dt.timedelta(days=1)
    return out


# ── 1. the reader honours the cutoff ─────────────────────────────────────────

class TestReaderCutoff:

    @pytest.mark.asyncio
    async def test_bars_stop_at_as_of(self):
        from engine.daily_series import session_bars
        rows = _series(dt.date(2026, 1, 5), 40)
        sess = _FakeSession(rows)
        cutoff = rows[19][0].date()
        bars = await session_bars("X.NS", dt.date(2026, 1, 1), dt.date(2026, 6, 1),
                                  sess, as_of=cutoff)
        assert bars, "series must not resolve empty"
        assert max(b[0] for b in bars) <= cutoff

    @pytest.mark.asyncio
    async def test_d_plus_1_is_never_visible(self):
        """Rule 1 of §12: a training row for D cannot see D+1."""
        from engine.daily_series import session_bars
        rows = _series(dt.date(2026, 1, 5), 40)
        sess = _FakeSession(rows)
        d = rows[19][0].date()
        d_plus_1 = rows[20][0].date()
        bars = await session_bars("X.NS", dt.date(2026, 1, 1), dt.date(2026, 6, 1),
                                  sess, as_of=d)
        assert d_plus_1 not in {b[0] for b in bars}

    @pytest.mark.asyncio
    async def test_d_plus_2_is_never_visible(self):
        """Rule 2 of §12 — the next-but-one session is no more available."""
        from engine.daily_series import session_bars
        rows = _series(dt.date(2026, 1, 5), 40)
        sess = _FakeSession(rows)
        d = rows[19][0].date()
        d_plus_2 = rows[21][0].date()
        bars = await session_bars("X.NS", dt.date(2026, 1, 1), dt.date(2026, 6, 1),
                                  sess, as_of=d)
        assert d_plus_2 not in {b[0] for b in bars}

    @pytest.mark.asyncio
    async def test_weekend_cutoff_resolves_backwards(self):
        """Rule 3 of §12: a weekend D resolves to the PREVIOUS valid session,
        never forward into Monday."""
        from engine.daily_series import session_bars
        rows = _series(dt.date(2026, 1, 5), 40)
        sess = _FakeSession(rows)
        sat = dt.date(2026, 1, 17)                       # Saturday
        bars = await session_bars("X.NS", dt.date(2026, 1, 1), dt.date(2026, 6, 1),
                                  sess, as_of=sat)
        assert max(b[0] for b in bars) == dt.date(2026, 1, 16)   # Friday

    @pytest.mark.asyncio
    async def test_cutoff_is_inclusive_of_its_own_session(self):
        from engine.daily_series import session_bars
        rows = _series(dt.date(2026, 1, 5), 40)
        sess = _FakeSession(rows)
        d = rows[19][0].date()
        bars = await session_bars("X.NS", dt.date(2026, 1, 1), dt.date(2026, 6, 1),
                                  sess, as_of=d)
        assert max(b[0] for b in bars) == d

    @pytest.mark.asyncio
    async def test_two_cutoffs_differ_only_by_the_tail(self):
        """The same historical row must be identical whenever it is rebuilt —
        the prefix is byte-identical, only the tail extends."""
        from engine.daily_series import session_bars
        rows = _series(dt.date(2026, 1, 5), 40)
        early = await session_bars("X.NS", dt.date(2026, 1, 1), dt.date(2026, 6, 1),
                                   _FakeSession(rows), as_of=rows[19][0].date())
        late = await session_bars("X.NS", dt.date(2026, 1, 1), dt.date(2026, 6, 1),
                                  _FakeSession(rows), as_of=rows[29][0].date())
        assert late[: len(early)] == early

    @pytest.mark.asyncio
    async def test_no_as_of_sees_the_whole_series(self):
        """The failure Phase 2L named: without a cutoff the reader hands back
        everything it has, including sessions after the training row."""
        from engine.daily_series import session_bars
        rows = _series(dt.date(2026, 1, 5), 40)
        bars = await session_bars("X.NS", dt.date(2026, 1, 1), dt.date(2026, 6, 1),
                                  _FakeSession(rows))
        assert max(b[0] for b in bars) == rows[-1][0].date()


# ── 2. the trainer passes its cutoff explicitly ──────────────────────────────

class TestTrainerPassesCutoff:

    def test_signature_accepts_as_of(self):
        from engine.ml_predictor import train_all_models
        p = inspect.signature(train_all_models).parameters
        assert "as_of" in p
        assert p["as_of"].kind is inspect.Parameter.KEYWORD_ONLY
        assert p["as_of"].default is None

    def test_session_bars_is_called_with_as_of(self):
        """Parsed, not grepped — the docstring above it names the query it
        replaced, so a substring search matches the prose as well as the code."""
        from engine.ml_predictor import train_all_models
        tree = ast.parse(inspect.getsource(train_all_models))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "session_bars"]
        assert calls, "train_all_models no longer reads through session_bars"
        for c in calls:
            kw = {k.arg for k in c.keywords}
            assert "as_of" in kw, "reader called without an explicit cutoff"

    def test_no_direct_candles_query(self):
        """§6: do NOT bypass the reader with a direct `candles` query."""
        from engine.ml_predictor import train_all_models
        tree = ast.parse(inspect.getsource(train_all_models))
        sql = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and "FROM candles" in n.value.upper()]
        assert not sql, f"direct candle SQL in the training path: {sql}"

    @pytest.mark.asyncio
    async def test_explicit_as_of_reaches_the_reader(self, monkeypatch):
        import engine.daily_series as ds
        import engine.ml_predictor as mp

        seen = {}

        async def _spy(symbol, start, end, session, **kw):
            seen["end"] = end
            seen["as_of"] = kw.get("as_of")
            return []

        monkeypatch.setattr(ds, "session_bars", _spy)
        cutoff = dt.date(2026, 3, 10)
        await mp.train_all_models(_FakeSession([]), as_of=cutoff)
        assert seen.get("as_of") == cutoff
        assert seen.get("end") == cutoff, "window end must equal the cutoff"

    @pytest.mark.asyncio
    async def test_default_cutoff_excludes_an_open_session(self, monkeypatch):
        """No `as_of` given: the default must be the last COMPLETED session,
        never today's half-formed bar."""
        import engine.daily_series as ds
        import engine.ml_predictor as mp

        seen = {}

        async def _spy(symbol, start, end, session, **kw):
            seen["as_of"] = kw.get("as_of")
            return []

        monkeypatch.setattr(ds, "session_bars", _spy)
        monkeypatch.setattr(ds, "current_open_session", lambda **kw: dt.date.today())
        await mp.train_all_models(_FakeSession([]))
        assert seen.get("as_of") == dt.date.today() - dt.timedelta(days=1)

    @pytest.mark.asyncio
    async def test_default_cutoff_is_today_when_market_is_closed(self, monkeypatch):
        import engine.daily_series as ds
        import engine.ml_predictor as mp

        seen = {}

        async def _spy(symbol, start, end, session, **kw):
            seen["as_of"] = kw.get("as_of")
            return []

        monkeypatch.setattr(ds, "session_bars", _spy)
        monkeypatch.setattr(ds, "current_open_session", lambda **kw: None)
        await mp.train_all_models(_FakeSession([]))
        assert seen.get("as_of") == dt.date.today()


# ── 3. live inference is a different contract ────────────────────────────────

class TestLiveInferenceUnaffected:

    def test_predict_direction_owns_no_reader(self):
        """§7 requires training and live inference to stay distinguishable.
        predict_direction is handed its DataFrame, so a training cutoff cannot
        leak into a live call — and a live call cannot be starved by one."""
        from engine.ml_predictor import predict_direction
        src = inspect.getsource(predict_direction)
        tree = ast.parse(src)
        names = {getattr(n.func, "id", getattr(n.func, "attr", None))
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "session_bars" not in names
        assert "session_closes" not in names

    def test_predict_direction_takes_no_as_of(self):
        from engine.ml_predictor import predict_direction
        assert "as_of" not in inspect.signature(predict_direction).parameters
