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
  missing data, or breakage if Yahoo changes something.
- GEX is computed using the standard public convention (dealers assumed
  long calls / short puts). This is the same simplifying assumption used
  by nearly every free GEX calculator. It is NOT SpotGamma's or
  MenthorQ's proprietary model and will not exactly match their numbers.
- QQQ options are used as a proxy for NDX/NQ, scaled via the live
  NQ/QQQ price ratio at run time.
- "Strength %" on each ranked GEX level is this strike's gamma exposure
  magnitude relative to the single largest strike found in the chain
  (100% = the biggest one). It is NOT a probability or a confidence score.
- This script intentionally does NOT attempt to replicate "HVL" (that's
  volume-profile data, a different data source) or "Blind Spots" (an
  undocumented MenthorQ-proprietary concept with no public formula).
"""

import sys
import math
import datetime as dt

import numpy as np
import yfinance as yf
from scipy.stats import norm

RISK_FREE_RATE = 0.05
CONTRACT_MULTIPLIER = 100
TOP_N_LEVELS = 7


def bs_gamma(spot, strike, t_years, iv, r=RISK_FREE_RATE):
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * t_years) / (iv * math.sqrt(t_years))
    return norm.pdf(d1) / (spot * iv * math.sqrt(t_years))


def pick_expiration(expirations, today):
    dated = sorted(expirations)
    for e in dated:
        exp_date = dt.datetime.strptime(e, "%Y-%m-%d").date()
        if exp_date >= today:
            return e
    return dated[-1] if dated else None


def compute_gex_for_expiry(qqq, expiry, spot, today):
    exp_date = dt.datetime.strptime(expiry, "%Y-%m-%d").date()
    t_years = max((exp_date - today).days, 0.5) / 365.0

    chain = qqq.option_chain(expiry)
    calls, puts = chain.calls, chain.puts
    strikes = sorted(set(calls["strike"]).union(set(puts["strike"])))

    gex_by_strike = {}
    atm_iv = None
    atm_diff = None

    for k in strikes:
        c_row = calls[calls["strike"] == k]
        p_row = puts[puts["strike"] == k]

        c_oi = float(c_row["openInterest"].iloc[0]) if not c_row.empty and not np.isnan(c_row["openInterest"].iloc[0]) else 0.0
        p_oi = float(p_row["openInterest"].iloc[0]) if not p_row.empty and not np.isnan(p_row["openInterest"].iloc[0]) else 0.0
        c_iv = float(c_row["impliedVolatility"].iloc[0]) if not c_row.empty else 0.0
        p_iv = float(p_row["impliedVolatility"].iloc[0]) if not p_row.empty else 0.0

        c_gamma = bs_gamma(spot, k, t_years, c_iv)
        p_gamma = bs_gamma(spot, k, t_years, p_iv)

        gex = (c_oi * c_gamma - p_oi * p_gamma) * CONTRACT_MULTIPLIER * spot ** 2 * 0.01
        gex_by_strike[k] = gex

        diff = abs(k - spot)
        if atm_diff is None or diff < atm_diff:
            atm_diff = diff
            atm_iv = c_iv if c_iv > 0 else p_iv

    return gex_by_strike, t_years, atm_iv


def wall_levels(gex_by_strike):
    call_wall = max(gex_by_strike, key=lambda k: gex_by_strike[k])
    put_wall = min(gex_by_strike, key=lambda k: gex_by_strike[k])
    return call_wall, put_wall


def gamma_flip_level(gex_by_strike, spot):
    strikes_sorted = sorted(gex_by_strike.keys())
    gex_values = [gex_by_strike[k] for k in strikes_sorted]
    cumulative = np.cumsum(gex_values)
    for i in range(1, len(cumulative)):
        if cumulative[i - 1] < 0 <= cumulative[i]:
            k0, k1 = strikes_sorted[i - 1], strikes_sorted[i]
            c0, c1 = cumulative[i - 1], cumulative[i]
            frac = -c0 / (c1 - c0) if (c1 - c0) != 0 else 0
            return k0 + frac * (k1 - k0)
    return spot


def top_n_ranked(gex_by_strike, exclude_strikes, n):
    max_abs = max((abs(v) for v in gex_by_strike.values()), default=1.0)
    ranked = sorted(
        ((k, v) for k, v in gex_by_strike.items() if k not in exclude_strikes),
        key=lambda kv: abs(kv[1]),
        reverse=True,
    )[:n]
    out = []
    for k, v in ranked:
        pct = round(100 * abs(v) / max_abs) if max_abs else 0
        out.append((k, pct, v >= 0))
    return out


def fetch_nq_price():
    nq = yf.Ticker("NQ=F")
    hist = nq.history(period="1d")
    if hist.empty:
        raise RuntimeError("Could not fetch NQ=F price.")
    return float(hist["Close"].iloc[-1])


def to_nq(price_qqq, qqq_spot, nq_spot):
    return price_qqq * (nq_spot / qqq_spot)


def build_levels(qqq_spot, nq_spot, today):
    qqq = yf.Ticker("QQQ")
    expirations = qqq.options
    if not expirations:
        raise RuntimeError("No QQQ option expirations returned.")

    main_expiry = pick_expiration(expirations, today)
    gex_main, t_years_main, atm_iv_main = compute_gex_for_expiry(qqq, main_expiry, qqq_spot, today)

    call_wall, put_wall = wall_levels(gex_main)
    gamma_flip = gamma_flip_level(gex_main, qqq_spot)
    ranked = top_n_ranked(gex_main, exclude_strikes={call_wall, put_wall}, n=TOP_N_LEVELS)

    # 0DTE (today's expiry), if it exists
    today_str = today.strftime("%Y-%m-%d")
    zero_dte_expiry = today_str if today_str in expirations else None
    call_res_0dte = put_sup_0dte = None
    if zero_dte_expiry:
        gex_0dte, _, _ = compute_gex_for_expiry(qqq, zero_dte_expiry, qqq_spot, today)
        if gex_0dte:
            call_res_0dte, put_sup_0dte = wall_levels(gex_0dte)

    # 1-day expected move using nearest-expiry ATM IV as an approximation
    atm_iv = atm_iv_main or 0.20
    em = qqq_spot * atm_iv * math.sqrt(1.0 / 365.0)
    em_low, em_high = qqq_spot - em, qqq_spot + em

    levels = []
    levels.append({"name": "Call Wall", "price": to_nq(call_wall, qqq_spot, nq_spot), "pct": 100, "color": "color.green", "style": "hline.style_solid"})
    levels.append({"name": "Put Wall", "price": to_nq(put_wall, qqq_spot, nq_spot), "pct": 100, "color": "color.red", "style": "hline.style_solid"})
    levels.append({"name": "Gamma Flip", "price": to_nq(gamma_flip, qqq_spot, nq_spot), "pct": 100, "color": "color.orange", "style": "hline.style_solid"})

    if call_res_0dte is not None:
        levels.append({"name": "Call Resistance 0DTE", "price": to_nq(call_res_0dte, qqq_spot, nq_spot), "pct": 95, "color": "color.new(color.red, 20)", "style": "hline.style_dashed"})
    if put_sup_0dte is not None:
        levels.append({"name": "Put Support 0DTE", "price": to_nq(put_sup_0dte, qqq_spot, nq_spot), "pct": 95, "color": "color.new(color.teal, 20)", "style": "hline.style_dashed"})

    for i, (k, pct, is_call_side) in enumerate(ranked, start=1):
        levels.append({
            "name": f"GEX {i}",
            "price": to_nq(k, qqq_spot, nq_spot),
            "pct": pct,
            "color": "color.new(color.aqua, 10)" if is_call_side else "color.new(color.fuchsia, 10)",
            "style": "hline.style_dotted",
        })

    levels.append({"name": "1D Expected Move High", "price": to_nq(em_high, qqq_spot, nq_spot), "pct": 68, "color": "color.new(color.yellow, 30)", "style": "hline.style_dashed"})
    levels.append({"name": "1D Expected Move Low", "price": to_nq(em_low, qqq_spot, nq_spot), "pct": 68, "color": "color.new(color.yellow, 30)", "style": "hline.style_dashed"})

    return levels, main_expiry, zero_dte_expiry


def generate_pine_script(levels, main_expiry, zero_dte_expiry, qqq_spot, nq_spot, generated_at):
    lines = []
    lines.append('//@version=6')
    lines.append('indicator("NQ GEX Levels (Auto-Generated, Free Data Estimate)", overlay=true, max_lines_count=50, max_labels_count=100)')
    lines.append('')
    lines.append('// ============================================================')
    lines.append(f'// AUTO-GENERATED — {generated_at} UTC')
    lines.append(f'// Source: QQQ options chain (free/unofficial). Main expiry: {main_expiry}' + (f', 0DTE: {zero_dte_expiry}' if zero_dte_expiry else ' (no 0DTE chain available today)'))
    lines.append(f'// QQQ spot at calc time: {qqq_spot:.2f} | NQ spot at calc time: {nq_spot:.2f}')
    lines.append('// FREE-DATA ESTIMATE using the standard public GEX convention')
    lines.append('// (dealers long calls / short puts). NOT SpotGamma\'s or')
    lines.append('// MenthorQ\'s proprietary model. "Strength %" = this level\'s')
    lines.append('// magnitude relative to the single strongest strike found.')
    lines.append('// Paste this whole script over the old version each morning.')
    lines.append('// ============================================================')
    lines.append('')

    # hlines: fixed, chart-locked, never drift regardless of scroll/zoom
    for i, lv in enumerate(levels):
        lines.append(
            f'hline({lv["price"]:.2f}, title="{lv["name"]} ({lv["pct"]}%)", '
            f'color={lv["color"]}, linestyle={lv["style"]}, linewidth=2)'
        )
    lines.append('')

    # var label declarations
    for i in range(len(levels)):
        lines.append(f'var label lbl_{i} = na')
    lines.append('')

    # delete + recreate labels each bar so they always track the right edge
    # without ever stacking up duplicates
    lines.append('if barstate.islast')
    for i, lv in enumerate(levels):
        lines.append(f'    label.delete(lbl_{i})')
        lines.append(
            f'    lbl_{i} := label.new(bar_index + 3, {lv["price"]:.2f}, '
            f'"{lv["name"]}  {lv["pct"]}%", xloc=xloc.bar_index, '
            f'style=label.style_label_left, color=color.new(color.black, 100), '
            f'textcolor={lv["color"]}, size=size.small)'
        )
    lines.append('')

    return "\n".join(lines)


def main():
    generated_at = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    today = dt.date.today()

    qqq_hist = yf.Ticker("QQQ").history(period="1d")
    if qqq_hist.empty:
        raise RuntimeError("Could not fetch QQQ spot price.")
    qqq_spot = float(qqq_hist["Close"].iloc[-1])

    nq_spot = fetch_nq_price()

    levels, main_expiry, zero_dte_expiry = build_levels(qqq_spot, nq_spot, today)
    pine = generate_pine_script(levels, main_expiry, zero_dte_expiry, qqq_spot, nq_spot, generated_at)

    with open("output.pine", "w") as f:
        f.write(pine)

    print(f"Generated output.pine with {len(levels)} levels | QQQ {qqq_spot:.2f} -> NQ {nq_spot:.2f}")
    for lv in levels:
        print(f"  {lv['name']:<24} {lv['price']:.2f}  ({lv['pct']}%)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
