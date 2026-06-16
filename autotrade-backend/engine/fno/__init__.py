"""Futures & Options (F&O) engine.

Modules:
  options_pricing  — Black-Scholes Greeks + implied-volatility solver (scipy).
  contracts        — resolve a directional signal to a concrete NFO contract
                     by looking up the KiteInstrument master.
  selection        — directional Hub signal → option choice + lot-rounded sizing.
  margin           — approximate SPAN/exposure paper-margin model.
  strategies_vol   — volatility / delta-neutral strategy construction.
"""
