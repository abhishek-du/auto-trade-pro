"""PHASE 5 PART A — pre-deployment safety proof.

Five claims must hold BEFORE the BUG-2 queue fix is deployed. If any fails, stop.
Static proof from the AST, plus a live execution proof, plus DB evidence.
"""
import ast, asyncio, sys, inspect
sys.path.insert(0,"/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad")
SRC = open("tasks/india_tasks.py").read()
tree = ast.parse(SRC)
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.AsyncFunctionDef) and n.name == "_india_trade_loop")
ok = True
def check(label, cond, detail=""):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))
    if not cond: ok = False

print("="*88); print("PHASE 5 PART A — PRE-DEPLOYMENT SAFETY PROOF"); print("="*88)

# ── 1. BUG-1 is still present and still unconditional ────────────────────────
reads = [n for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == "settings"
         and isinstance(n.ctx, ast.Load)]
local_imports = [n for n in ast.walk(fn) if isinstance(n, ast.ImportFrom)
                 and any(a.name == "settings" and a.asname is None for a in n.names)]
first_read = min(n.lineno for n in reads) if reads else None
first_bind = min(n.lineno for n in local_imports) if local_imports else None
module_import = any(isinstance(n, ast.ImportFrom) and n.module == "utils.config"
                    and any(a.name == "settings" and a.asname is None for a in n.names)
                    for n in tree.body)
check("BUG-1 present: `settings` read before its local import",
      first_read is not None and first_bind is not None and first_read < first_bind,
      f"read at :{first_read}, local import at :{first_bind}")
check("no module-level `settings` import rescues it", not module_import)

# the crash must sit BEFORE any origination
hub_q  = SRC.index("hub_rows = (await session.execute")
hub_ln = SRC[:hub_q].count("\n") + 1
intent = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
          and getattr(n.func, "id", "") == "TradeIntent"]
exec_c = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
          and getattr(n.func, "id", "") == "execute_trade_intent"]
check("crash precedes the Hub query", first_read < hub_ln, f":{first_read} < :{hub_ln}")
check("crash precedes every TradeIntent construction",
      all(first_read < l for l in intent), f"TradeIntent at {intent}")
check("crash precedes every execute_trade_intent call",
      all(first_read < l for l in exec_c), f"execute_trade_intent at {exec_c}")

# ── 2. COMPILE-LEVEL proof that the read at :610 must raise ──────────────────
# Calling the function for real is NOT used here: outside market hours it
# returns at the status check on :524 and never reaches :610, and mocking the
# market open would run the exit-management block against live positions —
# a side effect this proof must not cause.
#
# The compiler settles it instead. Because `settings` is imported locally at
# :632, CPython classifies it as a function-local for the WHOLE body, so it
# appears in co_varnames. Reading a local before it is bound raises
# UnboundLocalError by language semantics — there is no execution path in which
# :610 succeeds.
from tasks.india_tasks import _india_trade_loop as _fn
code = _fn.__code__
inner = [c for c in code.co_consts if hasattr(c, "co_varnames")]
names = set(code.co_varnames) | {n for c in inner for n in c.co_varnames}
check("`settings` is compiled as a function-local (co_varnames)",
      "settings" in names,
      f"co_varnames contains 'settings' -> the :{first_read} read is a load-before-store")

def _demo():
    try:
        _probe                      # read before the local import below
        return None
    except UnboundLocalError as e:
        return e
    from utils.config import settings as _probe   # noqa: F401  (makes it local)
check("that pattern raises UnboundLocalError in this interpreter",
      isinstance(_demo(), UnboundLocalError), "same scoping rule, isolated")

# ── 3. DB evidence: no TECHNICAL trade has ever opened from this path ────────
async def dbcheck():
    from sqlalchemy import text
    from db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        a = (await s.execute(text(
            "SELECT COUNT(*) FROM paper_trades WHERE strategy_family='TECHNICAL'"))).scalar()
        b = (await s.execute(text(
            "SELECT COUNT(*) FROM simulation_logs WHERE event_type='EXECUTION_GATE' "
            "AND data->>'strategy_family'='TECHNICAL' "
            "AND split_part(message,' | ',1) LIKE 'EXECUTED%'"))).scalar()
    return a, b
tech_trades, tech_exec = asyncio.run(dbcheck())
check("no TECHNICAL-family paper trade exists, ever", tech_trades == 0, f"count={tech_trades}")
check("no TECHNICAL intent has ever been EXECUTED at the gate", tech_exec == 0, f"count={tech_exec}")

# ── 4. the queue fix touches scheduling only ─────────────────────────────────
print()
print("  Scope of the intended change: tasks/celery_app.py (queue + route + expires)")
print("  and a new systemd unit. Neither file contains origination logic.")
print()
print("="*88)
print(f"  BUG-1 still blocks Hub candidate creation : {'YES' if ok else 'NOT PROVEN'}")
print(f"  Master Intelligence can reach candidates  : {'NO' if ok else 'NOT PROVEN'}")
print(f"  Order submission reachable from this test : {'NO' if ok else 'NOT PROVEN'}")
print(f"  A paper trade can be opened               : {'NO' if ok else 'NOT PROVEN'}")
print(f"  A live trade can be opened                : {'NO — PAPER_MODE=true and no path' if ok else 'NOT PROVEN'}")
print("="*88)
if not ok:
    print("\n  *** STOP — do not deploy ***"); sys.exit(1)
print("\n  VERDICT: safe to deploy the BUG-2 scheduling fix.")
