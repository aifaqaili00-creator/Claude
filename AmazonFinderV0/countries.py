"""Country names, adjectives and codes: sellers' business addresses, Helium 10's seller country and the flags."""
NAMES = {
    'AU': 'Australia', 'AE': 'United Arab Emirates', 'US': 'United States', 'CN': 'China', 'HK': 'Hong Kong',
    'GB': 'United Kingdom', 'DE': 'Germany', 'IN': 'India', 'JP': 'Japan', 'KR': 'South Korea', 'TW': 'Taiwan',
    'VN': 'Vietnam', 'CA': 'Canada', 'TR': 'Turkey', 'SG': 'Singapore', 'FR': 'France', 'IT': 'Italy', 'ES': 'Spain',
    'NL': 'Netherlands', 'PL': 'Poland', 'MY': 'Malaysia', 'TH': 'Thailand', 'PK': 'Pakistan', 'ID': 'Indonesia',
    'PH': 'Philippines', 'NZ': 'New Zealand', 'SA': 'Saudi Arabia', 'IL': 'Israel', 'MX': 'Mexico', 'BR': 'Brazil',
    'BD': 'Bangladesh', 'LK': 'Sri Lanka', 'EG': 'Egypt', 'IE': 'Ireland', 'SE': 'Sweden', 'CH': 'Switzerland',
    'BE': 'Belgium', 'AT': 'Austria', 'DK': 'Denmark', 'PT': 'Portugal', 'CZ': 'Czechia', 'LT': 'Lithuania',
}
ADJ = {'AU': 'Australian', 'AE': 'UAE', 'US': 'American', 'CN': 'Chinese', 'HK': 'Hong Kong', 'GB': 'British',
       'DE': 'German', 'IN': 'Indian', 'JP': 'Japanese', 'KR': 'Korean', 'TW': 'Taiwanese', 'VN': 'Vietnamese',
       'CA': 'Canadian', 'TR': 'Turkish', 'SG': 'Singaporean', 'FR': 'French', 'IT': 'Italian', 'ES': 'Spanish',
       'NL': 'Dutch', 'PL': 'Polish', 'MY': 'Malaysian', 'TH': 'Thai', 'PK': 'Pakistani', 'ID': 'Indonesian',
       'PH': 'Filipino', 'NZ': 'New Zealand', 'SA': 'Saudi', 'IL': 'Israeli', 'MX': 'Mexican', 'BR': 'Brazilian'}
ALIASES = {'usa': 'US', 'u.s.a': 'US', 'united states of america': 'US', 'uk': 'GB', 'great britain': 'GB',
           'england': 'GB', 'uae': 'AE', 'prc': 'CN', "people's republic of china": 'CN', 'mainland china': 'CN',
           'korea': 'KR', 'republic of korea': 'KR', 'hongkong': 'HK', 'viet nam': 'VN', 'turkiye': 'TR'}
ISO = set((
    'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC '
    'CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD '
    'GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH '
    'KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW '
    'MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC '
    'SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY '
    'UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW').split()) - {'NA'}           # "NA" is how exports write "not available"


def code_of(text):
    """'CN', 'China' or "People's Republic of China" -> 'CN'. None when it is not a country."""
    t = ' '.join(str(text or '').split()).strip(' .,')
    if len(t) == 2:
        return t.upper() if t.upper() in ISO else None
    k = t.lower()
    if k in ALIASES:
        return ALIASES[k]
    return next((c for c, n in NAMES.items() if n.lower() == k), None)


def name(code):
    return NAMES.get(code, code)


def adjective(code):
    """'CN' -> 'Chinese'; None when there is no common adjective."""
    return ADJ.get(code)
