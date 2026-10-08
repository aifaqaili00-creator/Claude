# Product Checker – Amazon AU / UAE / US

A small Windows app that runs on your own PC. It has two tabs:

1. **Check a product (local sellers):** type a product, for example `moving bags`. The app searches amazon.com.au, amazon.ae and amazon.com in a browser window and reads every result's delivery date. It then tells you how many listings deliver **fast** (stock already in that country) and how many come **slowly from overseas**. If only 3–4 listings are fast, it marks the search as an **opportunity**.
2. **Top 10 from a Helium 10 file:** open any Black Box or Xray export (`.csv` or `.xlsx`). The app ranks every product and shows the best 10 with price, sales, revenue, reviews, age, trend and warning flags.

## Install (once)

1. Install **Python 3.10 or newer** from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"** during setup.
2. You also need **Google Chrome** or **Microsoft Edge**. Edge comes with Windows.
3. Put this folder anywhere, for example `C:\Users\danis\OneDrive\Desktop\Claude\product-checker`.
4. Double-click **`start.bat`**. The first start installs what the app needs, which takes 1–2 minutes. After that, it opens straight away.

## First use

1. Click **"Open browser: log in to Helium 10 / set delivery locations"**. A browser window opens with four tabs.
2. **Helium 10 tab:** log in (optional). You can also add the Helium 10 Chrome extension in this window to run Xray on the search pages.
3. **Amazon tabs:** click **"Deliver to"** at the top left and set:
   - amazon.com.au → postcode **2000**
   - amazon.ae → **Dubai**
   - amazon.com → ZIP **10001**

This browser keeps its own profile, so your logins and locations are remembered next time.

## Daily use

- **Tab 1:** type a product, tick the countries, and press **Check**. Leave the browser window open while it works. If Amazon shows a "type the characters" check, solve it in that window and the app carries on.
- **Tab 2:** press **Open CSV / Excel file…**. The country is detected from the file name or the Amazon links in it, or you can choose it. Select a product and press **Check selected on Amazon** to run the tab 1 check on it.
- **Save to Excel** on either tab saves the results.
- Double-click any row to open the product on Amazon.

## How it decides

| | Australia | UAE | USA |
|---|---|---|---|
| Price | A$25–70 | AED 60–260 | US$20–60 |
| Sales per month, at least | 100 | 50 | 300 |
| Reviews, at most | 230 | 115 | 300 |

- **Flags:** electrical, kids/toys, consumable or chemical, medical, fits another brand, seasonal, bulky, heavy, Amazon sells it, too cheap or too expensive.
- **Good pick:** meets the targets with no flags.
- **Check first:** meets the targets but has a flag to read.
- **Score:** sales ÷ √(reviews + 20). Products under 12 months old get a bonus, as do products with a rating of 4.2 or lower (room to do better) and products whose sales are growing. Products with falling sales are scored lower.
- **Fast delivery:** arrives within 3 days. You can change this in tab 1.

To change any of these numbers, edit `TARGETS` at the top of `file_rank.py`.

## Notes

- **No Helium 10 searches are used.** The app reads only Amazon's public search pages and your own export files.
- **Keep checks reasonable:** a few product checks at a time. Searching Amazon heavily from one PC can trigger its "type the characters" check more often.
- **If the app doesn't open:** run `.venv\Scripts\python.exe app.py` in this folder from Command Prompt to see the error.
