---
name: smartscout-harvest
description: Export SmartScout's Amazon Australia / UAE / USA data into Product Checker (Market Gaps, Shipped in, Contacts, the sales model). Use when the user has SmartScout again and asks to fetch, refresh or harvest SmartScout data, or to add a SmartScout export to the tool.
---

# SmartScout harvest for Product Checker

The app reads SmartScout **files**; it never logs in to SmartScout itself. You make the files: Excel exports from the
SmartScout website through the user's Chrome (Claude in Chrome), plus the SmartScout connector's answers saved as JSON.
Everything goes into `C:\Claude_repo\HuntyZone_1510\drive_Data_Scout` (Chrome's download folder for this). The first
harvest (October 2026) is described file by file in `drive_Data_Scout\_harvest_log.txt`: read it first.

## Ground rules
- The SmartScout login may be shared with other people. Use only the website's Export / Excel buttons and the
  connector. No bulk page scraping, no account or billing changes. Save filter presets only when the user says so,
  named `PC – …`.
- Downloading needs the user's OK (say which exports and the folder). Load the `chrome-browser` skill before the
  first browser step, and work in a new tab.
- If the browser disconnects, or one step fails twice, stop and ask; resume from the last file in the log.
- Do not commit the exports: the GitHub repo is public.

## Facts about the website
- Pick the market in the URL: `https://app.smartscout.com/app/<page>?marketplace=AU` (or `AE`, `US`).
- An export holds **every filtered row, up to 20,000** ("20,000 max rows" in the Export menu). Split a bigger table by
  a revenue or units band into `part 1`, `part 2`… with the same date. The Niche Finder export is not capped.
- Keep the Chrome window about 1500 px wide, or the Filters button is hidden.
- Number filters: type the value, leave the box (blur), wait about 1 second, then click Apply. Otherwise the last value
  is lost. The filter panel closes after Apply: read the row count shown on the page before exporting.
- Chrome saves under SmartScout's own name (`SmartScout - Niche Finder.xlsx`, `smartscout-…`). Rename it **at once**
  to `smartscout <AU|UAE|US> <slice> <YYYY-MM-DD>[ part N].xlsx`. Rename only a file written in the last 10 minutes,
  open it to count its rows, and compare with the page's count. (On 2026-10-25 daylight saving ended at 03:00 and the
  file times jumped back an hour: never pick "the newest file" by time alone.)
- Add a line to `_harvest_log.txt` for every file: time, name, filters, rows.
- AU and UAE sellers have no country or business name in SmartScout (the Country filter returns nothing).
- Not available on the Business plan: Movers & Shakers (upgrade), API and Data Lake (sales pages). Brand Reports are
  one brand at a time and hold no contacts; Seller Map is a picture only. No page has brand emails.

## What to export (AU, then UAE; same date for every file)
| Page | Filters | Slice in the file name |
|---|---|---|
| Subcategories → Niche Finder (2nd tab): Columns → tick all, then Excel → Excel icon | none | `subcategories` |
| Brands (all columns on) | monthly revenue ≥ 2,500 / 1,000–2,500 | `brands … part 1` / `part 2` |
| Sellers (Filters → Columns: switch every column on; there is no select-all) | monthly revenue ≥ 1,000 | `sellers` |
| Products | Number of All Sellers ≤ 6, units bands so each part stays under 20,000 (AU: ≥100, 65–99, 50–64, 30–49; UAE: ≥100, 50–99, 30–49, 20–29) | `products-few-sellers … part 1..4` |
| Products | units ≥ 100, any sellers | `products-top` |
| Products | AU units 10–29, UAE 10–19 (the top 20,000 by revenue: sales-rank spread for the sales model) | `products-mid` |
| Products | Top New Products switched on | `products-top-new` |
| Sellers → magnifier on a seller → Offers → Excel side button → Excel icon (top 20,000 by revenue) | AU: Amazon US, UK, Germany, Japan. UAE: Amazon US, UK, Germany, JetSouq, DesertcartAE, ExbordersFZE | `seller-products-<seller name in lower case, spaces as ->` e.g. `seller-products-amazon-us` |
| US: Subcategories → Niche Finder | none | `US subcategories` |

The slice name is what the app keys on (`smartscout.preset_of`): `seller-products-*` feeds Shipped in, the other
`products-*` slices feed Products to sell, the stock watch and the sales model. A new slice name is fine: it is
imported as products. A filtered slice never replaces the brands / sellers / subcategories baseline.

## The connector (USA only on the Business plan)
A question about AU or the UAE is silently answered with US data, and phrase searches return nothing. What works is a
US profile of the same ASINs (many Global Store listings are US listings):
1. `query_analytics`: "Full profile of product <one ASIN>" gives a handle.
2. `run_query(handle, filterValues=[up to 50 ASINs])` returns title, monthly units and revenue, Buy Box price and
   number of sellers for each ASIN that also sells in the USA.
3. Save each batch as `drive_Data_Scout\smartscout-mcp US asin-profiles <YYYY-MM-DD> <a, b, c…>.json`:
   `{"source": "smartscout-mcp", "market": "US", "topic": "asin-profiles", "question": "…", "as_of": "YYYY-MM-DD", "rows": [{"ASIN": …, "Product Title": …, "Monthly Unit Sales Estimate": …, "Monthly Revenue Estimate": …, "Buy Box Price": …, "Number of Sellers": …}]}`
4. Which ASINs: the AU / UAE products with few sellers the app shows first, and the Shipped in products. Stop a list
   when two batches in a row match almost nothing (UAE-only listings). In October about 12 questions covered 620 ASINs.

## Import and check
- The app imports `drive_Data_Scout` by itself at start and every hour; **Market Gaps → Import SmartScout files** does
  it at once. Each file is imported once (by its contents); the result lists rows and calibration points per file.
- After an import the sales model is refitted for that market. Check that Australia and the UAE stay `calibrated`
  (Health → Sales curves, with Advanced on).
- Look at Market Gaps in both markets: the date on top, Products to sell (SmartScout pills), Niches (SmartScout line),
  Shipped in, Contacts.
- Header names change now and then (the sellers export did between September and October 2026). If a file imports
  with missing fields, add the new header to the lists in `smartscout.py` (`BRAND_COLS`, `SELLER_COLS`,
  `PRODUCT_COLS`, `SUBCAT_COLS`) with a test in `tests/test_smartscout.py`.

## When the subscription ends
- Set **Settings → Gap Finder → SmartScout subscription ends on** (`ss_until`): after that day Market Gaps calls the data
  a snapshot. Nothing else changes: the sales model stays calibrated, the stock watch keeps reading SmartScout's best
  sellers every day, contacts and the seller registry stay, and the live Refresh keeps checking niches.
- If presets were saved in the account, offer to delete them.
