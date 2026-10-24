"""Shortlist: one click from a product you found (Market Gaps, Out of stock, Gap Finder) to the Board, with its numbers.

The listing is read on Amazon (its product page, kept two days, or the SP-API): price, package size and weight, rating and
reviews, sellers, and the sales a month its sales rank stands for. With those, the most you can pay your supplier and keep
20% (Amazon's fees for that size, sea freight, duty, ads and returns), and, when you saved a supplier price for it (Profit?),
the profit a sale. The product goes on the Board as an Idea (its keyword watched, so the research keeps coming), with these
numbers as its first note. The page opens the Alibaba search for it at the same time.
"""
import asyncio
import time

import amazon_check as ac
import board
import config
import fxrates
import gapfinder
import storage
import throttle
from engine import curve as curvemod
from engine import profit

MARGIN = 0.20


def _n(x, d=0):
    return '{:,.{}f}'.format(x, d)


def facts_of(page, listing, units):
    """What the note says about the listing, from its product page reading and gapfinder.read_listing's answer."""
    p = page or {}
    dims = p.get('pkg_cm') or [p.get('pkg_l_cm'), p.get('pkg_w_cm'), p.get('pkg_h_cm')]
    return {'price': p.get('price') or (listing or {}).get('price'), 'dims_cm': dims if dims and all(dims) else None,
            'kg': p.get('pkg_kg'), 'rating': p.get('rating'), 'reviews': p.get('reviews'), 'rank': p.get('root_rank'),
            'category': p.get('root_name'), 'units': units, 'sellers': (listing or {}).get('n_offers'),
            'brand': p.get('brand'), 'available': (listing or {}).get('available')}


def numbers(facts, market, settings, cogs_usd=None, today=None):
    """{'pay_usd': the most to pay a piece and keep 20%, 'profit': unit economics at your supplier price or None}."""
    if not facts.get('price'):
        return {'pay_usd': None, 'profit': None}
    inp = {'market': market, 'price': facts['price'], 'category': facts.get('category') or '', 'dims_cm': facts.get('dims_cm'),
           'kg': facts.get('kg'), 'registered': bool((settings.get('registered') or {}).get(market)),
           'date': today or config.market_today(market).isoformat()}
    pay = profit.max_cogs(inp, MARGIN)
    econ = profit.unit_economics({**inp, 'cogs_usd': cogs_usd}) if cogs_usd else None
    return {'pay_usd': pay, 'profit': econ}


def note_text(facts, nums, market, source, cogs_usd=None):
    """The card's first note: everything known about the product when you shortlisted it, in one paragraph."""
    cur = ac.MARKETS[market]['currency']
    money = lambda x: cur + ('' if cur.endswith('$') else ' ') + _n(x, 2)  # noqa: E731 - A$45.99, AED 129.00
    name = ac.MARKETS[market]['name']
    bits = []
    if facts.get('price'):
        bits.append('%s on Amazon %s' % (money(facts['price']), name))
    if facts.get('available') is False:
        bits.append('out of stock now')
    if facts.get('kg'):
        bits.append('%s kg' % _n(facts['kg'], 2))
    if facts.get('dims_cm'):
        bits.append('%s cm' % ' × '.join(_n(x, 0) for x in facts['dims_cm']))
    if facts.get('units'):
        bits.append('about %s sold a month%s' % (_n(facts['units']), ' (sales rank #%s)' % _n(facts['rank']) if facts.get('rank') else ''))
    if facts.get('sellers'):
        bits.append('%d seller%s' % (facts['sellers'], '' if facts['sellers'] == 1 else 's'))
    if facts.get('rating'):
        bits.append('%s★ (%s reviews)' % (_n(facts['rating'], 1), _n(facts.get('reviews') or 0)))
    out = 'Shortlisted%s: %s.' % (' from ' + source if source else '', ' · '.join(bits) or 'the listing could not be read')
    if nums.get('pay_usd'):
        out += ' To keep %d%%: pay your supplier at most US$%s a piece (Amazon fees%s, sea freight, duty, ads and returns).' % (
            MARGIN * 100, _n(nums['pay_usd'], 2), ' for this size' if facts.get('dims_cm') else ' for a small parcel (size not read)')
    elif facts.get('price'):
        out += ' At this price nothing is left for the product after Amazon fees, freight and ads: check it.'
    econ = nums.get('profit')
    if econ and cogs_usd:
        out += ' With your supplier price US$%s: about %s profit a sale (%s%% margin).' % (
            _n(cogs_usd, 2), money(econ['Profit']), _n((econ.get('Margin') or 0) * 100))
    return out


async def run(S, d, progress=None):
    """Shortlist one product: d = {market, asin, title, kw, image, source}. Returns {'card', 'exists', 'summary', 'facts',
    'pay_usd', 'profit'}. A listing that could not be read still goes on the board (the note says so)."""
    say = progress or (lambda m: None)
    market = d.get('market') if d.get('market') in config.MARKETS else 'AU'
    asin = str(d.get('asin') or '').strip().upper() or None
    page, listing, units = None, {}, None
    if asin:
        say('Reading the listing on Amazon %s…' % ac.MARKETS[market]['name'])
        try:
            listing = await gapfinder.read_listing(S, market, asin, getattr(S, 'spapi', None), {})
        except (ac.BlockedError, throttle.ThrottleError) as e:
            listing = {'status': str(e)}
        page = listing.get('page') or S.db.read(storage.latest_page, market, asin, time.time() - 14 * 86400)
        cv = S.db.read(storage.get_curve, market)
        rank = (page or {}).get('root_rank') or (listing.get('catalog') or {}).get('rank')
        u = curvemod.predict(cv, (page or {}).get('root_node'), rank) if cv and rank else None
        units = int(round(u['v'])) if u and u.get('v') else None
    facts = facts_of(page, listing, units)
    cogs = (S.db.read(storage.prefs, market, [asin]).get(asin) or {}).get('cogs_usd') if asin else None
    await asyncio.to_thread(fxrates.rates)
    nums = numbers(facts, market, S.settings, cogs)
    note = note_text(facts, nums, market, d.get('source'), cogs)
    say('Adding it to the board…')
    card = S.db.write(board.add_card, {'title': d.get('title') or (page or {}).get('title') or asin, 'market': market,
                                       'asin': asin, 'kw': d.get('kw') or '', 'image': d.get('image') or (page or {}).get('image')},
                      S.settings)
    S.db.write(board.add_note, card['id'], note)
    board.bump()
    S.sync_tasks()                                       # its keyword is watched now: the research keeps coming
    head = ('Already on your board as PB-%d: numbers added' if card.get('exists') else 'Shortlisted as PB-%d') % card['id']
    return {'card': card['id'], 'exists': bool(card.get('exists')), 'summary': head + '. ' + note, 'facts': facts,
            'pay_usd': nums['pay_usd'], 'profit': (nums['profit'] or {}).get('Profit')}
