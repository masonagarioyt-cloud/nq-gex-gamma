"""
NQ Gamma Exposure (GEX) Level Generator
-----------------------------------------
Free-data estimate of gamma exposure levels for Nasdaq futures (NQ),
derived from QQQ options open interest (the closest free, liquid proxy
for NDX/NQ). Outputs a complete, ready-to-paste TradingView Pine Script
with the levels hardcoded.

IMPORTANT / HONEST LIMITATIONS:
- Data source is Yahoo Finance's free, unofficial feed (via yfinance).
  It is NOT a licensed real-time feed. Expect occasional delays,
  missing data, or breakage if Yahoo changes their site.
- GEX is computed using the standard public convention (dealers assumed
  long calls / short puts). This is the same simplifying assumption used
  by nearly every free GEX calculator. It is NOT SpotGamma's or
  MenthorQ's proprietary model and will not exactly match their numbers.
- QQQ options are used as a proxy for NDX/NQ. They track closely but
  are not identical (QQQ options also reflect QQQ-specific flows).
- Levels are scaled to NQ terms using the live NQ/QQQ price ratio at
  runtime, not a fixed constant (the ratio drifts over time).
"""

import sys
import math
import datetime as dt

import numpy as np
import yfinance as yf
from scipy.stats import norm

RISK_FREE_RATE = 0.05  # rough approximation; good enough for short-dated gamma
CONTRACT_MULTIPLIER = 100


def bs_gamma(spot, strike, t_years, iv, r=RISK_FREE_RATE):
    """Black-Scholes gamma. Returns 0 if inputs are degenerate."""
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * t_years) / (iv * math.sqrt(t_years))
    return norm.pdf(d1) / (spot * iv * math.sqrt(t_years))


def pick_expiration(expirations, today):
    """Pick the nearest expiration that's at least 0 days out (today counts)."""
    dated = sorted(expirations)
    for e in dated:
        exp_date = dt.datetime.strptime(e, "%Y-%m-%d").date()
        if exp_date >= today:
            return e
    return dated[-1] if dated else None


def fetch_qqq_gex():
    qqq = yf.Ticker("QQQ")
    spot_hist = qqq.history(period="1d")
    if spot_hist.empty:
        raise RuntimeError("Could not fetch QQQ spot price.")
    spot = float(spot_hist["Close"].iloc[-1])

    today = dt.date.today()
    expirations = qqq.options
    if not expirations:
        raise RuntimeError("No QQQ option expirations returned.")
    expiry = pick_expiration(expirations, today)
    exp_date = dt.datetime.strptime(expiry, "%Y-%m-%d").date()
    t_years = max((exp_date - today).days, 0.5) / 365.0

    chain = qqq.option_chain(expiry)
    calls, puts = chain.calls, chain.puts

    strikes = sorted(set(calls["strike"]).union(set(puts["strike"])))
    gex_by_strike = {}

    for k in strikes:
        c_row = calls[calls["strike"] == k]
        p_row = puts[puts["strike"] == k]

        c_oi = float(c_row["openInterest"].iloc[0]) if not c_row.empty and not np.isnan(c_row["openInterest"].iloc[0]) else 0.0
        p_oi = float(p_row["openInterest"].iloc[0]) if not p_row.empty and not np.isnan(p_row["openInterest"].iloc[0]) else 0.0
        c_iv = float(c_row["impliedVolatility"].iloc[0]) if not c_row.empty else 0.0
        p_iv = float(p_row["impliedVolatility"].iloc[0]) if not p_row.empty else 0.0

        c_gamma = bs_gamma(spot, k, t_years, c_iv)
        p_gamma = bs_gamma(spot, k, t_years, p_iv)

        # Standard public GEX convention: dealers long calls, short puts
        gex = (c_oi * c_gamma - p_oi * p_gamma) * CONTRACT_MULTIPLIER * spot ** 2 * 0.01
        gex_by_strike[k] = gex

    return spot, expiry, gex_by_strike


def compute_levels(spot, gex_by_strike):
    strikes_sorted = sorted(gex_by_strike.keys())
    gex_values = [gex_by_strike[k] for k in strikes_sorted]

    # Call wall = strike with the largest positive GEX
    call_wall = max(gex_by_strike, key=lambda k: gex_by_strike[k])
    # Put wall = strike with the most negative GEX
    put_wall = min(gex_by_strike, key=lambda k: gex_by_strike[k])

    # Zero gamma flip: where cumulative GEX (sorted by strike) crosses zero
    cumulative = np.cumsum(gex_values)
    flip_strike = None
    for i in range(1, len(cumulative)):
        if cumulative[i - 1] < 0 <= cumulative[i]:
            # linear interpolation between the two strikes
            k0, k1 = strikes_sorted[i - 1], strikes_sorted[i]
            c0, c1 = cumulative[i - 1], cumulative[i]
            frac = -c0 / (c1 - c0) if (c1 - c0) != 0 else 0
            flip_strike = k0 + frac * (k1 - k0)
            break
    if flip_strike is None:
        flip_strike = spot  # fallback if no clean crossing found

    # Top 3 positive and negative strikes for extra context
    top_pos = sorted(gex_by_strike.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_neg = sorted(gex_by_strike.items(), key=lambda kv: kv[1])[:3]

    return {
        "call_wall": call_wall,
        "put_wall": put_wall,
        "gamma_flip": flip_strike,
        "top_pos": top_pos,
        "top_neg": top_neg,
    }


def fetch_nq_price():
    nq = yf.Ticker("NQ=F")
    hist = nq.history(period="1d")
    if hist.empty:
        raise RuntimeError("Could not fetch NQ=F price.")
    return float(hist["Close"].iloc[-1])


def to_nq_terms(qqq_price_level, qqq_spot, nq_spot):
    ratio = nq_spot / qqq_spot
    return qqq_price_level * ratio


def generate_pine_script(nq_spot, qqq_spot, expiry, levels_nq, generated_at):
    call_wall, put_wall, gamma_flip = levels_nq["call_wall"], levels_nq["put_wall"], levels_nq["gamma_flip"]
    top_pos_lines = "\n    ".join(
        f'array.push(topPosLevels, {lvl:.2f})' for lvl, _ in levels_nq["top_pos"]
    )
    top_neg_lines = "\n    ".join(
        f'array.push(topNegLevels, {lvl:.2f})' for lvl, _ in levels_nq["top_neg"]
    )

    pine = f'''//@version=6
indicator("NQ GEX Levels (Auto-Generated, Free Data Estimate)", overlay=true, max_lines_count=50)

// ============================================================
// AUTO-GENERATED — {generated_at} UTC
// Source: QQQ options chain (free/unofficial), expiry {expiry}
// QQQ spot at calc time: {qqq_spot:.2f} | NQ spot at calc time: {nq_spot:.2f}
// This is a FREE-DATA ESTIMATE using the standard public GEX
// convention (dealers long calls / short puts). It is NOT
// SpotGamma's or MenthorQ's proprietary model.
// Paste this whole script over the old version each morning.
// ============================================================

callWall   = {call_wall:.2f}
putWall    = {put_wall:.2f}
gammaFlip  = {gamma_flip:.2f}

showTopLevels = input.bool(true, "Show Top Positive/Negative GEX Strikes")

var topPosLevels = array.new_float(0)
var topNegLevels = array.new_float(0)
if barstate.isfirst
    array.clear(topPosLevels)
    array.clear(topNegLevels)
    {top_pos_lines if top_pos_lines else "// no data"}
    {top_neg_lines if top_neg_lines else "// no data"}

plot(callWall, title="Call Wall", color=color.new(color.green, 0), linewidth=2, style=plot.style_line)
plot(putWall, title="Put Wall", color=color.new(color.red, 0), linewidth=2, style=plot.style_line)
plot(gammaFlip, title="Gamma Flip (Zero Gamma)", color=color.new(color.orange, 0), linewidth=2, style=plot.style_circles)

if showTopLevels and barstate.islast
    for i = 0 to array.size(topPosLevels) - 1
        line.new(bar_index, array.get(topPosLevels, i), bar_index + 20, array.get(topPosLevels, i), color=color.new(color.green, 60), width=1, extend=extend.right)
    for i = 0 to array.size(topNegLevels) - 1
        line.new(bar_index, array.get(topNegLevels, i), bar_index + 20, array.get(topNegLevels, i), color=color.new(color.red, 60), width=1, extend=extend.right)

var label infoLabel = na
if barstate.islast
    label.delete(infoLabel)
    infoLabel := label.new(bar_index, gammaFlip, "Generated {generated_at} UTC\\nQQQ->NQ estimate, expiry {expiry}", style=label.style_label_left, color=color.new(color.gray, 80), textcolor=color.white, size=size.small)
'''
    return pine


def main():
    generated_at = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    qqq_spot, expiry, gex_by_strike = fetch_qqq_gex()
    levels_qqq = compute_levels(qqq_spot, gex_by_strike)
    nq_spot = fetch_nq_price()

    levels_nq = {
        "call_wall": to_nq_terms(levels_qqq["call_wall"], qqq_spot, nq_spot),
        "put_wall": to_nq_terms(levels_qqq["put_wall"], qqq_spot, nq_spot),
        "gamma_flip": to_nq_terms(levels_qqq["gamma_flip"], qqq_spot, nq_spot),
        "top_pos": [(to_nq_terms(k, qqq_spot, nq_spot), v) for k, v in levels_qqq["top_pos"]],
        "top_neg": [(to_nq_terms(k, qqq_spot, nq_spot), v) for k, v in levels_qqq["top_neg"]],
    }

    pine = generate_pine_script(nq_spot, qqq_spot, expiry, levels_nq, generated_at)

    with open("output.pine", "w") as f:
        f.write(pine)

    print(f"Generated output.pine | QQQ spot {qqq_spot:.2f} -> NQ spot {nq_spot:.2f}")
    print(f"Call wall (NQ terms): {levels_nq['call_wall']:.2f}")
    print(f"Put wall (NQ terms): {levels_nq['put_wall']:.2f}")
    print(f"Gamma flip (NQ terms): {levels_nq['gamma_flip']:.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
