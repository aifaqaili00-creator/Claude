"""Analyse a Helium 10 Xray export: is this market worth entering?

Xray runs inside your own Chrome (the Helium 10 extension), on an Amazon search page.
Export it to CSV and this module reads it. The app can also find new exports in your Downloads folder by itself.
"""
import os
import re
import time
from collections import Counter
from pathlib import Path

import file_rank as fr

REFERRAL = 0.15            # Amazon's referral fee for most categories


def downloads_dir():
    if os.environ.get('PC_DOWNLOADS'):
        return Path(os.environ['PC_DOWNLOADS'])
    try:                                                     # Windows: the real Downloads folder (can be on OneDrive/D:)
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as k:
            p = winreg.QueryValueEx(k, '{374DE290-123F-4565-9164-39C4925E467B}')[0]
            p = Path(os.path.expandvars(p))
            if p.is_dir():
                return p
    except (ImportError, OSError):
        pass
    return Path.home() / 'Downloads'


def _header(path):
    try:
        if path.suffix.lower() == '.csv':
            for enc in ('utf-8-sig', 'cp1252'):
                try:
                    with open(path, encoding=enc) as f:
                        line = f.readline()
                        if line.startswith('#'):
                            line = f.readline()
                        return line.lower()
                except UnicodeDecodeError:
                    continue
        else:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            row = next(wb.active.iter_rows(max_row=1, values_only=True), ())
            wb.close()
            return ','.join(str(c or '') for c in row).lower()
    except Exception:
        return ''
    return ''


def looks_like_export(path):
    h = _header(Path(path))
    return 'asin' in h and ('product details' in h or 'title' in h) and ('sales' in h or 'revenue' in h)


def find_new_export(since):
    """Newest Helium 10 product export saved in Downloads after `since` (epoch seconds), or None."""
    folder = downloads_dir()
    try:
        files = [p for p in folder.iterdir() if p.suffix.lower() in ('.csv', '.xlsx') and p.is_file()]
    except OSError:
        return None
    now = time.time()
    files = [p for p in files if since < p.stat().st_mtime < now - 1]          # finished writing
    for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:15]:
        if looks_like_export(p):
            return p
    return None


def guess_keyword(titles):
    """The words most listings share, e.g. 'moving bags'."""
    words = Counter()
    for t in titles[:30]:
        seen = set()
        for w in re.findall(r"[a-z][a-z'-]+", str(t).lower()):
            if len(w) > 2 and w not in fr.FILLER and w not in seen:
                seen.add(w)
                words[w] += 1
    common = [w for w, n in words.most_common(4) if n >= max(3, len(titles[:30]) // 3)]
    if not common:
        return ''
    first = str(titles[0]).lower()
    return ' '.join(sorted(common[:3], key=lambda w: first.find(w) if w in first else 999))


def _med(s):
    s = s.dropna()
    return float(s.median()) if len(s) else None


def left_per_sale(market, price, fba_fee, category='', registered=False):
    """What is left per sale after Amazon's referral and FBA fees (and GST/VAT), before product cost and shipping."""
    from engine import fees
    if price != price or price is None or fba_fee != fba_fee or fba_fee is None:
        return float('nan')
    tax = (fees.market_table(market) or {}).get('tax') or {}
    v, v_fees = float(tax.get('price') or 0), float(tax.get('fees') or 0)
    net = price / (1 + v) if registered else price
    amazon = (fees.referral_fee(market, category or '', price) or price * REFERRAL) + fba_fee
    return net - amazon - (0.0 if registered else v_fees * amazon)


def analyse(path, market='auto', keyword='', registered=False):
    mk, d = fr.prepare(path, market)
    T = fr.TARGETS[mk]
    d = fr.score(d, T)
    if 'asin' in d and d['asin'].str.len().gt(0).any():
        d = d.drop_duplicates('asin')
    d['is_ad'] = d['sponsored'].str.lower().isin(['yes', 'true', '1', 'sponsored', 'y'])
    d['amazon'] = d.apply(fr.amazon_sells, axis=1)
    d['new'] = d['age'].between(0, 12)
    d['beatable'] = (d['sales'] >= T['sales']) & (d['reviews'].fillna(0) <= T['reviews'])
    d['left'] = [left_per_sale(mk, p, f, c, registered)                         # per unit, before product + shipping
                 for p, f, c in zip(d['price'], d['fba_fees'], d['category'])]
    organic = d[~d['is_ad']]
    base = (organic if len(organic) >= 5 else d).sort_values('revenue', ascending=False).reset_index(drop=True)
    top10 = base.head(10)
    total_rev = float(base['revenue'].sum())
    cur = T['currency']

    brand_rev = base[base['brand'] != ''].groupby('brand')['revenue'].sum().sort_values(ascending=False)
    top_brand = brand_rev.index[0] if len(brand_rev) else ''
    top_brand_share = float(brand_rev.iloc[0] / total_rev) if len(brand_rev) and total_rev else 0.0
    top3_share = float(top10['revenue'].head(3).sum() / total_rev) if total_rev else 0.0
    new_winners = base[base['new'] & (base['sales'] >= T['sales'])]
    fulfil = Counter(f.upper() for f in base['fulfillment'] if f)
    countries = Counter(c.upper() for c in base['seller_country'] if c and c.upper() not in ('N/A', 'NA', '-'))
    cn_share = countries.get('CN', 0) / sum(countries.values()) if countries else None
    oversize = int(base['size_tier'].str.lower().str.contains('oversize').sum())
    has_fees = base['fba_fees'].notna().any()
    left_med = _med(top10['left']) if has_fees else None
    left_pct = (left_med / _med(top10['price'])) if left_med is not None and _med(top10['price']) else None

    m = {
        'listings': int(len(d)), 'sponsored': int(d['is_ad'].sum()), 'organic': int(len(base)),
        'total_revenue': total_rev, 'total_sales': float(base['sales'].sum()),
        'top10_sales_median': _med(top10['sales']), 'top10_sales_avg': float(top10['sales'].mean()) if len(top10) else None,
        'price_median': _med(base['price']), 'price_p25': float(base['price'].quantile(.25)) if base['price'].notna().any() else None,
        'price_p75': float(base['price'].quantile(.75)) if base['price'].notna().any() else None,
        'rating_avg': float(base['rating'][base['rating'] > 0].mean()) if (base['rating'] > 0).any() else None,
        'review_barrier': _med(top10['reviews']), 'reviews_median': _med(base['reviews']),
        'top3_share': top3_share, 'top_brand': top_brand, 'top_brand_share': top_brand_share,
        'new_winners': int(len(new_winners)), 'beatable': int(base['beatable'].sum()),
        'amazon_total': int(base['amazon'].sum()), 'amazon_top10': int(top10['amazon'].sum()),
        'fulfillment': dict(fulfil), 'countries': dict(countries.most_common(6)), 'cn_share': cn_share,
        'oversize': oversize, 'left_median': left_med, 'left_pct': left_pct,
        'fees_median': _med(top10['fba_fees']) if has_fees else None,
        'trend_median': _med(top10['trend']),
    }

    # ---- verdict: points for demand, review barrier, concentration, newcomers, Amazon, margin ----
    pts, reasons = 0, []

    def say(level, text):
        reasons.append({'level': level, 'text': text})

    s = m['top10_sales_median'] or 0
    if s >= 2 * T['sales']:
        pts += 2; say('good', 'Strong demand: the top 10 sell a median of %d a month (target %d).' % (s, T['sales']))
    elif s >= T['sales']:
        pts += 1; say('warn', 'Enough demand: the top 10 sell a median of %d a month (target %d).' % (s, T['sales']))
    else:
        say('bad', 'Low demand: the top 10 sell a median of only %d a month (target %d).' % (s, T['sales']))

    rb = m['review_barrier'] or 0
    if rb <= T['reviews']:
        pts += 2; say('good', 'Low review barrier: the top 10 have a median of %d reviews.' % rb)
    elif rb <= 4 * T['reviews']:
        pts += 1; say('warn', 'Medium review barrier: the top 10 have a median of %d reviews.' % rb)
    else:
        say('bad', 'High review barrier: the top 10 have a median of %d reviews. Hard to rank against.' % rb)

    if top3_share < .45:
        pts += 2; say('good', 'Spread out: the top 3 listings take %d%% of the revenue.' % round(top3_share * 100))
    elif top3_share < .65:
        pts += 1; say('warn', 'Somewhat concentrated: the top 3 take %d%% of the revenue.' % round(top3_share * 100))
    else:
        say('bad', 'Dominated: the top 3 take %d%% of the revenue.' % round(top3_share * 100))
    if top_brand_share >= .5:
        pts -= 1; say('bad', 'One brand (%s) earns %d%% of the revenue.' % (top_brand, round(top_brand_share * 100)))

    nw = m['new_winners']
    if nw >= 3:
        pts += 2; say('good', '%d listings under a year old already sell %d+ a month: newcomers can win here.' % (nw, T['sales']))
    elif nw >= 1:
        pts += 1; say('warn', '%d listing under a year old sells %d+ a month.' % (nw, T['sales']) if nw == 1 else
                      '%d listings under a year old sell %d+ a month.' % (nw, T['sales']))
    else:
        say('bad', 'No listing under a year old sells %d+ a month yet.' % T['sales'])

    if m['amazon_top10'] >= 3:
        pts -= 1; say('bad', 'Amazon itself sells %d of the top 10.' % m['amazon_top10'])
    elif m['amazon_top10'] == 0:
        pts += 1; say('good', 'Amazon itself does not sell in the top 10.')
    else:
        say('warn', 'Amazon itself sells %d of the top 10.' % m['amazon_top10'])

    if left_pct is not None:
        if left_pct >= .45:
            pts += 1; say('good', 'After Amazon fees about %s %.2f (%d%%) is left per sale for product cost and shipping.' % (cur, left_med, round(left_pct * 100)))
        elif left_pct < .30:
            pts -= 1; say('bad', 'After Amazon fees only about %s %.2f (%d%%) is left per sale.' % (cur, left_med, round(left_pct * 100)))
        else:
            say('warn', 'After Amazon fees about %s %.2f (%d%%) is left per sale.' % (cur, left_med, round(left_pct * 100)))
    if oversize:
        say('warn', '%d listings are oversize: higher FBA and storage fees.' % oversize)
    if cn_share is not None and cn_share >= .6:
        say('warn', '%d%% of sellers are based in China: expect price competition.' % round(cn_share * 100))
    flagged = Counter(f for fl in top10['flags'] for f in str(fl).split(', ')
                      if f and f not in ('cheap', 'expensive', 'Amazon sells'))       # Amazon has its own line above
    if flagged:
        say('warn', 'Watch out, the top 10 include: %s.' % ', '.join('%s (%d)' % kv for kv in flagged.most_common(3)))

    verdict = 'Promising' if pts >= 7 else 'Possible' if pts >= 4 else 'Hard'
    level = {'Promising': 'good', 'Possible': 'warn', 'Hard': 'bad'}[verdict]
    keyword = keyword or guess_keyword(list(base['title']))

    rows = []
    for i, r in base.iterrows():
        rows.append({
            'pos': i + 1, 'asin': r['asin'], 'title': r['title'], 'brand': r['brand'], 'url': r['url'],
            'price': r['price'], 'sales': r['sales'], 'revenue': r['revenue'], 'reviews': r['reviews'],
            'rating': r['rating'], 'age': r['age'], 'fulfillment': r['fulfillment'], 'country': r['seller_country'],
            'fba_fees': r['fba_fees'], 'left': r['left'], 'size_tier': r['size_tier'], 'amazon': bool(r['amazon']),
            'beatable': bool(r['beatable']), 'new': bool(r['new']),
            'flags': [f for f in str(r['flags']).split(', ') if f], 'verdict': r['verdict'],
        })
    return {'market': mk, 'name': fr.MARKET_NAMES[mk], 'currency': cur, 'target': T, 'keyword': keyword,
            'file': Path(path).name, 'verdict': verdict, 'level': level, 'points': pts, 'metrics': m,
            'reasons': reasons, 'rows': rows, 'analysed_at': time.time()}


def export_excel(a, path):
    import pandas as pd
    m, cur = a['metrics'], a['currency']
    pct = lambda v: '' if v is None else '%d%%' % round(v * 100)
    money = lambda v: '' if v is None else '%s %.2f' % (cur, v)
    summary = [
        ('Keyword', a['keyword']), ('Market', a['name']), ('File', a['file']),
        ('Verdict', '%s (%d points)' % (a['verdict'], a['points'])),
        ('Listings (sponsored)', '%d (%d)' % (m['listings'], m['sponsored'])),
        ('Total revenue / month', money(m['total_revenue'])),
        ('Top 10 median sales / month', m['top10_sales_median']),
        ('Price range (middle half)', '%s - %s' % (money(m['price_p25']), money(m['price_p75']))),
        ('Review barrier (top 10 median)', m['review_barrier']),
        ('Top 3 revenue share', pct(m['top3_share'])),
        ('Biggest brand', '%s (%s)' % (m['top_brand'], pct(m['top_brand_share']))),
        ('New listings selling well', m['new_winners']), ('Beatable listings', m['beatable']),
        ('Amazon sells (top 10)', m['amazon_top10']),
        ('Left per sale after fees (median)', '%s (%s)' % (money(m['left_median']), pct(m['left_pct']))),
    ] + [('Reason', r['text']) for r in a['reasons']]
    with pd.ExcelWriter(path, engine='openpyxl') as xw:
        pd.DataFrame(summary, columns=['What', 'Value']).to_excel(xw, sheet_name='Summary', index=False)
        rows = pd.DataFrame(a['rows'])
        rows['flags'] = rows['flags'].map(', '.join)
        rows.to_excel(xw, sheet_name='Listings', index=False)
        xw.sheets['Summary'].column_dimensions['A'].width = 34
        xw.sheets['Summary'].column_dimensions['B'].width = 90
        xw.sheets['Listings'].column_dimensions['C'].width = 60
