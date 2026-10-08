# Product Checker – Amazon AU / UAE / US

A small Windows app that runs on your own PC, in its own window.

1. **Check a product:** type a product, for example `moving bags`. The app searches amazon.com.au, amazon.ae and amazon.com at the same time and reads every listing's delivery date. It then shows how many listings arrive **fast** (stock already in that country) and how many come **slowly from overseas**. If only 3–4 are fast, it marks the search as an **opportunity**.
2. **Top products from a file:** drop in any Helium 10 Black Box or Xray export (`.csv` or `.xlsx`). The app ranks every product and shows the best ones with price, sales, revenue, reviews, age, trend and warning flags. One click then checks the local sellers for all of them.
3. **History:** past checks open again instantly, without a new search.
4. **Settings:** your Chrome profile, delivery locations and how checks run.

## Install or update

1. You need **Python 3.10 or newer** (https://www.python.org/downloads/, with **"Add python.exe to PATH"** ticked) and **Chrome** or **Edge**.
2. Unzip into a folder, for example `C:\Users\danis\Downloads\Product_Checker\`. To update, unzip over the old folder; your settings and history are kept.
3. Double-click **`start.bat`**. The first start installs what the app needs (1–2 minutes) and puts a **Product Checker** shortcut on your desktop.

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

- **No Helium 10 searches are used.** The app reads only Amazon's public search pages and your own files.
- **To see errors:** run `start.bat debug`, which shows a console window. The log file is `%LOCALAPPDATA%\ProductChecker\app.log`.
- **If the background browser misbehaves:** press **Restart** under Settings → Background browser.
