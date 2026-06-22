"""Post-close monitor for the Level-4 reflection + shadow-reasoning rollout.

Summarises a day's accrued trade_lessons + reasoning_verdicts, runs the shadow
A/B, writes a dated report to logs/, and (if Telegram is configured) sends a
concise summary. Designed to run locally after NSE close (has DB + .venv access);
a cloud agent cannot, since the data lives only on this machine.

Usage: .venv/bin/python scripts/monitor_lessons.py
"""
import asyncio
import datetime
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def build_report() -> str:
    from db.database import AsyncSessionLocal
    from sqlalchemy import text

    out: list[str] = []
    def w(s=""): out.append(s)

    now = datetime.datetime.utcnow()
    w(f"📊 Lessons/Reasoning Monitor — {now:%Y-%m-%d %H:%M} UTC")
    w("=" * 56)

    async with AsyncSessionLocal() as s:
        # ── 1. trade_lessons today ────────────────────────────────────────────
        try:
            n_today = (await s.execute(text(
                "SELECT count(*) FROM trade_lessons WHERE created_at::date = (now() at time zone 'utc')::date"
            ))).scalar()
            n_all = (await s.execute(text("SELECT count(*) FROM trade_lessons"))).scalar()
            w(f"\n[1] trade_lessons: {n_today} new today | {n_all} total")
            rows = (await s.execute(text(
                "SELECT symbol, strategy, regime, won, r_multiple, lesson "
                "FROM trade_lessons ORDER BY created_at DESC LIMIT 20"
            ))).fetchall()
            for r in rows:
                tag = "W" if r[3] else "L"
                w(f"  [{tag}] {r[0]:13} {str(r[1]):18} {str(r[2]):14} R={r[4]}")
                w(f"      → {r[5]}")
            if not rows:
                w("  (no lessons yet — no trades have closed under reflection)")
        except Exception as exc:
            w(f"  ERROR reading trade_lessons: {exc}")

        # ── 2. reasoning_verdicts today ───────────────────────────────────────
        try:
            v = (await s.execute(text("""
                SELECT mode, llm_verdict, count(*) FROM reasoning_verdicts
                WHERE ts::date = (now() at time zone 'utc')::date
                GROUP BY mode, llm_verdict ORDER BY mode, llm_verdict
            """))).fetchall()
            tot = sum(r[2] for r in v)
            w(f"\n[2] reasoning_verdicts today: {tot}")
            for r in v:
                w(f"  {r[0]:8} {r[1]:5} : {r[2]}")
            if not v:
                w("  (none — gate did not run, or no qualified candidates today)")
        except Exception as exc:
            w(f"  ERROR reading reasoning_verdicts: {exc}")

    # ── 3. shadow A/B ─────────────────────────────────────────────────────────
    w("\n[3] Shadow A/B (last 1d):")
    try:
        res = subprocess.run(
            [sys.executable, "scripts/ab_reasoning_eval.py", "--from-verdicts", "--days", "1"],
            cwd=_ROOT, capture_output=True, text=True, timeout=180,
        )
        body = (res.stdout or "").strip() or (res.stderr or "").strip()
        for line in body.splitlines():
            if any(k in line for k in ("Gemini", "fallback", "429", "backing off")):
                continue
            w("  " + line)
    except Exception as exc:
        w(f"  ERROR running A/B: {exc}")

    # ── 4. anomaly flags ──────────────────────────────────────────────────────
    w("\n[4] Health flags:")
    text_all = "\n".join(out).lower()
    flags = []
    if "error" in text_all:
        flags.append("⚠️ errors present above — investigate")
    if "no lessons yet" in text_all and "none — gate did not run" in text_all:
        flags.append("⚠️ BOTH tables empty today — reasoning may not be firing (check flags/market/worker)")
    if not flags:
        flags.append("✅ no anomalies detected")
    for f in flags:
        w("  " + f)
    return "\n".join(out)


def self_remove_cron(tag: str) -> None:
    """Remove this job's own crontab line (makes it a true one-shot)."""
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if cur.returncode != 0:
            return
        kept = [ln for ln in cur.stdout.splitlines() if tag not in ln]
        p = subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True)
        if p.returncode == 0:
            print(f"[monitor] removed one-shot cron line tagged {tag}")
    except Exception as exc:
        print(f"[monitor] cron self-remove skipped: {exc}")


async def _amain():
    report = await build_report()
    print(report)
    # write dated report file
    logs = os.path.join(_ROOT, "logs")
    os.makedirs(logs, exist_ok=True)
    path = os.path.join(logs, f"lessons_monitor_{datetime.datetime.utcnow():%Y%m%d}.txt")
    with open(path, "w") as f:
        f.write(report)
    print(f"\n[monitor] report saved → {path}")
    # telegram (best-effort)
    try:
        from integrations.telegram_service import send
        head = report if len(report) < 3500 else report[:3500] + "\n…(truncated)"
        await send("<pre>" + head.replace("<", "&lt;").replace(">", "&gt;") + "</pre>")
        print("[monitor] telegram summary sent")
    except Exception as exc:
        print(f"[monitor] telegram skipped: {exc}")


if __name__ == "__main__":
    asyncio.run(_amain())
    if "--oneshot" in sys.argv:
        self_remove_cron("ATP_LESSONS_MONITOR_ONESHOT")
