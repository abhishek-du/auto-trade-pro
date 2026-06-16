"""Black-Scholes-Merton option pricing, Greeks, and implied-volatility solver.

Implemented natively on scipy (already a project dependency) rather than adding
py_vollib — avoids a new C-extension dependency in a constrained build/network
environment. Suitable for European, cash-settled NSE index options.

Conventions (broker-friendly scaling):
  delta  — per ₹1 move in the underlying        (-1..1)
  gamma  — delta change per ₹1 move
  vega   — price change per +1% (0.01) IV change
  theta  — price change per 1 calendar day      (negative for long options)
  rho    — price change per +1% (0.01) rate change
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import brentq
from scipy.stats import norm

# Year basis for time-to-expiry and per-day theta.
_DAYS_PER_YEAR = 365.0


@dataclass
class Greeks:
    iv:    float
    delta: float
    gamma: float
    vega:  float
    theta: float
    rho:   float


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float, q: float) -> tuple[float, float]:
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vt
    d2 = d1 - vt
    return d1, d2


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             flag: str, q: float = 0.0) -> float:
    """Black-Scholes-Merton price. flag: 'c' (call/CE) or 'p' (put/PE)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        # Intrinsic value at/after expiry.
        if flag == "c":
            return max(0.0, S - K)
        return max(0.0, K - S)
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)
    if flag == "c":
        return S * disc_q * norm.cdf(d1) - K * disc_r * norm.cdf(d2)
    return K * disc_r * norm.cdf(-d2) - S * disc_q * norm.cdf(-d1)


def implied_vol(price: float, S: float, K: float, T: float, r: float,
                flag: str, q: float = 0.0) -> float | None:
    """Solve IV from a market option price by inverting Black-Scholes.

    Returns None when the price is below intrinsic or the solver can't converge.
    """
    if price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(0.0, (S - K) if flag == "c" else (K - S)) * math.exp(-r * T)
    if price < intrinsic - 1e-6:
        return None  # arbitrage / stale quote

    def _obj(sigma: float) -> float:
        return bs_price(S, K, T, r, sigma, flag, q) - price

    try:
        return float(brentq(_obj, 1e-4, 5.0, maxiter=100, xtol=1e-6))
    except (ValueError, RuntimeError):
        return None


def greeks(S: float, K: float, T: float, r: float, sigma: float,
           flag: str, q: float = 0.0) -> Greeks:
    """Full Greeks for a European option at a given IV (sigma)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        # Degenerate at expiry: delta is a step, other Greeks ~0.
        intrinsic_delta = (1.0 if S > K else 0.0) if flag == "c" else (-1.0 if S < K else 0.0)
        return Greeks(iv=sigma, delta=intrinsic_delta, gamma=0.0, vega=0.0, theta=0.0, rho=0.0)

    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    sqrt_t = math.sqrt(T)
    pdf_d1 = norm.pdf(d1)
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)

    gamma = disc_q * pdf_d1 / (S * sigma * sqrt_t)
    vega  = S * disc_q * pdf_d1 * sqrt_t / 100.0          # per 1% IV

    if flag == "c":
        delta = disc_q * norm.cdf(d1)
        theta_yr = (-S * disc_q * pdf_d1 * sigma / (2 * sqrt_t)
                    - r * K * disc_r * norm.cdf(d2)
                    + q * S * disc_q * norm.cdf(d1))
        rho = K * T * disc_r * norm.cdf(d2) / 100.0       # per 1% rate
    else:
        delta = -disc_q * norm.cdf(-d1)
        theta_yr = (-S * disc_q * pdf_d1 * sigma / (2 * sqrt_t)
                    + r * K * disc_r * norm.cdf(-d2)
                    - q * S * disc_q * norm.cdf(-d1))
        rho = -K * T * disc_r * norm.cdf(-d2) / 100.0

    return Greeks(
        iv=sigma,
        delta=round(delta, 4),
        gamma=round(gamma, 6),
        vega=round(vega, 4),
        theta=round(theta_yr / _DAYS_PER_YEAR, 4),         # per calendar day
        rho=round(rho, 4),
    )


def greeks_from_price(price: float, S: float, K: float, T: float, r: float,
                      flag: str, q: float = 0.0) -> Greeks | None:
    """Convenience: solve IV from market price, then return full Greeks."""
    iv = implied_vol(price, S, K, T, r, flag, q)
    if iv is None:
        return None
    return greeks(S, K, T, r, iv, flag, q)


def years_to_expiry(days: float) -> float:
    """Convert calendar days-to-expiry into the year-fraction T."""
    return max(0.0, days) / _DAYS_PER_YEAR
