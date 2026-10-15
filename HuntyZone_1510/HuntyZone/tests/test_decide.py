"""Go or no-go (engine/decide.py), the first year week by week (engine/yearplan.py), the report that gathers their facts,
the routes, and the AI tool."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_tools  # noqa: E402
import reports  # noqa: E402
import storage  # noqa: E402
from engine import decide as dc  # noqa: E402
from engine import yearplan as yp  # noqa: E402
from test_board import NOW, S, search  # noqa: E402
from test_board import db as db  # noqa: E402,F401  (the fixture)
from test_http import call  # noqa: E402
from test_http import server as server  # noqa: E402,F401  (the fixture)

GOOD = {'market': 'AU', 'searched': True, 'price': 39.0, 'review_barrier': 120, 'page1_units': 2400, 'margin': 0.28,
        'amazon_share': 0.0, 'amazon_read': 8, 'risk_flags': [], 'season': {'strength': 0.2, 'reliable': True, 'peak': 'Dec'},
        'season_read': True, 'fast': 6, 'trend': 'Growing', 'score': 72, 'score_label': 'Strong'}


def rule(d, key):
    return next(x for x in d['rules'] if x['key'] == key)


# ---------- go or no-go ----------
def test_a_clean_niche_is_a_go_with_its_rules_recorded():
    d = dc.decide(GOOD)
    assert d['tier'] == 'go' and d['label'] == 'Go' and d['confidence'] == 'high' and d['tone'] == 'good'
    assert all(x['state'] == 'pass' for x in d['rules']) and 'opportunity 72/100 (Strong)' in d['why']
    assert d['used']['price_band'] == {'on': True, 'limit': [20, 110]} and len(d['fingerprint']) == 8
    assert dc.decide(GOOD)['fingerprint'] == d['fingerprint'] != dc.decide({**GOOD, 'market': 'US'})['fingerprint']
    json.dumps(d, allow_nan=False)


@pytest.mark.parametrize('change, key, words', [
    ({'price': 12.0}, 'price_band', 'under A$ 20'), ({'price': 150.0}, 'price_band', 'over A$ 110'),
    ({'review_barrier': 3000}, 'review_wall', '3,000 reviews'), ({'page1_units': 120}, 'demand_floor', 'at least 120'),
    ({'margin': 0.08}, 'margin_floor', 'margin is 8%'), ({'amazon_share': 0.5}, 'amazon_sells', 'Amazon itself sells 50%'),
    ({'risk_flags': ['medical']}, 'restricted', 'medical'), ({'season': {'strength': 0.8, 'reliable': True, 'peak': 'Dec'}},
                                                             'one_season', 'one season')])
def test_any_failed_deal_breaker_is_a_no_go(change, key, words):
    d = dc.decide({**GOOD, **change})
    assert d['tier'] == 'no_go' and rule(d, key)['state'] == 'fail' and words in rule(d, key)['why']
    assert rule(d, key)['fix'] and d['why'].startswith('No-go: ')


def test_warnings_unknowns_and_scores():
    d = dc.decide({**GOOD, 'fast': 30})
    assert d['tier'] == 'maybe' and rule(d, 'fast_stock')['state'] == 'fail' and 'warning: 30 listings' in d['why']
    assert dc.decide({**GOOD, 'fast': 30, 'trend': 'Declining'})['tier'] == 'weak'
    assert dc.decide({**GOOD, 'score': 45})['tier'] == 'maybe' and dc.decide({**GOOD, 'score': 20})['tier'] == 'weak'
    blind = dc.decide({**GOOD, 'page1_units': None, 'amazon_share': None, 'margin': None, 'after_amazon': None,
                       'season_read': False})
    assert [x['key'] for x in blind['rules'] if x['state'] == 'unknown'] == ['demand_floor', 'margin_floor', 'amazon_sells',
                                                                            'one_season']
    assert blind['tier'] == 'maybe' and blind['confidence'] == 'low' and '4 not read yet' in blind['why']   # never a pass
    seasonal_noise = dc.decide({**GOOD, 'season': {'strength': 0.9, 'reliable': False}})
    assert rule(seasonal_noise, 'one_season')['state'] == 'pass'                   # a pattern that does not repeat
    assert dc.decide({**GOOD, 'amazon_share': 0.9, 'amazon_read': 3})['tier'] == 'go'                   # 3 read: unknown
    no_cost = dc.decide({**GOOD, 'margin': None, 'after_amazon': 0.2})
    assert rule(no_cost, 'margin_floor')['state'] == 'fail' and '20% of the price is left' in rule(no_cost, 'margin_floor')['why']
    assert rule(dc.decide({**GOOD, 'margin': None, 'after_amazon': 0.5}), 'margin_floor')['state'] == 'unknown'
    gone = dc.decide({'market': 'AE', 'searched': False})
    assert gone['tier'] == 'unknown' and gone['rules'] == [] and 'Search this keyword first' in gone['why']


def test_your_rules_are_checked_merged_and_change_the_fingerprint():
    r = dc.merge_rules({'review_wall': {'limit': {'AU': 50, 'XX': 1}}, 'price_band': {'limit': {'AU': [90, 10]}},
                        'margin_floor': {'limit': {'US': 7}}, 'restricted': {'limit': {'AU': ['kids/toy', 'rockets']}},
                        'fast_stock': {'on': False}, 'nope': {'on': False}})
    assert r['review_wall']['limit'] == {'US': 2000, 'AU': 50, 'AE': 500} and r['price_band']['limit']['AU'] == [20, 110]
    assert r['margin_floor']['limit']['US'] == 1.0 and r['restricted']['limit']['AU'] == ['kids/toy'] and 'nope' not in r
    d = dc.decide(GOOD, r)
    assert d['tier'] == 'no_go' and rule(d, 'review_wall')['state'] == 'fail' and rule(d, 'fast_stock')['state'] == 'off'
    assert d['fingerprint'] != dc.decide(GOOD)['fingerprint']
    assert dc.changes(r) == {'review_wall': {'limit': {'AU': 50}}, 'margin_floor': {'limit': {'US': 1.0}},
                             'restricted': {'limit': {'AU': ['kids/toy']}}, 'fast_stock': {'on': False}}
    assert dc.changes(dc.merge_rules()) == {}


# ---------- the first year ----------
ECON = {'price': 40.0, 'Profit': 8.0, 'Ads': 4.8, 'Landed': 9.0, 'COGS': 5.0, 'currency': 'A$'}
NEW = {'v': 300.0, 'lo': 150.0, 'hi': 450.0, 'why': 'test'}


def test_the_first_year_in_three_cases():
    p = yp.plan(ECON, NEW, 500, 70, '2027-01-04')
    assert p['status'] == 'ok' and not p['seasonal'] and len(p['assumptions']) == 6
    g, li, po = (p['cases'][k] for k in ('good', 'likely', 'poor'))
    assert g['profit_year'] > li['profit_year'] > po['profit_year']
    assert g['money_back_week'] < li['money_back_week']
    w = li['weeks']
    assert len(w) == 52 and w[0]['day'] == '2027-01-04' and w[0]['units'] == pytest.approx(300 * 12 / 52 * 0.2, abs=0.1)
    assert w[0]['ads'] == pytest.approx(w[0]['revenue'] * 0.35, abs=0.01) and w[20]['ads'] == pytest.approx(w[20]['revenue'] * 0.10, abs=0.01)
    assert -500 * 9 < w[0]['cash'] < -4000 and li['most_money_needed'] >= 500 * 9          # the first order is paid first
    assert li['reorders'] >= 1 and any(x['reorder'] for x in w)
    assert sum(x['units'] for x in w) == pytest.approx(li['units_year'], abs=1)
    assert po['price'] == 38.0
    json.dumps(p, allow_nan=False)


def test_the_season_and_a_late_reorder_move_sales():
    si = [0.5] * 6 + [1.5] * 6                                          # Jul-Dec sells three times Jan-Jun
    p = yp.plan(ECON, NEW, 200, 120, '2027-01-04', si)
    w = p['cases']['likely']['weeks']
    assert p['seasonal'] and w[30]['units'] > w[15]['units'] * 2
    late = p['cases']['good']
    assert late['stockout_weeks'] > 0 and late['first_stockout_week'] and late['lost_year'] > 0
    assert yp.plan(ECON, NEW, 200, 120, '2027-01-04', [1, 2])['seasonal'] is False      # not 12 months: no season


def test_the_first_year_needs_a_cost_and_sales():
    assert yp.plan({**ECON, 'COGS': 0}, NEW, 300, 70, '2027-01-04')['status'] == 'needs_cost'
    assert yp.plan(ECON, None, 300, 70, '2027-01-04')['status'] == 'unknown_sales'
    loss = yp.plan({**ECON, 'Profit': -6.0}, NEW, 300, 70, '2027-01-04')['cases']['likely']
    assert loss['profit_year'] < 0 and loss['money_back_week'] is None


# ---------- the report, the routes and the AI tool ----------
def test_the_decision_report_reads_what_was_saved(db):
    search(db)
    r = reports.decision(db, 'US', 'calming dog bed', S, 6.0, 300, now=NOW)
    f, d = r['facts'], r['decision']
    assert f['searched'] and f['price'] and f['review_barrier'] is not None and f['margin'] is not None
    assert d['tier'] in ('go', 'maybe', 'weak', 'no_go') and len(d['rules']) == len(dc.RULES)
    assert rule(d, 'amazon_sells')['state'] == 'unknown'                        # no product page read
    assert r['plan']['status'] in ('ok', 'unknown_sales')
    none = reports.decision(db, 'AU', 'calming dog bed', S, now=NOW)
    assert none['decision']['tier'] == 'unknown' and none['plan'] is None
    first = db.write(reports.record_decision, 'US', 'calming dog bed', d, NOW)
    assert first is None and db.write(reports.record_decision, 'US', 'calming dog bed', d, NOW + 5) is None
    other = {**d, 'tier': 'no_go', 'label': 'No-go', 'fingerprint': 'abcdef12'}
    prev = db.write(reports.record_decision, 'US', 'calming dog bed', other, NOW + 9)
    assert prev['tier'] == d['tier'] and prev['rules_changed'] and prev['at'] == NOW + 5


def test_the_routes_and_the_ai_tool(server):
    assert call(server, 'POST', '/api/decide', {'market': 'AU', 'kw': ''})[0] == 400
    st, _, raw = call(server, 'POST', '/api/decide', {'market': 'AU', 'kw': 'never searched here', 'cogs': '3'})
    out = json.loads(raw)
    assert st == 200 and out['decision']['tier'] == 'unknown' and out['previous'] is None
    st, _, raw = call(server, 'GET', '/api/decide/rules')
    r = json.loads(raw)
    assert st == 200 and r['rules']['review_wall']['limit']['AU'] == 800 and 'medical' in r['restricted']
    st, _, raw = call(server, 'POST', '/api/decide/rules', {'rules': {'review_wall': {'limit': {'AU': 900}}}})
    st, _, raw = call(server, 'POST', '/api/decide/rules', {'rules': {'review_wall': {'on': False}}})
    rw = json.loads(raw)['rules']['review_wall']
    assert rw['limit']['AU'] == 900 and rw['on'] is False                      # one change does not undo the other
    assert server.db.read(storage.get_state, reports.RULES_KEY) == {'review_wall': {'on': False, 'limit': {'AU': 900}}}
    st, _, raw = call(server, 'POST', '/api/decide/rules', {'reset': True})
    assert json.loads(raw)['rules']['review_wall']['on'] and server.db.read(storage.get_state, reports.RULES_KEY) == {}
    c = ai_tools.Ctx(server.db, server.settings, None)
    with pytest.raises(ai_tools.ToolError, match='product_cost_usd'):
        ai_tools.call(c, 'go_no_go', {'keyword': 'dog bed', 'market': 'AU', 'product_cost_usd': 'cheap'})
    t = ai_tools.call(c, 'go_no_go', {'keyword': 'never searched here', 'market': 'AU'})
    assert t['decision'] == 'Not searched' and 'nothing was fetched' in t['source']
