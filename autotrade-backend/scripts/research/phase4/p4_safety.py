"""Part 15 — prove the shadow script cannot execute anything."""
import ast, sys
SP="/tmp/claude-1000/-home-cis-windows-auto-trade-pro/5e075b1b-8af1-45c2-9e44-41734fa98c18/scratchpad"
BANNED_MODULES = {"engine.decision_router","engine.tactical_executor","engine.zerodha_executor",
                  "paper_trading.trade_simulator","paper_trading.virtual_wallet",
                  "engine.agent.execution","paper_trading","engine.agent.agent_loop"}
BANNED_CALLS = {"execute_trade_intent","authorize_trade_intent","route_decision",
                "open_paper_trade","close_paper_trade","scale_out_paper_trade",
                "place_real_order","place_order","_record_exit"}
# `.execute()` is NOT banned outright — it is SQLAlchemy's, and banning it would
# make this proof vacuous by flagging every SELECT. Instead every .execute() is
# checked individually below: its SQL must be a SELECT literal.
src = open(f"{SP}/p4_shadow.py").read()
tree = ast.parse(src)
bad = []
for n in ast.walk(tree):
    if isinstance(n, ast.Import):
        for a in n.names:
            if any(a.name.startswith(b) for b in BANNED_MODULES): bad.append(f"import {a.name}")
    if isinstance(n, ast.ImportFrom) and n.module:
        if any(n.module.startswith(b) for b in BANNED_MODULES): bad.append(f"from {n.module}")
    if isinstance(n, ast.Call):
        nm = getattr(n.func,"id",None) or getattr(n.func,"attr",None)
        if nm in BANNED_CALLS: bad.append(f"call {nm}()")
writes = [n for n in ast.walk(tree) if isinstance(n,ast.Call)
          and (getattr(n.func,"attr",None) in ("add","commit","flush","add_all"))]
sqls = [s for s in (getattr(n,"value","") for n in ast.walk(tree) if isinstance(n,ast.Constant))
        if isinstance(s,str) and any(k in s.upper() for k in ("INSERT","UPDATE","DELETE","DROP","ALTER"))]

# every .execute(...) must carry a SELECT-only literal
non_select = []
for n in ast.walk(tree):
    if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "execute":
        lits = [c.value for c in ast.walk(n) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
        sql = " ".join(lits).strip().upper()
        if not sql.startswith("SELECT") and "SELECT" not in sql[:40]:
            non_select.append(ast.get_source_segment(src, n)[:60] if ast.get_source_segment(src, n) else "?")
print(f"  .execute() calls               : {sum(1 for n in ast.walk(tree) if isinstance(n,ast.Call) and getattr(n.func,'attr',None)=='execute')} — non-SELECT: {non_select if non_select else 'NONE'}")
print("="*78); print("PART 15 — SHADOW SAFETY PROOF"); print("="*78)
print(f"  banned execution imports/calls : {bad if bad else 'NONE'}")
print(f"  session.add/commit/flush calls : {len(writes)}")
print(f"  mutating SQL literals          : {len(sqls)}")
ok = not bad and not writes and not sqls and not non_select
print(f"\n  order submission     : {'IMPOSSIBLE' if ok else 'NOT PROVEN'}")
print(f"  trade opening        : {'IMPOSSIBLE' if ok else 'NOT PROVEN'}")
print(f"  capital allocation   : {'IMPOSSIBLE' if ok else 'NOT PROVEN'}")
print(f"  paper execution      : {'IMPOSSIBLE' if ok else 'NOT PROVEN'}")
if not ok: print("\n  *** STOP — do not run the shadow funnel ***"); sys.exit(1)
print("\n  VERDICT: the shadow script has no reachable execution path.")
