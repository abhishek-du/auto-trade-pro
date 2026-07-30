import re

def update_file():
    with open('/home/cis/windows/auto-trade-pro/autotrade-backend/crawler/zerodha_market.py', 'r') as f:
        content = f.read()

    # We need to change the instrument fetching logic.
    # Replace the fetch part:
    old_fetch = """    try:
        rows = await kite.get_instruments("NFO")
    except Exception as exc:
        logger.error(f"[zerodha_market] NFO instrument download failed: {exc}", exc_info=True)
        return 0

    today = _dt.date.today()"""

    new_fetch = """    try:
        nfo_rows = await kite.get_instruments("NFO")
        nse_rows = await kite.get_instruments("NSE")
        bse_rows = await kite.get_instruments("BSE")
    except Exception as exc:
        logger.error(f"[zerodha_market] Instrument download failed: {exc}", exc_info=True)
        return 0

    today = _dt.date.today()"""

    content = content.replace(old_fetch, new_fetch)
    
    # Now replace the loop logic. 
    # The old logic processes `rows` (which was NFO).
    # We will process `nfo_rows` with the NFO logic, and then process `nse_rows + bse_rows` without expiry filters.
    
    old_loop1 = """    for r in rows:
        nm    = str(r.get("name") or "")
        itype = str(r.get("instrument_type") or "")
        exp_s = str(r.get("expiry") or "")
        if nm not in _FNO_NAMES or not exp_s:
            continue
        try:
            exp = _dt.date.fromisoformat(exp_s)
        except ValueError:
            continue
        if exp < today:
            continue
        _expiry_sets.setdefault((nm, itype), set()).add(exp)"""

    new_loop1 = """    for r in nfo_rows:
        nm    = str(r.get("name") or "")
        itype = str(r.get("instrument_type") or "")
        exp_s = str(r.get("expiry") or "")
        if nm not in _FNO_NAMES or not exp_s:
            continue
        try:
            exp = _dt.date.fromisoformat(exp_s)
        except ValueError:
            continue
        if exp < today:
            continue
        _expiry_sets.setdefault((nm, itype), set()).add(exp)"""
        
    content = content.replace(old_loop1, new_loop1)
    
    old_clear = """    # Clear existing NFO rows, insert filtered batch
    await session.execute(delete(KiteInstrument).where(KiteInstrument.exchange == "NFO"))"""
    new_clear = """    # Clear existing NFO, NSE, BSE rows, insert filtered batch
    await session.execute(delete(KiteInstrument).where(KiteInstrument.exchange.in_(["NFO", "NSE", "BSE"])))"""
    content = content.replace(old_clear, new_clear)
    
    old_loop2 = """    for r in rows:
        nm    = str(r.get("name") or "")
        itype = str(r.get("instrument_type") or "")
        if nm not in _FNO_NAMES:
            continue
        exp_s = str(r.get("expiry") or "")
        if not exp_s:
            continue
        try:
            exp = _dt.date.fromisoformat(exp_s)
        except ValueError:
            continue
        if exp < today:
            continue
        if exp not in _allowed_exp.get((nm, itype), set()):
            continue

        # OTM filter for options
        if itype in ("CE", "PE"):
            strike = float(r.get("strike") or 0)
            spot   = _spots.get(nm, 0)
            if spot and (strike < spot * (1 - _OTM_PCT) or strike > spot * (1 + _OTM_PCT)):
                continue

        try:
            token = int(r.get("instrument_token") or 0)
            if not token:
                continue
            batch.append(KiteInstrument(
                instrument_token = token,
                exchange_token   = int(r.get("exchange_token") or 0),
                tradingsymbol    = str(r.get("tradingsymbol") or ""),
                name             = nm,
                last_price       = float(r.get("last_price") or 0.0),
                expiry           = exp_s,
                strike           = float(r.get("strike") or 0.0),
                tick_size        = float(r.get("tick_size") or 0.05),
                lot_size         = int(float(r.get("lot_size") or 1)),
                instrument_type  = itype,
                segment          = str(r.get("segment") or "NFO"),
                exchange         = "NFO",
                refreshed_at     = now,
            ))
        except (ValueError, TypeError):
            continue"""
            
    new_loop2 = """    for r in nfo_rows:
        nm    = str(r.get("name") or "")
        itype = str(r.get("instrument_type") or "")
        if nm not in _FNO_NAMES:
            continue
        exp_s = str(r.get("expiry") or "")
        if not exp_s:
            continue
        try:
            exp = _dt.date.fromisoformat(exp_s)
        except ValueError:
            continue
        if exp < today:
            continue
        if exp not in _allowed_exp.get((nm, itype), set()):
            continue

        # OTM filter for options
        if itype in ("CE", "PE"):
            strike = float(r.get("strike") or 0)
            spot   = _spots.get(nm, 0)
            if spot and (strike < spot * (1 - _OTM_PCT) or strike > spot * (1 + _OTM_PCT)):
                continue

        try:
            token = int(r.get("instrument_token") or 0)
            if not token:
                continue
            batch.append(KiteInstrument(
                instrument_token = token,
                exchange_token   = int(r.get("exchange_token") or 0),
                tradingsymbol    = str(r.get("tradingsymbol") or ""),
                name             = nm,
                last_price       = float(r.get("last_price") or 0.0),
                expiry           = exp_s,
                strike           = float(r.get("strike") or 0.0),
                tick_size        = float(r.get("tick_size") or 0.05),
                lot_size         = int(float(r.get("lot_size") or 1)),
                instrument_type  = itype,
                segment          = str(r.get("segment") or "NFO"),
                exchange         = "NFO",
                refreshed_at     = now,
            ))
        except (ValueError, TypeError):
            continue

    # Add NSE and BSE Equity instruments
    for r in (nse_rows + bse_rows):
        nm    = str(r.get("name") or "")
        itype = str(r.get("instrument_type") or "")
        if itype not in ("EQ", ""):
            continue
            
        try:
            token = int(r.get("instrument_token") or 0)
            if not token:
                continue
            exchange = str(r.get("exchange") or "")
            batch.append(KiteInstrument(
                instrument_token = token,
                exchange_token   = int(r.get("exchange_token") or 0),
                tradingsymbol    = str(r.get("tradingsymbol") or ""),
                name             = nm,
                last_price       = float(r.get("last_price") or 0.0),
                expiry           = None,
                strike           = 0.0,
                tick_size        = float(r.get("tick_size") or 0.05),
                lot_size         = int(float(r.get("lot_size") or 1)),
                instrument_type  = "EQ",
                segment          = str(r.get("segment") or exchange),
                exchange         = exchange,
                refreshed_at     = now,
            ))
        except (ValueError, TypeError):
            continue"""
    content = content.replace(old_loop2, new_loop2)
    
    old_log = """    logger.info(
        f"[zerodha_market] NFO instruments refreshed: {len(batch)} contracts "
        f"(from {len(rows):,} raw) — names={sorted(_FNO_NAMES)}"
    )"""
    new_log = """    logger.info(
        f"[zerodha_market] Instruments refreshed: {len(batch)} contracts "
        f"(NFO={len(nfo_rows)}, NSE={len(nse_rows)}, BSE={len(bse_rows)})"
    )"""
    content = content.replace(old_log, new_log)
    
    with open('/home/cis/windows/auto-trade-pro/autotrade-backend/crawler/zerodha_market.py', 'w') as f:
        f.write(content)

update_file()
