"""Amazon seller fees per marketplace: size tier, FBA fulfilment fee, referral fee, storage, placement.

The numbers live in DEFAULTS, which holds the same data as fee_defaults.json (a test keeps the two
in step). The app loads that file, merges the user's own fees.json on top (merge_tables) and passes
the result as `table`. Every function falls back to DEFAULTS when no table is given.
`table` may be the whole file ({'markets': {...}}) or just one market's part of it.

All tables are editable estimates as of 2026-10-09, taken from web-search extracts. US is medium to
high confidence; AU and AE are low (their official pages came through garbled), so verify them.
Pure functions: no file or network I/O. Nothing here raises on missing data; it returns None instead.
"""
import copy
import math
import re
from datetime import date, datetime, timezone

CM_PER_IN = 2.54
KG_PER_LB = 0.45359237
CM3_PER_CUFT = 28316.846592
CM3_PER_M3 = 1e6

THRESH = {
    'weight_eps': 1e-9,       # slack when comparing a weight with a table limit (float noise only)
    'side_eps': 1e-6,         # same for side lengths
    'stale_days': 90,         # fee tables older than this should be re-checked
    'max_months': 36,         # storage is summed over at most this many months
}

MARKET_ALIASES = {'US': 'US', 'USA': 'US', 'AU': 'AU', 'AUS': 'AU', 'AE': 'AE', 'UAE': 'AE'}


def _row(mx, fee, peak=None, extra=None, note=None):
    """One weight row: fee per price band for shipping weights up to `mx` (None = no upper limit)."""
    r = {'max': mx, 'fee': fee}
    if peak is not None:
        r['peak'] = peak
    if extra is not None:
        r['extra'] = extra
    if note:
        r['note'] = note
    return r


def _extra(above, step, per_step):
    """Add per_step for every started `step` of shipping weight above `above`."""
    return {'above': above, 'step': step, 'per_step': per_step}


def _cat(key, label, match, tiers, mode='portion'):
    """Referral rule. mode 'portion': each rate applies to the part of the price inside its tier.
    mode 'whole': the tier the whole price falls in sets one rate for the whole price."""
    return {'key': key, 'label': label, 'match': match, 'mode': mode, 'tiers': tiers}


_FLAT15 = [[None, 0.15]]
_FLAT13 = [[None, 0.13]]
_FLAT14 = [[None, 0.14]]

DEFAULTS = {
    'version': 1,
    'as_of': '2026-10-09',
    'stale_after_days': 90,
    'source': ('Research brief of 2026-10-09, built from web-search extracts of official and third-party '
               'pages (no page was opened in full). Every number is an editable estimate.'),
    'fx_per_usd': {'USD': 1.0, 'AUD': 1.55, 'AED': 3.6725},
    'fx_note': 'Local currency per US$. AED is pegged at 3.6725; AUD is an editable estimate.',
    'profit_defaults': {'tacos': 0.12, 'returns': 0.025, 'months_storage': 2, 'other_per_unit': 0.0},
    'markets': {
        'US': {
            'currency': 'USD', 'symbol': 'US$', 'conf': 'medium',
            'verify_url': 'https://sell.amazon.com/pricing',
            'units': {'length': 'in', 'weight': 'lb'},
            'dim_divisor': 139,
            'price_bands': [{'limit': 10, 'lower_if': '<'}, {'limit': 50, 'lower_if': '<='}],
            'band_labels': ['under $10', '$10-50', 'over $50'],
            'default_band': 1,
            'surcharge_pct': 3.5, 'surcharge_from': '2026-04-17', 'surcharge_on_peak': True,
            'peak': {'from': '10-15', 'to': '01-14'},
            'tiers': [
                {'key': 'small_standard', 'label': 'Small standard', 'max_sides': [15, 12, 0.75],
                 'max_weight': 1.0, 'dim_weight': False, 'storage': 'standard', 'placement': 0.30,
                 'conf': 'high', 'peak': 0.20,
                 'rows': [
                     _row(0.125, [2.43, 3.32, 3.58], 0.19),
                     _row(0.25, [2.49, 3.42, 3.68], 0.19),
                     _row(0.375, [2.56, 3.45, 3.71], 0.20),
                     _row(0.5, [2.66, 3.54, 3.80], 0.20),
                     _row(0.625, [2.77, 3.68, 3.94], 0.21),
                     _row(0.75, [2.82, 3.78, 4.04], 0.21),
                     _row(0.875, [2.92, 3.91, 4.17], 0.22),
                     _row(1.0, [2.95, 3.96, 4.22], 0.22),
                 ]},
                {'key': 'large_standard', 'label': 'Large standard', 'max_sides': [18, 14, 8],
                 'max_weight': 20.0, 'dim_weight': True, 'storage': 'standard', 'placement': 0.30,
                 'conf': 'high', 'peak': 0.30,
                 'rows': [
                     _row(0.25, [2.91, 3.73, 3.99], 0.30),
                     _row(0.5, [3.13, 3.95, 4.21], 0.30),
                     _row(0.75, [3.38, 4.20, 4.46], 0.30),
                     _row(1.0, [3.78, 4.60, 4.86], 0.30),
                     _row(1.25, [4.22, 5.04, 5.30], 0.30),
                     _row(1.5, [4.60, 5.42, 5.68], 0.31),
                     _row(1.75, [4.75, 5.57, 5.83], 0.32),
                     _row(2.0, [5.00, 5.82, 6.08], 0.33),
                     _row(2.25, [5.10, 5.92, 6.18], 0.35),
                     _row(2.5, [5.28, 6.10, 6.36], 0.37),
                     _row(2.75, [5.44, 6.26, 6.52], 0.39),
                     _row(3.0, [5.85, 6.67, 6.93], 0.41),
                     _row(None, [6.15, 6.97, 7.23], 0.45, _extra(3.0, 0.25, 0.08)),
                 ]},
                {'key': 'small_bulky', 'label': 'Small bulky', 'max_sides': [37, 28, 20], 'max_len_girth': 130,
                 'max_weight': 50.0, 'dim_weight': True, 'min_side': 2, 'storage': 'oversize', 'placement': 0.68,
                 'conf': 'medium', 'peak': 1.04,
                 'rows': [_row(None, [6.78, 7.55, 7.55], 1.04, _extra(1.0, 1.0, 0.38))]},
                {'key': 'large_bulky', 'label': 'Large bulky', 'max_sides': [59, 33, 33], 'max_len_girth': 130,
                 'max_weight': 50.0, 'dim_weight': True, 'min_side': 2, 'storage': 'oversize', 'placement': 0.68,
                 'conf': 'medium', 'peak': 1.04,
                 'rows': [_row(None, [8.58, 9.35, 9.35], 1.04, _extra(1.0, 1.0, 0.38))]},
                {'key': 'extra_large', 'label': 'Extra-large', 'max_sides': None,
                 'max_weight': None, 'dim_weight': True, 'min_side': 2, 'storage': 'oversize', 'placement': 1.15,
                 'conf': 'low', 'peak': 1.04,
                 'note': 'Peak adder is a placeholder (no source found). Overmax surcharge not modelled.',
                 'rows': [
                     _row(50.0, [25.56, 26.33, 26.33], None, _extra(1.0, 1.0, 0.38)),
                     _row(70.0, [36.55, 37.32, 37.32], None, _extra(51.0, 1.0, 0.75)),
                     _row(150.0, [50.55, 51.32, 51.32], None, _extra(71.0, 1.0, 0.75)),
                     _row(None, [194.0, 194.95, 194.95], None, _extra(151.0, 1.0, 0.19)),
                 ]},
            ],
            'storage': {'unit': 'cuft', 'peak_months': [10, 11, 12],
                        'rates': {'standard': {'offpeak': 0.78, 'peak': 2.40},
                                  'oversize': {'offpeak': 0.56, 'peak': 1.40}}},
            'placement_default': 0.30,
            'tax': {'price': 0.0, 'fees': 0.0, 'import': 0.0},
            'referral': {
                'min_fee': 0.30,
                'categories': [
                    _cat('furniture', 'Furniture', ['furniture'], [[200, 0.15], [None, 0.10]]),
                    _cat('tools', 'Tools & Home Improvement', ['tool', 'home improvement', 'hardware'], _FLAT15),
                    _cat('sports', 'Sports & Outdoors', ['sport', 'sporting', 'outdoor', 'fitness', 'exercise'],
                         _FLAT15),
                    _cat('garden', 'Lawn & Garden', ['garden', 'lawn', 'patio'], _FLAT15),
                    _cat('pet', 'Pet Products', ['pet'], _FLAT15),
                    _cat('office', 'Office Products', ['office'], _FLAT15),
                    _cat('crafts', 'Arts, Crafts & Sewing', ['craft', 'arts', 'sewing'], _FLAT15),
                    _cat('toys', 'Toys & Games', ['toy', 'game'], _FLAT15),
                    _cat('beauty_health', 'Beauty, Health & Personal Care, Baby',
                         ['beauty', 'health', 'personal care', 'baby'], [[10, 0.08], [None, 0.15]], 'whole'),
                    _cat('grocery', 'Grocery & Gourmet', ['grocery', 'gourmet'], [[15, 0.08], [None, 0.15]], 'whole'),
                    _cat('kitchen', 'Kitchen', ['kitchen'], _FLAT15),
                    _cat('home', 'Home', ['home'], _FLAT15),
                ],
                'default': _cat('everything_else', 'Everything else', [], _FLAT15),
            },
            'freight': {'currency': 'USD', 'sea': {'rate_kg': 2.5, 'rate_cbm': 150.0},
                        'air': {'rate_kg': 6.5, 'divisor': 6000}},
            'duty': {'pct': 0.25, 'base': 'FOB', 'volatile': True,
                     'note': 'US duty on China-origin goods changed several times in 2026 (IEEPA struck down '
                             '2026-02-20, Section 122 10% Feb-Jul, new Section 301 actions). High risk: check.'},
            'incentives': [
                {'key': 'new_selection', 'label': 'FBA New Selection (2026)', 'default_on': False,
                 'note': 'Free storage and returns processing, inbound placement credits for new branded '
                         'parent ASINs. Terms conflict (90 or 120 days, 100 or 200 units).'},
                {'key': 'new_brand_bonus', 'label': 'New Seller Incentives / Brand Registry bonus',
                 'default_on': False, 'note': 'Commonly cited as 5% back on up to $1M of branded sales.'},
            ],
            'notes': [
                'Fees are per unit in US$, before the 3.5% fuel and logistics surcharge (from 2026-04-17).',
                'Weights in lb, sides in inches. Holiday peak adders apply 15 Oct - 14 Jan.',
                'Over $50 = $10-50 + 0.26; large standard under $10 = $10-50 - 0.82 for a few inferred cells.',
                'Bulky rows rely on fewer sources; one source still lists 2024-style Large Bulky $9.73 + $0.42/lb.',
            ],
        },
        'AU': {
            'currency': 'AUD', 'symbol': 'A$', 'conf': 'low',
            'verify_url': 'https://sell.amazon.com.au/pricing',
            'units': {'length': 'cm', 'weight': 'kg'},
            'dim_divisor': 4000,
            'price_bands': [{'limit': 13, 'lower_if': '<'}],
            'band_labels': ['under A$13', 'A$13 and over'],
            'default_band': 1,
            'surcharge_pct': 0.0, 'surcharge_from': None, 'surcharge_on_peak': False,
            'peak': None,
            'tiers': [
                {'key': 'small_envelope', 'label': 'Small envelope', 'max_sides': [20, 15, 1], 'max_weight': 0.075,
                 'dim_weight': False, 'storage': 'standard', 'placement': 0.0, 'conf': 'low',
                 'rows': [_row(0.075, [3.64, 4.55])]},
                {'key': 'standard_envelope', 'label': 'Standard envelope', 'max_sides': [33, 23, 2.5],
                 'max_weight': 0.475, 'dim_weight': False, 'storage': 'standard', 'placement': 0.0, 'conf': 'low',
                 'rows': [_row(0.075, [3.67, 4.58]), _row(0.225, [4.35, 5.26]), _row(0.475, [4.70, 5.61])]},
                {'key': 'large_envelope', 'label': 'Large envelope', 'max_sides': [33, 23, 5], 'max_weight': 0.975,
                 'dim_weight': False, 'storage': 'standard', 'placement': 0.0, 'conf': 'low',
                 'note': 'Middle row interpolated between the 6.78 and 8.29 extracts.',
                 'rows': [_row(0.225, [5.87, 6.78]), _row(0.475, [6.62, 7.53]), _row(0.975, [7.38, 8.29])]},
                {'key': 'parcel', 'label': 'Parcel', 'max_sides': [45, 34, 20], 'max_weight': 12.0,
                 'dim_weight': True, 'storage': 'standard', 'placement': 0.0, 'conf': 'low',
                 'rows': [
                     _row(0.25, [6.43, 7.34]),
                     _row(0.5, [6.73, 7.64]),
                     _row(1.0, [8.31, 9.22]),
                     _row(1.5, [8.77, 9.68]),
                     _row(2.0, [8.90, 9.81]),
                     _row(None, [9.27, 10.18], None, _extra(2.0, 1.0, 0.01),
                          'Per-kg add-on as extracted (A$0.01/kg) looks too low: verify.'),
                 ]},
                {'key': 'small_oversize', 'label': 'Small oversize', 'max_sides': [61, 46, 46], 'max_weight': 25.0,
                 'dim_weight': True, 'storage': 'oversize', 'placement': 0.0, 'conf': 'low',
                 'note': 'Size limits assumed; per-kg add-on as extracted (A$0.01/kg) looks too low: verify.',
                 'rows': [_row(None, [9.77, 10.68], None, _extra(2.0, 1.0, 0.01))]},
                {'key': 'standard_oversize', 'label': 'Standard oversize', 'max_sides': [120, 60, 60],
                 'max_weight': 30.0, 'dim_weight': True, 'storage': 'oversize', 'placement': 0.0, 'conf': 'low',
                 'note': 'No fee found in the research: enter it from the Revenue Calculator.',
                 'rows': [_row(None, None)]},
                {'key': 'large_oversize', 'label': 'Large oversize', 'max_sides': None, 'max_weight': None,
                 'dim_weight': True, 'storage': 'oversize', 'placement': 0.0, 'conf': 'low',
                 'note': 'No fee found in the research: enter it from the Revenue Calculator.',
                 'rows': [_row(None, None)]},
            ],
            'storage': {'unit': 'm3', 'peak_months': [10, 11, 12],
                        'rates': {'standard': {'offpeak': 37.00, 'peak': 51.80},
                                  'oversize': {'offpeak': 34.20, 'peak': 48.00}}},
            'placement_default': 0.0,
            'tax': {'price': 0.10, 'fees': 0.10, 'import': 0.10},
            'referral': {
                'min_fee': 0.0,
                'categories': [
                    _cat('tools', 'Tools & DIY', ['tool', 'home improvement', 'hardware', 'diy'], _FLAT13),
                    _cat('sports', 'Sports & Outdoors', ['sport', 'sporting', 'outdoor', 'fitness', 'exercise'],
                         [[200, 0.13], [None, 0.12]]),
                    _cat('garden', 'Lawn & Garden', ['garden', 'lawn', 'patio'], _FLAT13),
                    _cat('pet', 'Pet Products', ['pet'], [[200, 0.13], [None, 0.12]]),
                    _cat('office', 'Office Products', ['office'], _FLAT13),
                    _cat('crafts', 'Arts & Crafts', ['craft', 'arts', 'sewing'], _FLAT15),
                    _cat('kitchen', 'Kitchen', ['kitchen'], _FLAT13),
                    _cat('home', 'Home', ['home'], _FLAT13),
                ],
                'default': _cat('everything_else', 'Everything else', [], _FLAT15),
            },
            'freight': {'currency': 'USD', 'sea': {'rate_kg': 2.0, 'rate_cbm': 150.0},
                        'air': {'rate_kg': 6.0, 'divisor': 6000}},
            'duty': {'pct': 0.05, 'base': 'FOB', 'volatile': False,
                     'note': 'General duty 5%; often 0% under ChAFTA with a certificate of origin.'},
            'incentives': [
                {'key': 'nsi', 'label': 'Brand Registry new-brand offer', 'default_on': False,
                 'note': '5% referral credit on the first A$1.5M of branded sales.'},
                {'key': 'new_selection', 'label': 'FBA New Selection', 'default_on': False,
                 'note': 'Monthly storage waived on the first 30 units of a new standard-size parent ASIN for 90 days.'},
            ],
            'notes': [
                'Fees in A$ excluding GST. Fees cut on 2026-06-10. The under-A$13 column is inferred from a '
                'constant A$0.91 gap.',
                'Envelopes use unit weight; parcel and oversize use the greater of unit and L*W*H cm / 4000.',
                'Referral is charged on the price including delivery and GST; no minimum.',
            ],
        },
        'AE': {
            'currency': 'AED', 'symbol': 'AED', 'conf': 'low',
            'verify_url': 'https://sell.amazon.ae/pricing',
            'units': {'length': 'cm', 'weight': 'kg'},
            'dim_divisor': None,
            'price_bands': [{'limit': 25, 'lower_if': '<='}],
            'band_labels': ['AED 25 or less', 'over AED 25'],
            'default_band': 1,
            'surcharge_pct': 0.0, 'surcharge_from': None, 'surcharge_on_peak': False,
            'peak': None,
            'tiers': [
                {'key': 'small_envelope', 'label': 'Small envelope', 'max_sides': [20, 15, 1], 'max_weight': 0.1,
                 'dim_weight': False, 'storage': 'standard', 'placement': 0.0, 'conf': 'low',
                 'rows': [_row(0.1, [5.5, 7.5])]},
                {'key': 'standard_envelope', 'label': 'Standard envelope', 'max_sides': [33, 23, 2.5],
                 'max_weight': 0.5, 'dim_weight': False, 'storage': 'standard', 'placement': 0.0, 'conf': 'low',
                 'rows': [_row(0.5, [6.5, 8.5])]},
                {'key': 'large_envelope', 'label': 'Large envelope', 'max_sides': [33, 23, 5], 'max_weight': 1.0,
                 'dim_weight': False, 'storage': 'standard', 'placement': 0.0, 'conf': 'low',
                 'rows': [_row(1.0, [7.5, 9.5])]},
                {'key': 'standard_parcel', 'label': 'Standard parcel', 'max_sides': [45, 34, 26], 'max_weight': 12.0,
                 'dim_weight': False, 'storage': 'standard', 'placement': 0.0, 'conf': 'low',
                 'rows': [
                     _row(0.25, [7.2, 9.2]),
                     _row(0.5, [7.5, 9.5]),
                     _row(1.0, [8.5, 10.5]),
                     _row(2.0, [9.5, 11.5]),
                     _row(None, [10.5, 12.5], None, _extra(3.0, 1.0, 0.78)),
                 ]},
                {'key': 'oversize', 'label': 'Oversize', 'max_sides': None, 'max_weight': None,
                 'dim_weight': False, 'storage': 'oversize', 'placement': 0.0, 'conf': 'low',
                 'rows': [_row(None, [10.5, 12.5], None, _extra(1.0, 1.0, 1.0))]},
            ],
            'storage': {'unit': 'cuft', 'peak_months': [],
                        'rates': {'standard': {'offpeak': 2.0, 'peak': 2.0},
                                  'oversize': {'offpeak': 2.0, 'peak': 2.0}}},
            'placement_default': 0.0,
            'tax': {'price': 0.05, 'fees': 0.05, 'import': 0.05},
            'referral': {
                'min_fee': 1.0,
                'categories': [
                    _cat('tools', 'Tools & Home Improvement', ['tool', 'home improvement', 'hardware', 'diy'],
                         _FLAT15),
                    _cat('sports', 'Sports', ['sport', 'sporting', 'fitness', 'exercise'], _FLAT14),
                    _cat('garden', 'Outdoor & Garden', ['outdoor', 'garden', 'lawn', 'patio'], _FLAT15),
                    _cat('pet', 'Pet Products', ['pet'], [[50, 0.08], [None, 0.15]], 'whole'),
                    _cat('office', 'Office Products', ['office'], _FLAT14),
                    _cat('kitchen', 'Kitchen', ['kitchen'], _FLAT15),
                    _cat('home', 'Home', ['home'], _FLAT15),
                ],
                'default': _cat('everything_else', 'Everything else', [], _FLAT15),
            },
            'freight': {'currency': 'USD', 'sea': {'rate_kg': 1.5, 'rate_cbm': 100.0},
                        'air': {'rate_kg': 4.5, 'divisor': 6000}},
            'duty': {'pct': 0.05, 'base': 'CIF', 'volatile': False,
                     'note': 'GCC duty 5% on the CIF value, plus 5% import VAT (a cost unless VAT-registered).'},
            'incentives': [
                {'key': 'new_to_prime', 'label': 'New-to-Prime', 'default_on': False,
                 'note': 'Referral cut to 5% for 120 days on eligible new FBA listings.'},
            ],
            'notes': [
                'Fees in AED excluding 5% VAT. Units priced AED 25 or less pay AED 2 less (since 2024-08-01).',
                'Shipping weight is unit weight only. Envelope size limits are assumed; the envelope '
                'columns are inferred from the AED 2 rule.',
                'Referral minimum AED 1; pet 8% up to AED 50 comes from one source. No closing fee found.',
            ],
        },
    },
}


# ---- small parsing helpers ----

def _num(x):
    """x as a finite float, or None (None / NaN / inf / text / bools)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _pos(x):
    f = _num(x)
    return f if f is not None and f > 0 else None


def _r(x, nd=4):
    f = _num(x)
    return None if f is None else round(f, nd)


def to_date(x):
    """'YYYY-MM-DD' / 'YYYY-MM' / date / datetime / unix seconds -> date, else None."""
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        if not math.isfinite(x) or x <= 0:
            return None
        try:
            return datetime.fromtimestamp(x, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(x, str):
        s = x.strip()
        for fmt, n in (('%Y-%m-%d', 10), ('%Y-%m', 7)):
            try:
                return datetime.strptime(s[:n], fmt).date()
            except ValueError:
                continue
    return None


def _month(x):
    """Month number 1-12 from an int, a date or a 'YYYY-MM(-DD)' string; None otherwise."""
    if isinstance(x, (int, float)) and not isinstance(x, bool) and _num(x) is not None:
        m = int(x)
        return m if 1 <= m <= 12 and m == x else None
    d = to_date(x) if not isinstance(x, (int, float)) else None
    return d.month if d else None


def _sides(dims):
    """(l, w, h) -> sides sorted longest first, or None unless all three are positive numbers."""
    if not isinstance(dims, (list, tuple)) or len(dims) != 3:
        return None
    s = [_pos(v) for v in dims]
    if any(v is None for v in s):
        return None
    return sorted(s, reverse=True)


# ---- tables ----

def norm_market(market):
    """'US' / 'usa' / 'AU' / 'UAE' ... -> 'US' | 'AU' | 'AE', or None."""
    return MARKET_ALIASES.get(str(market or '').strip().upper()) if market is not None else None


def market_table(market, table=None):
    """The fee table of one market. `table` may be the whole file, a single market's table, or None."""
    if isinstance(table, dict) and 'tiers' in table:
        return table                                   # already one market's table
    m = norm_market(market)
    if m is None:
        return None
    src = table if isinstance(table, dict) and isinstance(table.get('markets'), dict) else DEFAULTS
    mt = src['markets'].get(m)
    return mt if isinstance(mt, dict) else None


def merge_tables(base, override):
    """Deep-merge a user's fee overrides onto the defaults. Dicts merge key by key; anything else
    (numbers, lists such as tiers or fee rows) is replaced whole. Neither input is changed."""
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(override, dict):
        return out
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_tables(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _tier_by_key(mt, key):
    for t in mt.get('tiers') or []:
        if t.get('key') == key:
            return t
    return None


def _fits(t, s, w):
    """Do sides s (longest first, table units) and unit weight w fit tier t?"""
    eps_s, eps_w = THRESH['side_eps'], THRESH['weight_eps']
    mx = t.get('max_sides')
    if mx and any(si > mi + eps_s for si, mi in zip(s, sorted(mx, reverse=True))):
        return False
    mw = t.get('max_weight')
    if mw is not None and w > mw + eps_w:
        return False
    lg = t.get('max_len_girth')
    if lg is not None and s[0] + 2 * (s[1] + s[2]) > lg + eps_s:
        return False
    return True


# ---- size tier ----

def size_tier(market, dims_cm=None, kg=None, table=None):
    """Size tier from product dimensions (cm, any order) and unit weight (kg).

    US works in inches and lb: dimensional weight = L*W*H / 139, with bulky tiers using at least 2 in
    for width and height. AU uses L*W*H cm / 4000 for parcel and oversize. AE bills unit weight only.
    Returns {'tier', 'label', 'ship_weight_kg', 'dim_weight_kg', 'unit_weight_kg', 'sides_cm',
    'volume_cm3', 'storage', 'conf'}; tier is None when the inputs are missing.
    """
    sides = _sides(dims_cm)
    w = _pos(kg)
    out = {'tier': None, 'label': None, 'ship_weight_kg': None, 'dim_weight_kg': None,
           'unit_weight_kg': _r(w, 6), 'sides_cm': [_r(v, 4) for v in sides] if sides else None,
           'volume_cm3': _r(sides[0] * sides[1] * sides[2], 4) if sides else None,
           'storage': None, 'conf': 'low'}
    mt = market_table(market, table)
    if mt is None or sides is None or w is None:
        return out
    inch = (mt.get('units') or {}).get('length') == 'in'
    lb = (mt.get('units') or {}).get('weight') == 'lb'
    s = [v / CM_PER_IN for v in sides] if inch else list(sides)
    wu = w / KG_PER_LB if lb else w
    tier = next((t for t in mt.get('tiers') or [] if _fits(t, s, wu)), None)
    if tier is None:
        return out                                     # the table has no catch-all tier
    div = _pos(mt.get('dim_divisor'))
    dim_u = None
    if div:
        ms = _num(tier.get('min_side')) or 0.0
        dim_u = s[0] * max(s[1], ms) * max(s[2], ms) / div
    ship_u = max(wu, dim_u) if (tier.get('dim_weight') and dim_u is not None) else wu
    to_kg = KG_PER_LB if lb else 1.0
    out.update(tier=tier.get('key'), label=tier.get('label'), ship_weight_kg=_r(ship_u * to_kg, 6),
               dim_weight_kg=_r(dim_u * to_kg, 6) if dim_u is not None else None,
               storage=tier.get('storage') or 'standard', conf=tier.get('conf') or mt.get('conf') or 'low')
    return out


# ---- price bands and peak ----

def _crossed(limits, p):
    """How many band limits price p has passed. lower_if '<' keeps p < limit in the lower band,
    '<=' keeps p <= limit in the lower band."""
    i = 0
    for b in limits:
        lim = _num(b.get('limit')) if isinstance(b, dict) else None
        if lim is None:
            continue
        lower = b.get('lower_if', '<')
        if (p >= lim) if lower == '<' else (p > lim):
            i += 1
        else:
            break
    return i


def price_band(market, price, table=None):
    """Index of the FBA price band (0 = cheapest). US: <10 | 10-50 | >50; AU: <13 | >=13; AE: <=25 | >25."""
    mt = market_table(market, table)
    p = _num(price)
    if mt is None or p is None or p < 0:
        return None
    return _crossed(mt.get('price_bands') or [], p)


def in_peak(market, date_str=None, table=None):
    """True when the date falls in the market's holiday peak window (US 15 Oct - 14 Jan, inclusive)."""
    mt = market_table(market, table)
    d = to_date(date_str)
    pk = (mt or {}).get('peak')
    if not pk or d is None:
        return False
    try:
        f = tuple(int(v) for v in str(pk['from']).split('-'))
        t = tuple(int(v) for v in str(pk['to']).split('-'))
    except (KeyError, TypeError, ValueError):
        return False
    md = (d.month, d.day)
    return (f <= md <= t) if f <= t else (md >= f or md <= t)


def _today():
    return datetime.now(timezone.utc).date()


# ---- FBA fulfilment fee ----

def fba_fee(market, tier_info, price, date_str=None, table=None):
    """FBA fulfilment fee per unit from the table.

    fee = base(tier, shipping weight, price band) + peak adder (US, by date) + surcharge, where the US
    3.5% surcharge applies from 2026-04-17 on the base (and on the peak adder, as Amazon applies it on
    top of the published fees). date_str None means today.
    Returns {'fee', 'base', 'peak_adder', 'surcharge', 'source': 'table', 'conf', 'tier', 'band',
    'peak', 'note'}; fee is None when the tier or weight is unknown or the table has no fee for it.
    """
    out = {'fee': None, 'base': None, 'peak_adder': 0.0, 'surcharge': 0.0, 'source': 'table', 'conf': 'low',
           'tier': None, 'band': None, 'peak': False, 'note': None}
    mt = market_table(market, table)
    if mt is None:
        out['note'] = 'Unknown market'
        return out
    key = tier_info.get('tier') if isinstance(tier_info, dict) else None
    t = _tier_by_key(mt, key) if key else None
    w = _pos(tier_info.get('ship_weight_kg')) if isinstance(tier_info, dict) else None
    if t is None or w is None:
        out['note'] = 'Size tier unknown: add dimensions and weight, or enter the fee'
        return out
    out['tier'] = key
    wu = w / KG_PER_LB if (mt.get('units') or {}).get('weight') == 'lb' else w
    rows = t.get('rows') or []
    eps = THRESH['weight_eps']
    row = next((r for r in rows if r.get('max') is None or wu <= r['max'] + eps), rows[-1] if rows else None)
    fees = row.get('fee') if row else None
    if not fees:
        out['note'] = t.get('note') or 'No fee in the table for this size tier: enter it'
        return out
    conf = t.get('conf') or mt.get('conf') or 'low'
    band = price_band(market, price, mt)
    if band is None:
        band = int(_num(mt.get('default_band')) or 0)
        labels = mt.get('band_labels') or []
        conf = 'low'
        out['note'] = 'No price: assumed the %s band' % (labels[band] if 0 <= band < len(labels) else 'standard')
    band = max(0, min(band, len(fees) - 1))
    base = _num(fees[band])
    if base is None:
        out['note'] = 'No fee in the table for this price band: enter it'
        return out
    ex = row.get('extra')
    if isinstance(ex, dict):
        above, step, per = _num(ex.get('above')), _pos(ex.get('step')), _num(ex.get('per_step'))
        if above is not None and step and per is not None and wu > above + eps:
            base += math.ceil((wu - above) / step - 1e-9) * per
    d = to_date(date_str) or _today()
    peak = in_peak(market, d, mt)
    peak_adder = 0.0
    if peak:
        pa = _num(row.get('peak'))
        peak_adder = pa if pa is not None else (_num(t.get('peak')) or 0.0)
    sur_pct = (_num(mt.get('surcharge_pct')) or 0.0) / 100.0
    sur_from = to_date(mt.get('surcharge_from'))
    if sur_from is None or d < sur_from:
        sur_pct = 0.0
    sur_base = base + (peak_adder if mt.get('surcharge_on_peak', True) else 0.0)
    surcharge = sur_pct * sur_base
    out.update(fee=_r(base + peak_adder + surcharge), base=_r(base), peak_adder=_r(peak_adder),
               surcharge=_r(surcharge), conf=conf, band=band, peak=bool(peak and peak_adder))
    if row.get('note') and not out['note']:
        out['note'] = row['note']
    return out


# ---- referral fee ----

def _kw_re(kw):
    return re.compile(r'\b' + re.escape(str(kw).lower()) + r'(?:s|es)?\b')


def _ref_rule(mt, category):
    ref = (mt or {}).get('referral') or {}
    text = str(category or '').lower()
    if text:
        for c in ref.get('categories') or []:
            if any(_kw_re(k).search(text) for k in c.get('match') or []):
                return c
    return ref.get('default') or {'key': 'everything_else', 'mode': 'portion', 'tiers': [[None, 0.15]]}


def match_category(market, category, table=None):
    """Referral category key for a category text ('Home & Kitchen' -> 'kitchen'); 'everything_else'
    when nothing matches. None for an unknown market."""
    mt = market_table(market, table)
    return _ref_rule(mt, category).get('key') if mt else None


def _tier_rate(tiers, p):
    """Rate of the tier the whole price p falls in (upper limits inclusive)."""
    for up, rate in tiers:
        if up is None or p <= up:
            return rate
    return tiers[-1][1] if tiers else 0.0


def referral_fee(market, category, price, table=None):
    """Referral fee per unit on the selling price (tax-inclusive, as Amazon charges it).

    Tiered categories charge each rate on its part of the price (US furniture 15% up to $200 then
    10%; AU sports 13% up to A$200 then 12%); 'whole' categories pick one rate by price (US beauty 8%
    at $10 or less). The market minimum applies (US $0.30, AE AED 1, AU none). None when price is missing.
    """
    mt = market_table(market, table)
    p = _num(price)
    if mt is None or p is None or p <= 0:
        return None
    rule = _ref_rule(mt, category)
    tiers = [(None if up is None else _num(up), _num(rate) or 0.0)
             for up, rate in (rule.get('tiers') or [[None, 0.15]])]
    if rule.get('mode') == 'whole':
        fee = p * _tier_rate(tiers, p)
    else:
        fee, lo = 0.0, 0.0
        for up, rate in tiers:
            top = p if up is None else min(p, up)
            if top > lo:
                fee += (top - lo) * rate
            if up is None or p <= up:
                break
            lo = up
    fee = max(fee, _num(((mt.get('referral') or {}).get('min_fee'))) or 0.0)
    return _r(fee)


def price_thresholds(market, category=None, table=None):
    """Prices where a fee jumps: the FBA price-band limits and any whole-price referral tier limits.
    [{'price', 'kind': 'fba'|'referral', 'lower_if': '<'|'<='}], sorted by price."""
    mt = market_table(market, table)
    if mt is None:
        return []
    out = [{'price': _num(b.get('limit')), 'kind': 'fba', 'lower_if': b.get('lower_if', '<')}
           for b in mt.get('price_bands') or [] if isinstance(b, dict) and _num(b.get('limit')) is not None]
    rule = _ref_rule(mt, category)
    if rule.get('mode') == 'whole':
        out += [{'price': _num(up), 'kind': 'referral', 'lower_if': '<='}
                for up, _ in rule.get('tiers') or [] if _num(up) is not None]
    return sorted(out, key=lambda d: d['price'])


def threshold_state(market, category, price, fba_from_table=True, table=None):
    """(FBA bands passed, referral tiers passed) at this price. Two prices with different states sit on
    either side of a fee cliff."""
    p = _num(price)
    if p is None:
        return None
    th = price_thresholds(market, category, table)
    fba = [t for t in th if t['kind'] == 'fba'] if fba_from_table else []
    ref = [t for t in th if t['kind'] == 'referral']
    return (_crossed([{'limit': t['price'], 'lower_if': t['lower_if']} for t in fba], p),
            _crossed([{'limit': t['price'], 'lower_if': t['lower_if']} for t in ref], p))


# ---- storage and placement ----

def _storage_class(mt, tier):
    if isinstance(tier, dict):
        if tier.get('storage') in ('standard', 'oversize'):
            return tier['storage']
        tier = tier.get('tier')
    t = _tier_by_key(mt, tier) if tier else None
    return (t or {}).get('storage') or 'standard'


def storage_fee(market, volume_cm3, month_num=None, tier=None, table=None):
    """Monthly storage fee per unit: unit volume x the month's rate.

    US: per cu ft, standard $0.78 Jan-Sep / $2.40 Oct-Dec, oversize $0.56 / $1.40. AU: per m3, standard
    A$37.00 / 51.80, oversize A$34.20 / 48.00. AE: AED 2 per cu ft all year. `tier` (a size_tier dict or
    tier key) picks standard or oversize rates; None means standard. month_num None gives the yearly
    average. None when the volume is missing.
    """
    mt = market_table(market, table)
    vol = _pos(volume_cm3)
    st = (mt or {}).get('storage') or {}
    if mt is None or vol is None or not st.get('rates'):
        return None
    qty = vol / CM3_PER_M3 if st.get('unit') == 'm3' else vol / CM3_PER_CUFT
    rates = st['rates'].get(_storage_class(mt, tier)) or st['rates'].get('standard') or {}
    off, pk = _num(rates.get('offpeak')), _num(rates.get('peak'))
    if off is None:
        return None
    pk = off if pk is None else pk
    peak_months = [int(m) for m in st.get('peak_months') or []]
    m = _month(month_num)
    if m is None:
        rate = (off * (12 - len(peak_months)) + pk * len(peak_months)) / 12.0
    else:
        rate = pk if m in peak_months else off
    return round(qty * rate, 6)


def placement_fee(market, tier_info=None, table=None):
    """Inbound placement fee per unit for the tier (US default $0.30, bulky $0.68, extra-large $1.15;
    AU and AE none). Unknown tier -> the market default."""
    mt = market_table(market, table)
    if mt is None:
        return None
    key = tier_info.get('tier') if isinstance(tier_info, dict) else tier_info
    t = _tier_by_key(mt, key) if key else None
    v = _num((t or {}).get('placement'))
    return v if v is not None else (_num(mt.get('placement_default')) or 0.0)


def fees_age_days(date_str=None, table=None):
    """Days between the table's as_of date and date_str (today when None); None if unknown."""
    src = table if isinstance(table, dict) and 'as_of' in table else DEFAULTS
    a, d = to_date(src.get('as_of')), to_date(date_str) or _today()
    return (d - a).days if a else None
