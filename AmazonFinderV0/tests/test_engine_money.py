"""Fees, profit, scores and the launch planner (Phase 4 engine)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import fees, planner, profit, scores  # noqa: E402

SMALL = ((20, 15, 5), 0.2)
MID = ((30, 20, 10), 0.5)


def fba(market, price, date, size=MID):
    return fees.fba_fee(market, fees.size_tier(market, *size), price, date)['fee']


def test_fee_steps_at_price_thresholds():
    assert fba('US', 10.49, '2026-07-01', SMALL) > fba('US', 9.99, '2026-07-01', SMALL)
    assert fba('US', 29.99, '2026-11-20') > fba('US', 29.99, '2026-07-01')              # peak season adder
    assert fba('US', 29.99, '2027-01-20') == fba('US', 29.99, '2026-07-01')            # peak over
    au = [fba('AU', p, '2026-07-01', SMALL) for p in (12.0, 14.0)]
    ae = [fba('AE', p, '2026-07-01', SMALL) for p in (24.0, 26.0)]
    assert au[0] != au[1] and ae[0] != ae[1]
    th = fees.price_thresholds('US', 'Home')
    assert any(t['kind'] == 'fba' and t['price'] == 10 for t in th)


def test_referral_tiers_and_minimums():
    assert fees.referral_fee('AU', 'Sports & Outdoors', 100) == pytest.approx(13.0)
    assert fees.referral_fee('AU', 'Sports & Outdoors', 300) < 0.13 * 300
    assert fees.referral_fee('US', 'Home & Kitchen', 1.0) >= 0.30
    assert fees.referral_fee('AE', 'Home', 5) >= 1.0
    assert fees.fees_age_days('2026-10-10') == 1


def base(market, **kw):
    return {'market': market, 'price': 39.99, 'category': 'Home & Kitchen', 'dims_cm': MID[0], 'kg': MID[1], 'cogs_usd': 6.0,
            'date': '2026-07-01', **kw}


def test_profit_adds_up():
    e = profit.unit_economics(base('US'))
    parts = sum(e[k] for k in ('Referral', 'FBA', 'Storage', 'Placement', 'FeeTax', 'Landed', 'Ads', 'Returns', 'Other'))
    assert e['Profit'] == pytest.approx(e['NetSales'] - parts, abs=0.03)
    assert e['Duty'] == pytest.approx(0.25 * 6.0, abs=0.01) and e['fee_source'] == 'table'
    assert any('duty' in w.lower() for w in e['warnings'])
    assert e['BreakEvenACoS'] == pytest.approx((e['Profit'] + e['Ads']) / 39.99, abs=1e-3)


def test_gst_registered_vs_not_in_australia():
    reg = profit.unit_economics(base('AU', registered=True))
    not_reg = profit.unit_economics(base('AU', registered=False))
    assert reg['NetSales'] == pytest.approx(39.99 / 1.1, abs=0.01) and reg['FeeTax'] == 0 and reg['ImportTax'] == 0
    assert not_reg['NetSales'] == 39.99 and not_reg['FeeTax'] > 0 and not_reg['ImportTax'] > 0
    assert reg['COGS'] == pytest.approx(6.0 * profit.fx('AU'), abs=0.01)


def test_duty_base_fob_vs_cif():
    au = profit.unit_economics(base('AU'))
    ae = profit.unit_economics(base('AE', price=149.0))
    assert au['Duty'] == pytest.approx(0.05 * au['COGS'], abs=0.01)
    assert ae['Duty'] == pytest.approx(0.05 * (ae['COGS'] + ae['Freight']), abs=0.01)


def test_air_costs_more_than_sea_and_overrides_win():
    sea, air = profit.unit_economics(base('US')), profit.unit_economics(base('US', freight_mode='air'))
    assert air['Freight'] > sea['Freight']
    assert profit.unit_economics(base('US', fba_fee_override=1.0))['FBA'] == 1.0
    h = profit.unit_economics(base('US', h10_fba_fee=7.5))
    assert h['FBA'] == 7.5 and h['fee_source'] == 'h10'
    assert profit.unit_economics({'market': 'US', 'price': 0}) is None


def test_max_cogs_inverts_profit():
    inp = base('US')
    m = profit.max_cogs(inp, 0.20)
    e = profit.unit_economics({**inp, 'cogs_usd': m})
    assert e['Margin'] == pytest.approx(0.20, abs=0.005)


def test_price_grid_marks_the_fee_cliff():
    g = profit.price_grid({**base('US'), 'price': 10.0, 'dims_cm': SMALL[0], 'kg': SMALL[1]}, steps=21)
    assert any(r['cliff'] for r in g) and g[0]['price'] < 10 < g[-1]['price']
    assert profit.monthly_profit(5.0, {'v': 100, 'lo': 50, 'hi': 200})['hi'] == 1000.0


def metrics(**kw):
    m = {'units_top10_median': 300, 'momentum': 0.6, 'review_barrier': 150, 'hhi': 0.12, 'top3_share': 0.4,
         'sponsored_share': 0.2, 'n_selling': 12, 'osd_share': 0.5, 'fast_local': 4, 'margin': 0.25, 'risk_flags': [],
         'market': 'AU', 'target': {'currency': 'A$', 'price': (25, 70), 'sales': 100, 'reviews': 230}}
    m.update(kw)
    return m


def test_opportunity_moves_the_right_way():
    base_s = scores.opportunity(metrics())['score']
    assert scores.opportunity(metrics(fast_local=15, osd_share=0.05))['score'] < base_s
    assert scores.opportunity(metrics(margin=0.40))['score'] > base_s
    assert scores.opportunity(metrics(units_top10_median=0))['score'] < base_s / 2
    assert scores.opportunity(metrics(risk_flags=['electrical', 'kids/toy']))['score'] < base_s
    o = scores.opportunity(metrics())
    assert o['label'] in ('Strong', 'Worth a look', 'Weak', 'Skip') and o['why']
    s1, s2 = scores.saturation(metrics())['score'], scores.saturation(metrics(review_barrier=3000))['score']
    assert s2 > s1


def test_arbitrage_prefers_gaps_before_the_wave():
    us = {'trend_score': 80, 'cluster_surge': 70}
    t = {'market': 'AU', 'autocomplete': True, 'trends_nonzero_share': 0.8, 'bought_mid_sum': 300, 'osd_share': 0.6,
         'fast_local': 2, 'margin': 0.3, 'trend_label': 'Evergreen'}
    a = scores.arbitrage(us, t)['score']
    assert scores.arbitrage(us, {**t, 'fast_local': 14, 'osd_share': 0.05})['score'] < a
    assert scores.arbitrage(us, {**t, 'trend_label': 'Growing', 'fast_local': 12})['score'] < a
    assert scores.arbitrage({'trend_score': 10}, t)['score'] < a


SI = [0.6, 0.6, 0.7, 0.8, 1.0, 1.3, 1.6, 1.5, 1.2, 0.9, 0.7, 0.6]


def test_planner_dates_and_branches():
    p = planner.plan(SI, 0.8, True, 7, '2025-11-01', market='US')
    assert p['status'] == 'ok' and p['upswing_month'] == 5
    assert p['sea']['in_stock_by'] == '2026-03-17' and p['sea']['order_by'] < p['air']['order_by']
    late = planner.plan(SI, 0.8, True, 7, '2026-01-15', market='US')
    assert late['status'] == 'too_late' and late['late_by_days'] > 0 and late['air']['order_by'] >= '2026-01-15'
    assert planner.plan(SI, 0.1, False, 7, '2026-01-15')['status'] == 'unknown'
    au = planner.plan(SI, 0.8, True, 7, '2025-11-01', market='AU')
    assert au['sea']['transit_days'] == 30 and au['sea']['check_in_days'] == 7
