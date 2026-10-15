"""Trend radar and product ideas scored by engine.discovery from market.db: two weeks of list history, Google Trends
matched by whole phrases in the same market, product pages and Helium 10 rows, unknowns kept unknown."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import amazon_check as ac  # noqa: E402
import config  # noqa: E402
import ideas  # noqa: E402
import reports  # noqa: E402
import storage  # noqa: E402
from test_storage import ROWS  # noqa: E402

NOW = time.time()
DAY = 86400


def code(*nums):
    """A made-up word per number pair, so filler titles share no words."""
    return ''.join('bcdfghjklm'[int(c)] for c in ''.join('%02d' % n for n in nums))


@pytest.fixture
def db(tmp_path):
    d = storage.DB(tmp_path / 'm.db').open()
    yield d
    d.close()


def history(db, market='US', days=14):
    """Daily Movers & Shakers scans: a tower fan climbs gently every day, a fidget spinner jumps on the last three days
    only, and 40 other products come and go."""
    for d in range(days):
        items = [{'asin': 'FAN', 'title': 'Bladeless Tower Fan Quiet', 'rank_now': 2000 - d * 40, 'rank_before': 2100 - d * 40,
                  'pct': 5, 'price': 45.0, 'reviews': 40}]
        if d >= days - 3:
            items.append({'asin': 'SPIN', 'title': 'Fidget Spinner Metal', 'rank_now': 40 - (d - days + 3) * 10,
                          'rank_before': 2000, 'pct': 5000, 'price': 9.99, 'reviews': 25000})
        items += [{'asin': 'X%d_%d' % (d, k), 'title': 'Gadget %s' % code(d, k), 'rank_now': 900, 'rank_before': 1000,
                   'pct': 10, 'price': 20.0} for k in range(40)]
        db.write(storage.save_list, market, 'movers', 'home-garden', 'Home & Kitchen', items, 'ok', None,
                 NOW - (days - 1 - d) * DAY)


def trend(db, term, geo, score, label='Growing'):
    db.write(storage.save_trends_feature, term, geo, {'status': 'ok', 'label': label, 'score': score, 'q': 0.8})


def by_asin(r):
    return {i['asin']: i for i in r['items']}


def part(item, key):
    return next(p for p in item['opportunity']['parts'] if p['key'] == key)


def test_the_radar_ranks_a_steady_riser_with_rising_searches_above_a_fad_spike(db):
    history(db)
    trend(db, 'tower fan', 'US', 78)
    trend(db, 'fidget spinner', 'US', 18, 'Fad')
    r = reports.radar(db, 'US', now=NOW, limit=1000)
    json.dumps(r, allow_nan=False)
    it = by_asin(r)
    fan, spin = it['FAN'], it['SPIN']
    assert spin['surge'] > fan['surge']                                     # the old ranking put the spike first
    assert r['items'][0]['asin'] == 'FAN' and fan['opportunity']['score'] > spin['opportunity']['score']
    assert fan['trend']['term'] == 'tower fan' and fan['trend']['match'] == 'part' and fan['trend_phrase'] == 'tower fan'
    assert spin['opportunity']['risk']['flags'] == ['search fad']
    assert fan['presence'] == [1] * 14 and spin['presence'] == [0] * 11 + [1, 1, 1]
    assert r['presence_days'][-1] == config.market_day(NOW, 'US') and len(r['presence_days']) == 14
    assert fan['label'] == 'Rising' and len(fan['spark']) >= 3                # the old fields stay


def test_trends_count_only_in_their_own_market_and_missing_signals_stay_unknown(db):
    history(db)
    trend(db, 'tower fan', 'AU', 90)
    fan = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']
    assert fan['trend'] is None and fan['trend_phrase'] == 'bladeless tower fan'
    assert part(fan, 'S')['known'] is False and part(fan, 'S')['v'] is None
    assert part(fan, 'D')['known'] is False and part(fan, 'D')['v'] is None    # no badge, Helium 10 or curve yet
    assert fan['opportunity']['known'] == 3 and fan['opportunity']['conf'] == 'medium'
    cov = reports.radar(db, 'US', now=NOW, limit=5)['coverage']
    assert cov['n'] == 5 and cov['known']['A'] == 5 and cov['known']['S'] == 0 and cov['known']['D'] == 0


def test_product_pages_and_helium10_fill_demand(db):
    history(db)
    db.write(storage.save_product, 'US', 'FAN', {'title': 'Bladeless Tower Fan Quiet', 'status': 'ok', 'badge_low': 1000,
                                                 'root_rank': 500, 'root_node': 'home-garden', 'reviews': 42, 'price': 45.0},
             NOW - DAY)
    db.write(storage.save_product, 'US', 'X13_0', {'title': 'Random', 'status': 'ok', 'badge_low': 500}, NOW - 40 * DAY)
    db.write(storage.save_import, 'xray', 'US', 'fidget spinner', 'f.csv', 'sha-f',
             [{'asin': 'SPIN', 'title': 'Fidget Spinner', 'sales_asin': 5000, 'fba_fees': 3.5}], NOW - DAY)
    it = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))
    fan, spin = part(it['FAN'], 'D'), part(it['SPIN'], 'D')
    assert fan['src'] == 'badge' and fan['raw']['units'] == pytest.approx(1386.3, abs=1) and fan['known']
    assert spin['src'] == 'h10' and spin['raw']['units'] == 5000 and spin['conf'] == 'medium'
    assert part(it['SPIN'], 'E')['src'] == 'h10'                               # Helium 10's FBA fee
    assert part(it['X13_0'], 'D')['known'] is False                            # a 40-day-old page says nothing today


def test_ideas_are_scored_from_the_list_history(db):
    history(db)
    trend(db, 'tower fan', 'US', 78)

    def idea(asin, title, kind, **kw):
        return {'asin': asin, 'title': title, 'image': '', 'market': 'US', 'url': '', 'category': 'Home & Kitchen',
                'kind': kind, 'slug': 'home-garden', 'deal': False, 'rank': 3, 'pct': None, 'rank_now': None,
                'rank_before': None, 'rating': 4.5, 'reviews': 40, 'price': 45.0, **kw}
    scored = ideas.score_ideas([idea('FAN', 'Bladeless Tower Fan Quiet', 'movers', pct=5), idea('NEW1', 'Bamboo Bath Caddy Tray', 'new')],
                               'US', False)
    out = reports.ideas_discovery(db, 'US', scored, now=NOW)
    json.dumps(scored, allow_nan=False)
    by = {i['asin']: i for i in scored}
    assert part(by['FAN'], 'A')['src'] == 'lists' and part(by['FAN'], 'A')['conf'] == 'high'
    assert part(by['FAN'], 'S')['known'] and by['FAN']['presence'] == [1] * 14
    new = part(by['NEW1'], 'A')
    assert new['src'] == 'one reading' and new['conf'] == 'low' and new['raw']['new_rank'] == 3
    assert by['NEW1']['presence'] == [None] * 14
    assert by['FAN']['score'] is not None and by['FAN']['label'] and by['FAN']['lists'] == ['movers']   # old fields kept
    assert out['coverage'] == {'n': 2, 'known': {'A': 2, 'S': 1, 'D': 0, 'C': 2, 'E': 2}}
    assert out['presence_days'][-1] == config.market_day(NOW, 'US')


# ---- the phrase shoppers type, groups, market gaps, qualifying ----

def suggest(db, market, seed, words, at=NOW - DAY):
    db.write(storage.save_suggest, market, seed, [{'keyword': w, 'hits': 1, 'best': n} for n, w in enumerate(words, 1)],
             1, 0, 'ok' if words else 'empty', None, at)


def serp(db, market, kw, at=NOW - DAY):
    s = ac.summarize(market, kw, ac.MARKETS[market]['expect'][0], [dict(r) for r in ROWS], 3, 9, at)
    db.write(storage.save_serp, s, 'qualify', None, 1, None)


def test_the_phrase_shoppers_type_replaces_the_title_words(db):
    history(db)
    trend(db, 'bladeless tower fan', 'US', 70)
    fan = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']
    assert fan['phrase_src'] == 'title' and fan['search'] == 'Bladeless Tower Fan Quiet' and fan['phrase_at'] is None
    suggest(db, 'US', 'bladeless tower', ['bladeless tower fan', 'bladeless tower fan with remote'], NOW - 2 * DAY)
    suggest(db, 'US', 'tower fan', ['tower fan', 'tower fans for bedroom'])
    fan = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']
    assert fan['search'] == 'bladeless tower fan' and fan['phrase_src'] == 'amazon' and fan['phrase_at'] is None
    assert fan['title_search'] == 'Bladeless Tower Fan Quiet' and fan['trend']['match'] == 'shopper'
    suggest(db, 'US', 'fan quiet', [])                                      # every start asked: when is known
    fan = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']
    assert fan['phrase_at'] == pytest.approx(NOW - 2 * DAY)
    item = {'asin': 'FAN', 'title': 'Bladeless Tower Fan Quiet', 'search': 'Bladeless Tower Fan Quiet', 'price': 45.0}
    mine = reports.ideas_discovery(db, 'US', [item], now=NOW, phrases={'FAN': 'quiet fan'})['ideas'][0]
    assert mine['search'] == 'quiet fan' and mine['phrase_src'] == 'you'
    db.write(storage.set_pref, 'US', 'FAN', phrase='small tower fan')                # a phrase you chose is kept
    fan = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']
    assert fan['search'] == 'small tower fan' and fan['phrase_src'] == 'you'
    mine = reports.ideas_discovery(db, 'US', [dict(item)], now=NOW, phrases={'FAN': 'quiet fan'})['ideas'][0]
    assert mine['search'] == 'quiet fan'                                     # the words in the box win


def test_rows_show_the_highest_supplier_price_and_use_yours(db):
    history(db)
    fan = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']
    assert fan['buy']['max_usd'] > 0 and fan['buy']['margin_pct'] == 20 and fan['buy']['cost_usd'] is None
    s30 = {**config.defaults(), 'target_margin_pct': 30}
    assert by_asin(reports.radar(db, 'US', now=NOW, limit=1000, settings=s30))['FAN']['buy']['max_usd'] < fan['buy']['max_usd']
    db.write(storage.set_pref, 'US', 'FAN', cogs_usd=5.0)
    mine = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']
    e = part(mine, 'E')
    assert mine['buy']['cost_usd'] == 5.0 and mine['buy']['margin'] == e['raw']['margin'] and 'your cost' in e['why']


def test_not_for_me_hides_only_that_product_and_stars_mark_look_alikes(db):
    history(db)

    def idea(asin, title, **kw):
        return {'asin': asin, 'title': title, 'search': title, 'market': 'US', 'kind': 'new', 'slug': 'home-garden',
                'deal': False, 'category': 'Home & Kitchen', 'reviews': 40, 'price': 45.0, 'url': 'u/' + asin, 'image': '', **kw}
    items = [idea('FAN', 'Bladeless Tower Fan Quiet', kind='movers', pct=5), idea('FAN2', 'Bladeless Tower Fan Quiet Remote', rank=30),
             idea('FAN3', 'Bladeless Tower Fan Quiet Timer', rank=40), idea('NEW1', 'Bamboo Bath Caddy Tray', rank=3),
             idea('NEW2', 'Bamboo Bath Tray Organizer Wood', rank=9)]
    db.write(storage.upsert_asins, 'US', [{'asin': 'NEW1', 'title': 'Bamboo Bath Caddy Tray'}])
    db.write(storage.set_pref, 'US', 'FAN2', hidden=True)
    db.write(storage.set_pref, 'US', 'NEW1', liked=True)
    out = reports.ideas_discovery(db, 'US', items, now=NOW)
    by = {i['asin']: i for i in out['ideas']}
    assert 'FAN2' not in by and [m['asin'] for m in by['FAN']['members']] == ['FAN3']      # the group keeps the others
    assert [h['asin'] for h in out['hidden']] == ['FAN2'] and by['FAN']['pref']['liked'] is False
    assert by['NEW1']['pref'] == {'liked': True, 'fit': 0.0, 'like_of': None}
    assert by['NEW2']['pref'] == {'liked': False, 'fit': 0.5, 'like_of': 'Bamboo Bath Caddy Tray'}


def test_where_to_launch_compares_the_searched_markets(db):
    serp(db, 'US', 'calming dog bed')
    serp(db, 'AU', 'calming dog bed')
    r = reports.launch(db, 'US', 'Calming Dog Bed', config.defaults(), cogs_usd=4.0, units=200)
    json.dumps(r, allow_nan=False)
    us, au, ae = (r['markets'][m] for m in ('US', 'AU', 'AE'))
    assert ae == {'searched': False} and us['gap'] is None and au['gap']['score'] is not None
    assert us['sim']['units'] == 200 and us['newcomer']['v'] > 0 and us['max_usd'] > 0 and au['price'] == us['price']
    assert r['pick'] is None or r['pick']['market'] in ('US', 'AU')
    no_cost = reports.launch(db, 'US', 'calming dog bed', config.defaults())
    assert no_cost['markets']['US']['sim'] is None and no_cost['markets']['US']['max_usd'] == us['max_usd']
    hot = reports.launch(db, 'US', 'calming dog bed', config.defaults(), boom={'trend_score': 95.0, 'cluster_surge': 95.0})
    cold = reports.launch(db, 'US', 'calming dog bed', config.defaults(), boom={'trend_score': 5.0, 'cluster_surge': None})
    assert hot['markets']['AU']['gap']['score'] > cold['markets']['AU']['gap']['score']


def test_rows_say_when_the_wave_reaches_the_next_markets(db):
    from engine import wave
    history(db)
    months = lambda vals: [[wave.add_months('2022-01', i), v] for i, v in enumerate(vals)]       # noqa: E731
    rise = months([10] * 48 + [10, 10, 20, 30, 45, 60, 70, 80, 85, 90, 95, 100])
    later = months([10] * 51 + [10, 10, 20, 30, 45, 60, 70, 80, 85])                            # three months behind
    feat = lambda m: {'status': 'ok', 'label': 'Growing', 'score': 80, 'q': 0.8, 'monthly': m}     # noqa: E731
    db.write(storage.save_trends_feature, 'tower fan', 'US', feat(rise))
    fan = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']
    assert fan['trend']['term'] == 'tower fan' and fan['wave']['AU']['status'] == 'early'
    assert 'US searches took off in May 2026' in fan['wave']['AU']['why']
    for term in ('dog bed', 'sun hat', 'yoga mat'):                                           # three rose in both
        db.write(storage.save_trends_feature, term, 'US', feat(rise))
        db.write(storage.save_trends_feature, term, 'AU', feat(later))
    w = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']['wave']['AU']
    assert w['status'] in ('coming', 'due') and w['eta'] == ['2026-08', '2026-08']
    db.write(storage.save_suggest, 'AE', 'bladeless tower fan quiet',
             [{'keyword': 'bladeless tower fan quiet', 'hits': 1, 'best': 1}], 1, 0, 'ok', None, NOW - DAY)
    ae = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']['wave']['AE']
    assert ae['status'] == 'here' and ae['why'].startswith('Amazon suggests it to shoppers in the UAE since')


def test_rising_google_searches_become_candidates(db):
    rel = lambda q, v, b=False, kind='rising': {'kind': kind, 'query': q, 'value': v, 'breakout': b}     # noqa: E731
    db.write(storage.save_trends_related, 'tower fan', 'US', 'today 12-m',
             [rel('cordless tower fan', 0, True), rel('quiet tower fan', 400), rel('tower fan with remote', 350),
              rel('tower fan', 120), rel('fan for bedroom', 50), rel('dog bed', 900), rel('tower fans top', 100, kind='top')],
             NOW - DAY)
    db.write(storage.save_trends_related, 'dog bed', 'US', 'today 12-m', [rel('Cordless  Tower Fan', 0, True)], NOW - DAY)
    db.write(storage.save_trends_related, 'old term', 'US', 'today 12-m', [rel('ancient thing', 0, True)], NOW - 40 * DAY)
    db.write(storage.add_watch, 'keyword', 'US', 'tower fan with remote')
    out = reports.google_rising(db, 'US', config.defaults(), now=NOW)
    json.dumps(out, allow_nan=False)
    assert [(f['query'], f['breakout'], f['rise']) for f in out] == [('cordless tower fan', True, 0), ('quiet tower fan', False, 400)]
    assert sorted(out[0]['from']) == ['dog bed', 'tower fan'] and out[1]['gap'] == {'AU': None, 'AE': None}
    serp(db, 'AU', 'quiet tower fan')
    serp(db, 'US', 'cordless tower fan')
    out = {f['query']: f for f in reports.google_rising(db, 'US', config.defaults(), now=NOW)}
    assert out['quiet tower fan']['gap']['AU']['score'] is not None and out['quiet tower fan']['gap']['AE'] is None
    assert out['cordless tower fan']['opportunity']['score'] is not None and out['quiet tower fan']['opportunity'] is None


def test_similar_listings_fold_into_the_best_one(db):
    history(db)

    def idea(asin, title, **kw):
        return {'asin': asin, 'title': title, 'market': 'US', 'kind': 'new', 'slug': 'home-garden', 'deal': False,
                'category': 'Home & Kitchen', 'reviews': 40, 'price': 45.0, 'url': 'u/' + asin, 'image': '', **kw}
    items = [idea('FAN', 'Bladeless Tower Fan Quiet', kind='movers', pct=5),
             idea('FAN2', 'Bladeless Tower Fan Quiet Remote', rank=30), idea('FAN3', 'Bladeless Tower Fan Quiet Timer', rank=40),
             idea('NEW1', 'Bamboo Bath Caddy Tray', rank=3)]
    for i in items:
        i['search'] = i['title']
    out = reports.ideas_discovery(db, 'US', items, now=NOW)
    json.dumps(out, allow_nan=False)
    assert [i['asin'] for i in out['ideas']] == ['FAN', 'NEW1'] and out['coverage']['n'] == 2
    fan = out['ideas'][0]
    assert fan['similar'] == 2 and {m['asin'] for m in fan['members']} == {'FAN2', 'FAN3'}
    assert all(m['score'] is not None and m['url'] for m in fan['members']) and fan['presence'] == [1] * 14
    assert 'with 2 similar listings' in part(fan, 'A')['why'] and out['ideas'][1]['members'] == []


def test_a_market_gap_is_scored_once_the_phrase_was_searched_there(db):
    history(db)
    fan = by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']
    assert fan['gap'] == {'AU': None, 'AE': None}
    serp(db, 'AU', 'Bladeless Tower Fan Quiet', NOW - 70 * DAY)                # too old to say anything
    assert by_asin(reports.radar(db, 'US', now=NOW, limit=1000))['FAN']['gap']['AU'] is None
    serp(db, 'AU', 'Bladeless Tower Fan Quiet')
    suggest(db, 'AU', 'bladeless tower fan quiet', ['bladeless tower fan quiet'])
    r = reports.radar(db, 'US', now=NOW, limit=1)
    json.dumps(r, allow_nan=False)
    g = by_asin(r)['FAN']['gap']
    assert g['AE'] is None and g['AU']['score'] is not None and g['AU']['label'] and g['AU']['phrase'] == 'bladeless tower fan quiet'
    assert g['AU']['fast_local'] is not None and g['AU']['searched_at'] == pytest.approx(NOW - DAY)
    assert any(w.startswith('US Amazon lists surge') for w in g['AU']['why'])
    assert any('Australia autocomplete knows the term' in w for w in g['AU']['why'])
    assert r['targets'] == ['AU', 'AE'] and reports.radar(db, 'AU', now=NOW)['targets'] == ['AE']


def test_qualify_takes_the_best_safe_rows_that_still_miss_something(db):
    history(db)
    for d in range(14):
        db.write(storage.save_list, 'US', 'movers', 'bath', 'Bath', [
            {'asin': 'CADDY', 'title': 'Bamboo Bath Caddy Tray', 'rank_now': 300 - d * 10, 'rank_before': 400, 'pct': 30,
             'price': 35.0, 'reviews': 12}], 'ok', None, NOW - (13 - d) * DAY)
    s = config.defaults()
    got = [r['asin'] for r in reports.qualify_candidates(db, 'US', s, now=NOW, n=3, checks=1)]
    assert got[0] == 'CADDY' and 'FAN' not in got and len(got) == 3            # a fan is electrical: left out
    db.write(storage.save_product, 'US', 'CADDY', {'title': 'Bamboo Bath Caddy Tray', 'status': 'ok', 'reviews': 12},
             NOW - DAY)
    for p in ('bamboo bath', 'bath caddy', 'caddy tray'):
        suggest(db, 'US', p, ['bath caddy tray'] if p == 'bath caddy' else [])
    trend(db, 'bath caddy tray', 'US', 60)
    assert 'CADDY' in [r['asin'] for r in reports.qualify_candidates(db, 'US', s, now=NOW, n=3, checks=1)]
    serp(db, 'AU', 'bath caddy tray')
    serp(db, 'AE', 'bath caddy tray')
    assert 'CADDY' not in [r['asin'] for r in reports.qualify_candidates(db, 'US', s, now=NOW, n=3, checks=1)]
