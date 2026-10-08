# Product Checker – Amazon AU / UAE / US

A small Windows app that runs on your own PC, in its own window.

1. **Check a product:** type a product, for example `moving bags`. The app searches amazon.com.au, amazon.ae and amazon.com at the same time and reads every listing's delivery date. It then shows how many listings arrive **fast** (stock already in that country) and how many come **slowly from overseas**. If only 3–4 are fast, it marks the search as an **opportunity**.
2. **Xray analysis:** run Helium 10 Xray on an Amazon search in your own Chrome and export it. The app picks the file up from Downloads and tells you whether the market is **Promising**, **Possible** or **Hard**, and why.
3. **Top products from a file:** drop in any Helium 10 Black Box or Xray export (`.csv` or `.xlsx`). The app ranks every product and shows the best ones with price, sales, revenue, reviews, age, trend and warning flags. One click then checks the local sellers for all of them.
4. **History:** past checks open again instantly, without a new search.
5. **Settings:** your Chrome profile, delivery locations and how checks run.

## Install

1. You need **Python 3.10 or newer** (https://www.python.org/downloads/, with **"Add python.exe to PATH"** ticked) and **Chrome** or **Edge**.
2. Get the code using one of these:
   - On GitHub: **Code → Download ZIP**, then unzip it, for example into `C:\Users\danis\Downloads\Product_Checker\`.
   - With git: `git clone <this repository's URL>`.
3. Double-click **`start.bat`**. The first start installs what the app needs (1–2 minutes) and puts a **Product Checker** shortcut on your desktop.

**To update:** download the ZIP again and unzip it over the old folder, or run `git pull`. Your settings and history are kept, because they live in `%LOCALAPPDATA%\ProductChecker`.

## Local or not local?

Each country card shows how many listings come from **local sellers** and how many are **not local**. The app reads the delivery text under each product:

| Amazon shows | Counted as |
|---|---|
| "Today 5 pm – 10 pm", "Tomorrow", or a date within 3 days | **Local, fast**: stock is in an Amazon warehouse in that country |
| a date 4–9 days away, with no "international" | **Local, slower**: a local seller shipping it themselves |
| "FREE delivery on your first order", or Prime, with no date | **Local**: that offer only applies to items Amazon ships locally |
| "FREE **International** delivery … eligible international items", or "Ships from abroad" / "Global Store" | **Not local: international** |
| 10 days or more without that label, e.g. "10 – 13 Nov" | **Not local: ships from abroad** |
| no delivery date at all | **Unknown** |

The listings table shows **Ships from** and the reason for each row, and you can filter it by **Fast**, **Local** or **Not local**. You can change the 3-day and 9-day limits in Settings. Saved results and History are recounted straight away when you do.

A market is most interesting when it has **real demand, few fast local listings, and many listings shipping from overseas**. Buyers there are waiting 1–3 weeks, so stock in an Amazon warehouse would win the fast-delivery customers.

## Xray analysis (Helium 10)

Xray is part of the Helium 10 Chrome extension, so it runs in **your own Chrome profile**, where you're logged in to Helium 10. The app can't click the extension for you, because Chrome doesn't let automation tools use extensions. So it works in three steps:

1. In the **Xray analysis** tab, type the keyword, choose the Amazon site and press **Open search in Chrome**. The search opens in your profile, and the app starts watching your Downloads folder.
2. Click the **Helium 10** extension, then **Xray**. When the numbers have loaded, click **Export** and save it as **CSV**.
3. The app finds the new file within a couple of seconds and analyses it. You can also use **Or choose an Xray file…**.

**What you get:**

| | |
|---|---|
| **Verdict** | Promising / Possible / Hard, with up to 10 points: demand, review barrier, how spread out the revenue is, newcomers succeeding, Amazon as a seller, and margin after fees |
| **Why** | each point explained in plain words, e.g. "4 listings under a year old already sell 100+ a month" |
| **Numbers** | revenue per month, top 10 median sales, price range, review barrier (top 10 median reviews), top 3 share and biggest brand, new winners, beatable listings, and what is left per sale after the FBA fee and 15% referral fee |
| **Charts** | who fulfils the orders (FBA / FBM / Amazon) and where the sellers are based |
| **Listings** | every organic listing by revenue, with **beatable** (enough sales, few enough reviews) and **new** (under 1 year) tags |
| **Check local sellers** | runs the delivery check for the same keyword, to see how many listings deliver fast |
| **Save to Excel** | a Summary sheet with the reasons, plus a Listings sheet |

In the **Check a product** tab, **Analyse with Xray** takes the same keyword straight to this tab.

## Your Chrome profile (e.g. "Sohaib")

The app finds your Chrome and Edge profiles and picks **Sohaib** automatically. You can change this under **Settings → Your Chrome profile**. **Open Helium 10**, product links and "Open search in Chrome" all open in that profile, with your logins and the Helium 10 extension.

The automatic Amazon checks run in a **separate background browser**, kept minimised. Chrome doesn't allow automation tools to control your everyday profile (blocked since Chrome 136), and the checks don't need your logins. The background browser shows itself only when Amazon asks you to type the characters from a picture. You can also open it with **Show checker browser**.

## Delivery locations

The checks use postcode **2000** (Sydney), **Dubai** and ZIP **10001** (New York). You can change these in Settings. They're set automatically on the first check, and the pills at the top turn green when Amazon confirms them. If one stays orange:
1. Press **Show checker browser**.
2. Click **"Deliver to"** on that Amazon tab and set the location once.
3. Press **Hide** (Settings → Background browser).

## Speed

- **All countries at once:** the three Amazon sites are searched at the same time.
- **Lighter pages:** pictures and fonts are skipped in the background browser.
- **Saved results:** a check is reused for 6 hours (Settings), so repeat checks and History are instant. Tick **Search again** to force a fresh search.
- **Batch checks:** checks from a file run 3 products at a time.

## How products are ranked

| | Australia | UAE | USA |
|---|---|---|---|
| Price | A$25–70 | AED 60–260 | US$20–60 |
| Sales per month, at least | 100 | 50 | 300 |
| Reviews, at most | 230 | 115 | 300 |

- **Flags:** electrical, kids/toys, consumable or chemical, medical, fits another brand, seasonal, bulky, heavy, Amazon sells it, too cheap or too expensive.
- **Good pick:** meets the targets with no flags.
- **Check first:** meets the targets but has a flag to read.
- **Score:** sales ÷ √(reviews + 20). New listings, ratings of 4.2 or lower and growing sales get a bonus; falling sales lower the score.

To change these numbers, edit `TARGETS` at the top of `file_rank.py`.

## If something goes wrong

- **The app uses none of your Black Box searches.** It reads Amazon's public search pages and the Helium 10 files you export yourself. Xray runs only when you click it in your own Chrome.
- **To see errors:** run `start.bat debug`, which shows a console window. The log file is `%LOCALAPPDATA%\ProductChecker\app.log`.
- **If the background browser misbehaves:** press **Restart** under Settings → Background browser.

## For developers

| File | What it does |
|---|---|
| `app.py` | local web server and app window; settings, history and jobs |
| `ui/index.html` | the whole interface (plain HTML, CSS and JavaScript) |
| `amazon_check.py` | background browser (Playwright) that searches Amazon and reads delivery dates |
| `xray.py` | Xray export analysis and the Downloads watcher |
| `file_rank.py` | reads Helium 10 exports, flags and ranks products |
| `chrome_profiles.py` | finds Chrome/Edge profiles and opens links in one |

To run the tests: `python -m pip install pytest`, then `python -m pytest -q`.
