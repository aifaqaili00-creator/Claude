"""Unit economics for one product in one marketplace: what is left per sale after Amazon, freight, duty and ads.

Prices are in the marketplace currency (US$ / A$ / AED). Product cost and freight are entered in US$ (what a
supplier quotes) and converted with the fee table's exchange rates. GST/VAT: when you are registered, the
price includes tax you pay over (NetSales = price / (1 + v)) but you get tax on fees and imports back; when you
are not, the full price is yours but tax on Amazon's fees and on imports is a cost.
Pure functions; fees come from engine.fees (with optional overrides).
"""
import math

from . import fees

DEFAULT_KG = 0.5
DEFAULT_DIMS = (25.0, 20.0, 8.0)


def _num(x, default=None):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def fx(market, table=None):
    """Local currency per US$."""
    t = table or fees.DEFAULTS
    cur = (t.get('markets', {}).get(market) or {}).get('currency', 'USD')
    return float((t.get('fx_per_usd') or {}).get(cur, 1.0))


def _mt(market, table=None):
    return fees.market_table(market, table) or {}


def freight_per_unit(market, inp, table=None):
    """Freight per unit in local currency. Air bills the larger of real and volumetric weight (cm^3 / 6000)."""
    mt = _mt(market, table)
    fr = mt.get('freight') or {}
    mode = inp.get('freight_mode') or 'sea'
    if _num(inp.get('freight_per_unit_usd')) is not None:
        return _num(inp['freight_per_unit_usd']) * fx(market, table)
    kg = _num(inp.get('kg'), DEFAULT_KG)
    dims = inp.get('dims_cm') or DEFAULT_DIMS
    carton = inp.get('carton') or {}
    units = max(1, int(_num(carton.get('units'), 1)))
    if carton.get('dims_cm') and carton.get('kg'):
        c = carton['dims_cm']
        c_kg, c_vol = _num(carton['kg'], kg * units), c[0] * c[1] * c[2]
    else:
        c_kg, c_vol = kg * units, dims[0] * dims[1] * dims[2] * units * 1.15     # some carton slack
    if mode == 'air':
        a = fr.get('air') or {}
        chargeable = max(c_kg, c_vol / float(a.get('divisor') or 6000))
        usd = float(a.get('rate_kg') or 6.0) * chargeable / units
    else:
        s = fr.get('sea') or {}
        by_kg = float(s.get('rate_kg') or 2.0) * c_kg / units
        by_cbm = float(s.get('rate_cbm') or 150.0) * (c_vol / 1e6) / units
        usd = max(by_kg, by_cbm)
    return usd * fx(market, table)


def unit_economics(inp, table=None):
    """Profit per unit with every step shown. inp keys (all optional except market and price):
    market, price, category, dims_cm, kg, cogs_usd (or cogs in local currency), freight_mode 'sea'|'air',
    carton {dims_cm, kg, units} or freight_per_unit_usd, duty_pct, tacos_pct, returns_pct, registered,
    months_storage, date ('YYYY-MM-DD', for peak fees), fba_fee_override, h10_fba_fee, other_per_unit."""
    market = fees.norm_market(inp.get('market')) or 'US'
    price = _num(inp.get('price'))
    if price is None or price <= 0:
        return None
    mt = _mt(market, table)
    tax = mt.get('tax') or {}
    defaults = (table or fees.DEFAULTS).get('profit_defaults') or {}
    registered = bool(inp.get('registered'))
    v, v_fees, v_imp = float(tax.get('price') or 0), float(tax.get('fees') or 0), float(tax.get('import') or 0)
    dims = inp.get('dims_cm') or DEFAULT_DIMS
    kg = _num(inp.get('kg'), DEFAULT_KG)
    warnings = []

    net_sales = price / (1 + v) if registered and v else price
    referral = fees.referral_fee(market, inp.get('category') or '', price, table) or 0.0
    tier = fees.size_tier(market, dims, kg, table)
    if _num(inp.get('fba_fee_override')) is not None:
        fba, source = _num(inp['fba_fee_override']), 'user'
    elif _num(inp.get('h10_fba_fee')) is not None:
        fba, source = _num(inp['h10_fba_fee']), 'h10'
    else:
        f = fees.fba_fee(market, tier, price, inp.get('date'), table) or {}
        fba, source = float(f.get('fee') or 0.0), 'table'
    vol = dims[0] * dims[1] * dims[2]
    months = _num(inp.get('months_storage'), defaults.get('months_storage', 2))
    storage = (fees.storage_fee(market, vol, None, tier, table) or 0.0) * months
    placement = fees.placement_fee(market, tier, table) or 0.0
    amazon = referral + fba + storage + placement
    fee_tax = 0.0 if registered else v_fees * amazon

    rate = fx(market, table)
    cogs = _num(inp.get('cogs'))
    if cogs is None:
        cogs = _num(inp.get('cogs_usd'), 0.0) * rate
    freight = freight_per_unit(market, inp, table)
    duty_cfg = mt.get('duty') or {}
    duty_pct = _num(inp.get('duty_pct'), float(duty_cfg.get('pct') or 0))
    duty_base = cogs + freight if (duty_cfg.get('base') == 'CIF') else cogs
    duty = duty_pct * duty_base
    import_tax = 0.0 if registered else v_imp * (cogs + freight + duty)
    landed = cogs + freight + duty + import_tax
    ads = _num(inp.get('tacos_pct'), defaults.get('tacos', 0.12)) * price
    returns = _num(inp.get('returns_pct'), defaults.get('returns', 0.025)) * price
    other = _num(inp.get('other_per_unit'), defaults.get('other_per_unit', 0.0))
    profit = net_sales - referral - fba - storage - placement - fee_tax - landed - ads - returns - other

    if duty_cfg.get('volatile'):
        warnings.append('US duty on imports has changed several times in 2026: check the current rate.')
    if (mt.get('conf') or '') == 'low':
        warnings.append('The %s fee table is an estimate: check it on Amazon before ordering.' % market)
    if source == 'table' and tier.get('tier') in (None, 'unknown'):
        warnings.append('Size unknown: the FBA fee assumes a small standard parcel.')

    r2 = lambda x: round(x, 2)                                            # noqa: E731
    out = {
        'market': market, 'currency': mt.get('symbol') or '', 'price': r2(price), 'registered': registered,
        'NetSales': r2(net_sales), 'Referral': r2(referral), 'FBA': r2(fba), 'Storage': r2(storage),
        'Placement': r2(placement), 'FeeTax': r2(fee_tax), 'COGS': r2(cogs), 'Freight': r2(freight), 'Duty': r2(duty),
        'ImportTax': r2(import_tax), 'Landed': r2(landed), 'Ads': r2(ads), 'Returns': r2(returns), 'Other': r2(other),
        'Profit': r2(profit), 'Margin': round(profit / net_sales, 4) if net_sales else None,
        'ROI': round(profit / landed, 4) if landed > 0 else None,
        'BreakEvenACoS': round((profit + ads) / price, 4) if price else None,
        'tier': tier.get('tier'), 'fee_source': source, 'fx': rate, 'warnings': warnings,
    }
    out['waterfall'] = [[k, out[k]] for k in ('NetSales', 'Referral', 'FBA', 'Storage', 'Placement', 'FeeTax', 'Landed', 'Ads',
                                              'Returns', 'Other') if out[k]] + [['Profit', out['Profit']]]
    return out


def price_grid(inp, lo=0.6, hi=1.4, steps=17, table=None):
    """Profit across prices from lo to hi x the price; 'cliff' marks a fee step between two neighbouring prices."""
    p0 = _num(inp.get('price'))
    if not p0:
        return []
    market = fees.norm_market(inp.get('market')) or 'US'
    out, prev = [], None
    for i in range(steps):
        p = round(p0 * (lo + (hi - lo) * i / (steps - 1)), 2)
        e = unit_economics({**inp, 'price': p}, table)
        state = fees.threshold_state(market, inp.get('category') or '', p, True, table)
        out.append({'price': p, 'profit': e['Profit'], 'margin': e['Margin'], 'cliff': prev is not None and state != prev})
        prev = state
    return out


def max_cogs(inp, target_margin, table=None):
    """The highest product cost (US$) that still leaves target_margin of net sales. Profit is linear in COGS."""
    a = unit_economics({**inp, 'cogs_usd': 0.0, 'cogs': None}, table)
    b = unit_economics({**inp, 'cogs_usd': 1.0, 'cogs': None}, table)
    if not a or not b:
        return None
    slope = b['Profit'] - a['Profit']                                    # change in profit per US$ of cost
    need = target_margin * a['NetSales']
    if slope >= 0:
        return None
    x = (need - a['Profit']) / slope
    return round(max(0.0, x), 2)


def monthly_profit(unit_profit, captured_units):
    """Profit per month at the units you might capture (a number or an est dict with lo/hi)."""
    if unit_profit is None or captured_units is None:
        return None
    if isinstance(captured_units, dict):
        v, lo, hi = (_num(captured_units.get(k)) for k in ('v', 'lo', 'hi'))
    else:
        v = lo = hi = _num(captured_units)
    f = lambda u: round(unit_profit * u, 2) if u is not None else None   # noqa: E731
    return {'v': f(v), 'lo': f(lo), 'hi': f(hi), 'basis': 'estimated', 'conf': 'low'}
