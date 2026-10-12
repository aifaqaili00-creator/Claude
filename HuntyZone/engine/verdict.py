"""The report in one plain sentence, from numbers the report already has. Pure function."""
import countries

WHERE = {'AU': 'Australia', 'AE': 'the UAE', 'US': 'the USA'}
RISING, FALLING = ('Growing', 'Emerging', 'Breakout'), ('Declining', 'Fad')


def _about(v):
    return int(round(v, -2)) if v >= 1000 else int(round(v, -1)) if v >= 100 else int(round(v))


def _money(cur, v):
    return '%s%s%s' % (cur, '' if cur.endswith('$') else ' ', '{:,.0f}'.format(v))


def sentence(market, label=None, units=None, fast_local=None, dominant=None, amazon=None, price=None, profit=None,
             left=None, cur='', trend=None, searched=True):
    """'Worth a look in Australia: about 2,400 sales a month on page 1, only 3 fast local sellers, mostly Chinese FBA
    sellers and about A$15 profit a sale at A$45.' At most four facts, most telling first; unknown ones are left out.
    profit: per sale with your product cost; left: what the price leaves for product and shipping when no cost is set."""
    where = WHERE.get(market, market)
    if not searched:
        return 'Not searched in %s yet: press Refresh now to see how well it sells and who sells it.' % where
    facts = []
    if units:
        facts.append('about {:,} sales a month on page 1'.format(_about(units)))
    if fast_local is not None:
        facts.append('no fast local sellers' if not fast_local else
                     'only %d fast local seller%s' % (fast_local, '' if fast_local == 1 else 's') if fast_local <= 4 else
                     '%d fast local sellers' % fast_local)
    if amazon and amazon >= 3:
        facts.append('Amazon itself sells %d of the top listings' % amazon)
    elif dominant:
        adj, ships = countries.adjective(dominant['code']), 'FBA ' if dominant.get('ships') == 'fba' else ''
        facts.append('mostly %s %ssellers' % (adj, ships) if adj else
                     'mostly %ssellers based in %s' % (ships, countries.name(dominant['code'])))
    if price:
        if profit is not None:
            facts.append(('about %s profit a sale at %s' if profit >= 0 else 'a loss of about %s a sale at %s')
                         % (_money(cur, abs(profit)), _money(cur, price)))
        elif left is not None:
            facts.append('about %s of the %s price left for the product and shipping' % (_money(cur, max(0, left)),
                                                                                         _money(cur, price)))
    base = (trend or '').split(',')[0].strip()
    if base in RISING:
        facts.append('searches rising')
    elif base in FALLING:
        facts.append('searches falling')
    facts = facts[:4]
    lead = '%s in %s' % (label, where) if label else where[0].upper() + where[1:]
    if not facts:
        return lead + '.'
    return '%s: %s.' % (lead, facts[0] if len(facts) == 1 else ', '.join(facts[:-1]) + ' and ' + facts[-1])
