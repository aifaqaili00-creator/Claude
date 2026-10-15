# Product Checker – Amazon AU / UAE / US

A small Windows app that runs on your own PC, in its own window.

**How it is laid out.** The menu on the left follows your steps: **1 Find ideas** (Product ideas, Trend radar, Gap Finder, Market Gaps), **2 Check an idea** (Check sellers, Niche keywords, Xray analysis) and **3 Your products** (Board, Watchlist). Pages you need less often (Market intelligence, Ask AI, Rank a file, Library, Health) are under **More**. The app starts in a **simple view**: each page shows what you need to decide, and the explanations open from their (i) **What this means** link. Turn on **Show all details** (bottom left, or Settings, General) to see every chart, column and setting; a PDF always has everything.

1. **Check a product:** type a product, for example `moving bags`. The app searches amazon.com.au, amazon.ae and amazon.com at the same time and reads every listing's delivery date. It then shows how many listings arrive **fast** (stock already in that country) and how many come **slowly from overseas**. If only 3–4 are fast, it marks the search as an **opportunity**. **Price across markets** puts the typical page-1 price of each country side by side, converted to your currency, so you see at once where the product sells higher.
   Each country's card starts with its flag and **one sentence** on the situation, for example "Weak in Australia: about 3,400 sales a month on page 1, 8 fast local sellers, mostly Chinese FBA sellers and about A$27 of the A$55 price left for the product and shipping." Below it, **Who sells it** sums up the sellers once a report has read them; **See who really sells it** (or **Open report**) opens the report, which reads them.
   Switch to **A list** to check up to 20 products in one go (one per line). A table shows, for every product and country, the local and overseas sellers, buyers a month and the typical price, with the most buyers per local seller first; each row has **Report** and **Watch**, and **Save to Excel** keeps the table.
2. **Product ideas:** one click finds products that are **booming** or **brand new** on Amazon USA (or AU / UAE), then checks whether they have reached Australia and the UAE yet.
   **Niche keywords** shows what shoppers type for a niche and how each keyword does in every country.
3. **Xray analysis:** run Helium 10 Xray on an Amazon search in your own Chrome and export it. The app picks the file up from Downloads and tells you whether the market is **Promising**, **Possible** or **Hard**, and why.
4. **Rank a file:** drop in any Helium 10 Black Box or Xray export (`.csv` or `.xlsx`). The app ranks every product and shows the best ones with price, sales, revenue, reviews, age, trend and warning flags. One click then checks the local sellers for all of them.
5. **History:** past checks open again instantly, without a new search.
6. **Settings:** theme (light, dark, or the same as Windows), your currency, your Chrome profile, delivery locations and how checks run.
   - **Currency:** choose **your currency** (US$, A$, AED or €). Prices from the other marketplaces then also show “≈” in it, for example `AED 116 ≈ US$ 32`, and tables that mix marketplaces sort by the converted price. Choose separately the currency **your supplier quotes in**, used by **Profit?** and the profit calculator. Settings shows today's rates between all four currencies.
   - **Margin to keep** (default 20%): the radar and idea rows show the most you can pay your supplier per piece and still keep this share of the price.
   - **Email for reminders:** how the Product board's deadline emails are sent (see 12). **How emails are sent:** *in my browser* (the default, no password: each email opens ready to send in Gmail, in the Edge or Chrome profile where you are signed in, and you press Send) or *with a Gmail app password* (they go out by themselves; the password is kept encrypted for your Windows user on this PC, and its fields only show in this mode). **Browser profile:** *Automatic* picks the Edge or Chrome profile signed in with your email address (for example Chrome "Afaq"), else the one for links; or pick any profile. **Open a test email** (or **Send me a test email**) checks it.
   - **Currency** in the top bar opens a converter between US$, A$, AED and € from any screen; click a result to copy it.
   - **Save to Excel** after a check adds each country's currency and typical price, and every price converted to your currency.

7. **Keyword report** (press **Open report** after a check, or open a keyword in the Library). It reads like a story, with a faint flag of the country behind its header and a plain "what this means" line above every chart:
   - **Summary**: one sentence on the situation, the score with a green / amber / red bar for each part (demand, momentum, competition, local gap, margin) and the key numbers;
   - **Who sells it**: the top 10 listings on page 1 (not the ads), who sells each one, the seller's country (from the business address on their Amazon seller page), the brand, and how it ships: **Amazon itself**, **FBA** (stock in Amazon's warehouse) or **FBM** (the seller sends each order). Bars show the biggest sellers' share of the sales and where the sellers are based, with flags, plus what it means: for example "Sellers based in China have 7 of the top 10 listings: expect price competition" or "4 of the top listings launched in the last year: newcomers can win here". The app reads the listings' product pages and their sellers' pages by itself when you open the report (about a minute the first time), then keeps them: product pages for two weeks, sellers for a month. **Read again** reads them now. A Helium 10 Xray export fills in a seller's country, brand or shipping when Amazon does not show them;
   - **Money**: profit per sale, the first order and the launch plan (see 11);
   - **Demand**: search interest over 5 years (Google Trends) and the seasonal pattern, and **units sold and revenue per month**: Helium 10 numbers when you have an Xray export for that month, otherwise an estimate from Amazon's "bought in past month" badges, plus a backcast from the Google Trends shape. **More detail** shows page-1 sales and local and overseas sellers at each check;
   - **Competition**: sales per seller, prices, reviews and the top listings;
   - **Customer complaints**: the 5 most useful 1–3★ reviews of the top 5 listings on page 1, worst ratings and reviews from that country first, then the most helpful; at most 2 from one listing. They are read together with the listings; **Read again** in the panel reads them now. Amazon shows its critical reviews pages only to signed-in shoppers, so otherwise they come from the reviews on each product page (only a few each). Each one links to the listing and to all its critical reviews (in your Chrome). **What buyers complain about** groups all the critical reviews read into themes (broke, too small, smell, flimsy, stops working…) with a quote each and what to ask your supplier; **Copy supplier brief** copies it as a message;
   - **Where to launch**: the same product at your product cost and first order in each market it was searched in (see 9);
   - **Other markets** side by side, and where the numbers come from.
   **PDF** in the header saves the report in your Downloads folder, for example `dog bed - Amazon Australia - 2026-10-12.pdf`: tick the parts you want (Summary, Who sells it, Money, Demand, Competition, Customer complaints, Other markets, where the numbers come from). It starts with a cover (flag, keyword, date and the one-sentence summary) and has page numbers; **Open the PDF** and **Show in folder** are there when it is ready. The PDF is drawn by a hidden Chrome or Edge, so nothing pops up.
   Every estimate shows its range and where it came from. A blocked search is shown as a gap, never as zero.
8. **Library:** every keyword you have checked, your recent checks, and your Helium 10 imports.

9. **Automatic refresh:** watch a keyword (Watchlist, or **Watch** on a report) and the app checks it again every day, fetches Google Trends weekly, and reads the Movers & Shakers and New Releases lists (every 12 hours for the USA, daily for Australia and the UAE).
   - **Home** shows what changed: new trends, gaps opening in Australia or the UAE, demand jumps, captchas waiting.
   - **Trend radar** ranks the products climbing Amazon's lists by one **opportunity** score from five signals (Amazon lists, search trend, demand, competition, price & fees); a hatched cell is not measured yet. Similar listings fold into one row ("+N similar", listed under **More**). Each product is searched by the phrase shoppers type when Amazon's suggestions know one. **Qualify** (or **Qualify top 12**) reads what a row misses: that phrase, its Google trend, the product page, and whether Amazon in the other markets suggests it. After a search there (**Check AU & UAE**, or the daily automatic qualifying), the markets column shows a **gap score** for each market it could sell in next (from the USA: Australia and the UAE; between Australia and the UAE: the other one), and you can sort by it. With automatic refresh on, the top products are qualified once a day within a small daily budget (Settings → Automatic refresh). It uses the same price range and phone / laptop filter as Product ideas, and each row has **Find on Alibaba** and **Profit?**.
     - **Your supplier price:** the Price & fees cell shows **pay ≤**, the most you can pay a supplier per piece and keep your margin (Settings → Currency → Margin to keep). Type a quote in **Profit?** and it is kept for that product: the cell then shows your margin, and the score and the gap scores use it. **Forget** removes it.
     - **Train it:** ★ stars a product you like; **Not for me** hides that one product (they are listed under **Hidden**, with **Show again**). Products like the ones you starred get a "like your ★" pill, and the sort **Best for me** puts starred products first, then the ones like them.
     - **Where to launch?** (in **More**) compares the product's own market and the next ones at your supplier price and a first order of 300: typical price, profit per sale, the most you can pay, what a new seller might sell, when the first order sells out, profit a month and the gap score, and names the best market.
     - **Find the underserved version** searches the 4 longer phrases Amazon suggests for it (for example "calming dog bed for large dogs") in the next markets and ranks them by gap; **Use this phrase** makes the row search, score and check by it from then on.
     - **Wave lines** under each market say whether the trend has reached it: "here" (Amazon there already suggests the phrase), "expected Feb–Apr 2027", "due now" or "not there yet". The delay is learned from products whose Google searches rose in both markets, so dates appear once 3 such products are known.
     - **Rising on Google** lists the searches Google marks Breakout or rising fast among the related searches of the trends read in the last 30 days (not ones you watch or already track), with how they do on Amazon once searched; **Check** searches them here and in the next markets. Qualifying also reads these, within the same daily Google budget.
   - **Health** shows every source, its daily budget and any pause.
   - Under **Settings → Automatic refresh**, turn on **Keep refreshing after I close the window** and **Start with Windows** to keep the data fresh all the time. Important news then arrives as a Windows notification (not at night, and not while the app is in front of you).
   - Background work is polite: a few pages at a time, daily limits per marketplace, and it waits whenever you run something yourself. A captcha never pops up in the background; that marketplace pauses and Home offers **Solve now**.

10. **Product report** (open it from a keyword report's top listings, a radar card, or `#/p/<market>/<ASIN>`): the Best Sellers Rank over time, and **units sold and revenue per month for that one product**, estimated from its rank through a sales curve the app fits for each marketplace. Listings that show both a rank and a "bought in past month" badge teach the curve, as do your Helium 10 exports; **Track daily** reads the page every day. Until a marketplace has 30 readings the curve leans on default values, and the report says so. **Health** shows how well each curve fits. It also shows that product's **top negative reviews**, read the same way as in the keyword report.

11. **Money and decisions** (in every keyword report):
    - **Opportunity score** (0–100, Strong / Worth a look / Weak / Skip), built from demand, momentum, competition, the local gap and margin, with a reason for each part. Risky product types lower it.
    - **Profit per sale**: Amazon's referral, FBA, storage and placement fees for that marketplace, GST/VAT (set whether you are registered under Settings), freight by sea or air, duty, ads and returns. Type the product cost in US$, A$, AED or €. It also gives return on stock, break-even ad spend, the highest product cost for a 20% margin (in your supplier's currency), and a price grid that marks fee steps. What you type stays there when the report refreshes.
    - **Size and weight** are filled in for you from the product pages of the top listings, which the report reads when you open it (see Who sells it): the package size and weight Amazon's FBA fee uses. The calculator takes the median of up to 5 (or the Helium 10 weight if no page shows one). **Read size from Amazon** (or **Read more listings**) reads them now. Your own numbers always win; **Use Amazon's size** puts Amazon's back. To use your supplier's size instead, paste the text from their page (e.g. `Single package size: 30X20X10 cm, Single gross weight: 0.5 kg`, inches and pounds work too) into the box under the form. The FBA fee line shows which size tier it lands in.
    - **First order**: type how many units you would order. You see the money it needs (product, freight and duty), after how many sales the money is back, the profit on the whole order, and roughly how many months it lasts at the page-1 average.
    - The report header also opens the same search on **Alibaba** (suppliers) and on the country's other big shop: **Noon** (UAE), **eBay** (Australia) or **Walmart** (USA).
    - **Launch plan**: from the seasonal pattern, the dates to order, ship and be in stock before demand rises, by sea and by air. Home reminds you two weeks before an order-by date.
    - For US keywords, **Bring it from the USA?** scores how well the product would travel to Australia and the UAE, and shows each country's typical price against the USA's (for example "+20% against the USA").
    - Fee tables are dated 2026-10-09 and include links to check them; the Australian and UAE tables are estimates. US duty changed several times in 2026, so check it before ordering.

12. **Product board** (Board in the sidebar): your products from the first idea to re-orders, in five coloured columns: Idea, Approval, Sourcing & PO, Launching strategy and Re-order. Press **Add to board** on the Trend radar, Product ideas, a keyword report or a product report (or **Add product** on the board). Drag a card to move it; a stage opens only when the one before it is done.
    - **Next step:** every card says the one thing left to do on it, for example "Add the Alibaba link", "Tick 3 approval checks", "Add the price per unit and 3 more" or "Re-order now for Australia" (red when stock is due). Click it to open the product on the tab that does it; the same line is at the top of the product window.
    - **Idea:** every new product starts here. Add its **Amazon ASIN** (a listing you found; once your own listing is live, put your own ASIN here so stock and sales come from Amazon) and your supplier's **Alibaba link**, in the Idea tab or already in **Add product**. With both, the card moves to Approval by itself.
    - **Approval:** a checklist (demand, local sellers, profit, supplier, restrictions, size for FBA); remove the checks that do not apply or add your own. Under each standard check is what your research found (from the newest search of the product's keyword, its Go/No-go rules, the package size of the top listings and the supplier price you remember in the keyword report's profit calculator), for example "Page 1 sells at least 1,594 a month (floor 1,500)". **Tick the checks the research confirms** ticks those in one go; you still decide on the rest. When every check is ticked, the card wins the **Approved** trophy and moves to Sourcing & PO. You can tick checks while it is still an idea.
    - **Sourcing & PO:** the price per unit (in any of the four currencies), and for each market you send to (USA, Australia, UAE): units, sea or air, and the shipping cost for the whole shipment. You see the total to invest, the landed cost per unit and **What this order could make** in each market (profit per sale, ROI, months to sell the order) from the newest search there. A shipment tracker keeps the deposit, produced, shipped and arrived dates, with the day it should be on sale. When every number is in, the card wins the **Order ready** trophy and moves to Launching strategy.
    - **Launching strategy:** first the strategies people give you, each with who it came from (your Notifiers are suggested) and what it says: launch price, coupons, PPC bids, Vine, giveaways. **Edit** or delete them any time. Below them, a checklist made from the app's research for each market: the price band, your profit and how much ads can cost, the reviews to beat, who sells, what buyers complain about, the search phrases, the season and order-by dates, a first sales target and the Google trend. Mark each one **Done**, **Changed** (say what you do instead) or **Not relevant**, or add your own. The research keeps refreshing (the product's keyword is watched); a hint whose numbers moved goes back to To check with **Market moved**, and a checked hint asks to be checked again after 2 or 4 weeks. With every hint handled and the day it went live on Amazon, the card wins **Launched** and moves to Re-order.
    - **Re-order:** type how many units are left in each market. The bar turns red at 30% of the shipment (change it in Settings, Product board), the app works out how fast it sells (against your monthly sales target), when it runs out and the last day to re-order given the production and shipping time, and you get a Windows notification and a Home alert when stock turns red or the re-order day comes. **Start re-order** opens order #2 for that market; completing it wins a **Re-ordered** trophy.
    - **Deadlines and reminder emails:** on any stage, set the days to complete it and tick who to email from your **Notifiers** list. They get a "new task" email right away, a reminder 3 days before the due day (the day before for short tasks) and, on the due day, an invitation to an update meeting at 15:00 for 30 minutes (Accept / Decline; change the time, length and a Zoom / Meet / Teams link in Settings). Every email lists what is still open, you get a copy, and finishing the stage cancels the rest. **Send reminder now** and **Preview emails** are on the card. By default each email opens ready to send in Gmail, in your browser profile (Settings, Email for reminders), and you press **Send**; the update meeting opens as a **Google Calendar** event with the people already added as guests, and when you press **Save** Google emails them the invitation. The card then says "opened in your browser" rather than "sent". With a Gmail app password instead (turn on 2-step verification, create an app password named Product Checker and paste it), the emails and the invitation go out by themselves. They go out (or open) only while Product Checker runs: it keeps running in the background while emails are waiting, and with **Start with Windows** it starts again after a restart. An email that could not go out in time is reported on Home.
    - **Inform attendees:** one email to the people on your **Notifiers** list with every product on the board: product name, units needed, target market, sourcing ETA (the Sourcing & PO deadline, or "done"), when it should be at Amazon, and buttons to Amazon and Alibaba. Each card's Sourcing tab shows what the email says about that product (**In the team email**) and takes its Amazon ASIN and your supplier's Alibaba link; without them the email links to a search. The email is built from the board as it is, lists what changed since the last one, and can carry a message from you. **Automatically too:** when you add or change a product, everyone gets the update once nothing changed for 15 minutes (Settings, Product board: on or off, and the minutes).
    - **Inform deadlines:** next to Inform attendees, one email with just the deadlines: each product, its stage and its deadline ("was due Sat 10 Oct (5 days late)", "due Wed 21 Oct (in 6 days)"), the soonest first and the late ones in red, plus the products without a deadline. Tick who gets it and add a message if you like. It is sent only when you press **Send**, and changes nothing else: the product update and each deadline's own reminder emails go on as before.
    - **Notes & history** on every card, the **Latest** changes under the board, and a **Weekly summary** to copy into WhatsApp or an email.
    - **Removing things:** **Remove** at the top of a card takes the product off the board (its waiting emails are paused); for 10 seconds an **Undo** button brings it straight back, and later, under **Removed** at the bottom of the board, **Put back** does the same (emails whose time has not passed are scheduled again). **Delete for good** waits 10 seconds with an **Undo** button before it deletes the product with everything on its card. A note or a stock count typed by mistake has its own delete button (×).
    - **Working faster on the board:** see [Interactive board](#interactive-board) below: a timeline, filters you can save, quick actions and quick edit on each card, keyboard shortcuts, Undo, and stock and sales read from Amazon.

13. **Gap Finder** (Global → Gap Finder in the sidebar): products that are **proven** in big Amazon markets and **open** in Australia or the UAE. See [Global Gap Finder](#global-gap-finder) below.
13b. **Market Gaps** (Find ideas → Market Gaps): where Amazon Australia and the UAE leave room, from your SmartScout exports, checked live on Amazon. See [Market Gaps](#market-gaps) below.
14. **Market intelligence** (Global → Market intelligence): SmartScout-style views of Australia, the UAE and the USA, built only from what the app already read. See [Market intelligence](#market-intelligence) below.
15. **Ask AI** (Global → Ask AI) and the **Claude Desktop connector**: ask questions about your saved research and your board in plain English. Off until you set it up; it can only read. See [AI: Ask AI and the Claude Desktop connector](#ai-ask-ai-and-the-claude-desktop-connector) below.
16. **Go or no-go** and **the first year** in every keyword report (Money): deal-breakers you can tune, one plain decision, and 52 weeks of profit and cash in a good, likely and poor case. See [Go or no-go, and the first year](#go-or-no-go-and-the-first-year) below.

**Your history is kept.** Every check, Google Trends sample and Helium 10 export is stored in `%LOCALAPPDATA%\ProductChecker\market.db`, with automatic backups in its `backup` folder. Helium 10 exports (Xray, Black Box, Magnet, Cerebro) in your Downloads folder are imported when the app starts, so months you researched before give you history from day one.

The tools are in the sidebar on the left. The theme switch is at the bottom of the sidebar. Each tool has its own address (for example `#/rank`), so the window reopens where you left it.

## Install

1. You need **Python 3.10 or newer** (https://www.python.org/downloads/, with **"Add python.exe to PATH"** ticked) and **Chrome** or **Edge**.
2. Get the code using one of these:
   - On GitHub: **Code → Download ZIP**, then unzip it, for example into `C:\Users\danis\Downloads\Product_Checker\`.
   - With git: `git clone <this repository's URL>`.
3. Double-click **`start.bat`**. The first start installs what the app needs (1–2 minutes) and puts a **Product Checker** shortcut on your desktop.

**Any copy works.** `start.bat` checks at every start that the Python it uses has everything in `requirements.txt` (`deps.py` does the check). It uses the `.venv` folder next to it; a `.venv` that does not work on this PC (copied from another computer, or made by a Python that was removed or upgraded) is made again, a missing package is installed, and when no `.venv` can be made in the folder the packages go into your own Python. **To share the app**, zip the folder without `.venv` (it is large, and each PC makes its own).

**To update:** download the ZIP again and unzip it over the old folder, or run `git pull`. Your settings and history are kept, because they live in `%LOCALAPPDATA%\ProductChecker`. The version you are running is shown at the bottom of the left menu (for example **Version 7.0**). If an older version is still running (in the background, or from another folder), `start.bat` closes it and starts the new one.

## Demand vs sellers

Every check now shows how the buyers are shared out among the sellers:

- **Sales per seller:** buyers a month on page 1 divided by the number of sellers there, for example "≈ 39 sales per seller". **Per local seller** divides by the local sellers only; "none yet" means nobody delivers locally.
- **Sales taken by overseas sellers:** the share of sales going to listings that ship from abroad. A high share means buyers are waiting for slow deliveries, which is a chance for local stock.
- **Sales per seller chart:** one bar per listing, longest first. **Green** = local seller, **orange** = ships from overseas, **grey** = unknown. The thin line on each bar is the range behind the estimate. Hatched bars are the top "100K+" badge, where the real number is unknown. Listings without a badge are counted below the chart, not drawn as zero.
- **Opportunity map** (Library, and Product ideas after **Check AU & UAE**): every product you checked is a dot. **Up** = more buyers a month (log scale), **right** = more local sellers. The **top-left corner**, with many buyers and few local sellers, is where the opportunities are. Hover a dot for its buyers, sellers and typical price (≈ in your currency); click it to open its report.
- **Idea and radar cards** show, after a check, a **Buyers** bar and a **Sellers** bar for each country (local green, overseas orange) and "≈ N buyers per local seller".

All of these come from Amazon's "bought in past month" badges, which are ranges and are missing on many listings, so they are estimates and usually too low. Use them to compare products, not as exact sales.

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

## New product ideas

**Find product ideas** reads two public Amazon lists in each chosen category:

| List | What it shows | Label in the app |
|---|---|---|
| **Movers & Shakers** | the products whose sales rank jumped most in the last 24 hours. This is the earliest public sign of a boom, like tower fans a few summers ago. | **Booming** (rank up 300%+), **Rising** (50%+) |
| **Hot New Releases** | the best-selling products that are new on Amazon | **New #rank** |

The app starts with the safe categories (Home & Kitchen, Kitchen & Dining, Garden, Pets, Sports, Office, Tools, Crafts). Categories with extra rules or big brands, such as Electronics, Toys, Beauty, Health and Appliances, have a dashed outline; click one to include it. **Hide risky products** hides electrical, kids, chemical and medical products. Untick it to see everything, for example tower fans, which count as electrical.

**Your price range:** by default only products that **sell on Amazon for 20–50 US$** are shown. Change the numbers, or switch the currency to **A$**, **AED** or **€**, right above the categories (or in Settings); switching converts the numbers, so the range keeps meaning the same prices. The range is converted for each marketplace automatically: about A$31–78 in Australia and AED 73–184 in the UAE, with the European Central Bank's daily rates (the dirham is fixed to the dollar). Untick **Amazon price** to see every price. The line under the title says how many products the range hid. Changing the range never re-reads Amazon.

**No phones / laptops** (on by default) leaves out phones, tablets, laptops, computers and their accessories: cases, chargers, cables, screen protectors, stands, keyboards, computer mice and earbuds. A mouse trap or a paper notebook stays.

A scan you start yourself reads up to 16 categories (the automatic refresh reads 8, to stay gentle with Amazon).

**How ideas are scored:**
- **Higher:** a bigger rank jump, being high on New Releases, appearing on both lists, few reviews, a price in the target range, and several similar products moving at once (shown as "+N similar").
- **Lower:** warning flags.

**For each idea:**
- **Check AU & UAE:** searches Amazon Australia and UAE for the idea and shows **local vs abroad** sellers. A green **gap** means 4 or fewer local sellers. **Check top 12 in AU & UAE** does the first 12 in one go.
- **Trends:** Google Trends for the last 5 years in your Chrome. Use it to see whether the rise is real and lasting or just a one-off spike.
- **Xray:** jumps to the Xray tab with the keyword filled in.
- **Click the title:** opens the product in your Chrome profile.
- **Find on Alibaba:** opens an Alibaba search for the product in your Chrome, so you can compare supplier prices.
- **Profit?:** type the supplier price per piece (in the currency your supplier quotes in: Settings → Currency) and press **Profit?**. You get a rough profit per sale and margin in A$, AED or US$ (and ≈ in your currency), after Amazon's fees, sea freight, duty, GST/VAT, 12% ads and returns. It assumes a small standard-size item; hover over the result to see the breakdown, and check the real size and weight before ordering.

The words used to search the other countries are shown in a box on each card, and you can edit them.

**Search a niche:** type a niche, e.g. `tower fan`, `dog bed` or `kitchen organiser`, and press **Find niche keywords**. The app asks Amazon's search box for its suggestions (what shoppers actually type) for about 20 variations of your niche, and lists the keywords that come back most often. Tick the keywords you want and the countries, then press **Check selected keywords**. Each keyword then shows local vs abroad sellers, demand ("bought in past month") and fast sellers per country.

A strong idea is **booming or new in the USA, with real demand, but few local sellers in Australia or the UAE yet**.

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

The checks use postcode **2000** (Sydney), **Dubai** and ZIP **10001** (New York). You can change these in Settings. They're set automatically on the first check, and the pills at the top turn green when Amazon confirms them. Amazon remembers them in the app's own browser, so this happens once.

- **One at a time:** the app sets the countries one after another, in a tab brought to the front, because Amazon's location popups only work in the active tab. Several checks at once simply wait their turn.
- **UAE:** Amazon.ae now asks for a city in a box with suggestions. The app types "Dubai" and picks it from the list.
- **Australia:** Amazon.com.au asks for the postcode and then a suburb. The app types the postcode key by key (that is what makes Amazon load the suburbs) and picks Sydney.
- **UK and Germany** (postcodes in Settings, Gap Finder) are only used by the Gap Finder; their Best Sellers lists do not depend on a delivery location.
- **If Amazon's popup still won't take it:** the checker window opens on that page with the popup open, and the progress line says, for example, "UAE: please choose Dubai in the browser window that just opened and press Apply. The app waits up to 2 minutes." Pick the place and press Apply; the app carries on by itself. This only happens for checks you start, never during the automatic refresh.
- **To redo all three:** Settings → **Set locations now**.

## Global Gap Finder

What it looks for: a product that **sells well in at least two of the USA, the UK and Germany**, and that in Australia or the UAE is sold by **one exporter only**, by Amazon only, only from abroad, or by **nobody**.

**1. Proven.** Once a day the app reads the Best Sellers lists of your categories in the USA, the UK and Germany, and the Movers & Shakers lists of the same categories in the UK and Germany (the USA's come from the Product ideas scans). The categories are the ones you picked in Product ideas, else Home & Kitchen, DIY, Garden, Sports, Pet Supplies and Office. A list read in the last 20 hours is not read again, and Best Sellers are read first, so they always fit the daily budget. A market counts as **selling well** with any of:

| Signal | Where it comes from | Shown as |
|---|---|---|
| a Best Sellers top 100 place | the list itself | "#12 in Best Sellers, Kitchen" |
| a Movers & Shakers product now in its Best Sellers top 100 | the "Sales rank: 85 (previously 2,140)" line of the list | "#85 in Best Sellers now, up from #2,140 (Movers & Shakers, Kitchen)" |
| Helium 10 sales | your exports from that market (a UK export is recognised by `UK_` in its name or amazon.co.uk links) | ~1,000/mo (750–1,250, Helium 10): ±25% |
| a "bought in past month" badge | the product page | the badge's range |
| the US sales curve | the US Best Sellers rank (or a US mover's rank) through the app's fitted curve | the curve's 80% range |

The thresholds (300 a month in the USA, 100 in the UK and Germany) are in **Settings → Gap Finder**. A Movers & Shakers product still outside the top 100 only says "rising". The same product is found across markets by its ASIN, else by the same brand with nearly the same title.

**2. Open.** For each proven product, a few a day per market, the app reads in Australia and the UAE:
- the **same ASIN** there (its product page, or the Selling Partner API when you saved keys);
- the **phrase shoppers type** there (Amazon's own suggestions, never with the brand name) and **page 1** of its search;
- the pages of other listings of **the same product** (every word of the phrase in the title), and the **seller profiles** that say where a lone seller is based.

| Gap | What it means |
|---|---|
| **Lone exporter, ships from abroad** | one seller based abroad ships it in slowly, and nobody else sells it |
| **Lone exporter, local FBA** | one foreign seller keeps stock in Amazon's local warehouse, with no rivals |
| **Amazon only** | Amazon itself sells it, and no local third-party seller does |
| **Imported only** | every seller ships it from abroad |
| **Missing** | nobody sells it there |
| Few local rivals (1–4) | not a gap: 1 to 4 local sellers offer the same product |
| Crowded | not a gap: 5 or more |
| Not read | Amazon could not be read there (captcha, block, pause or used-up budget): a gap in the data, **never** shown as missing |

"Somebody else" means another seller on the same listing, or another listing of the same product that delivers locally. Other similar products on page 1 only fill the **local rivals** count.

**Each row shows** the proof per market with its estimate and source, the gap and why, how sure it is, who sells it there, page 1's demand (a floor: Amazon's badges say "50+"), the price there and the **most you can pay your supplier** to keep 20% (Amazon fees for its size, or a small parcel when the size is unknown, sea freight, duty, ads and returns). Warnings come with their reason: **chemical / dangerous goods** (words like stain remover, aerosol, flammable; an item form such as spray or liquid; the page's safety lines or Safety Data Sheet; Amazon's own dangerous-goods attributes with the SP-API), electrical, kids, medical. Buttons: **Amazon**, **Report** (the keyword report of the phrase), **Board**, and **Watch** for a lone exporter: its listing is read every day, and when a **second seller** appears you get a Home alert (and a Windows notification). **Save to Excel** saves the table, and a click on a column header sorts it (click again to reverse). **Check now** runs it yourself (12 products per market; the proof lists are read again only after 20 hours).

**Politeness.** It uses the same throttle as everything else, with its own daily budgets (UK and Germany: 10 list pages and 5 product pages a day; Australia 40 and the UAE 30 reads a day for checks, 20 for the lone-exporter watch). A captcha pauses that marketplace and is never solved by the app; nothing logs in.

**Amazon Selling Partner API (optional, Settings).** With your own seller app keys (client ID and secret from Seller Central → Develop apps, and a refresh token for North America, Europe (the UAE) and/or Far East (Australia)) the app asks Amazon directly whether an ASIN is listed (Catalog Items) and who offers it (Product Pricing getItemOffers: offers, FBA or not, where it ships from, the buy box), instead of reading the page. Amazon's own seller ids are learned from pages, never typed in. The keys are encrypted for your Windows user (`spapi.secret` in the app folder), never written to settings.json, never shown again; **Test connection** lists the marketplaces they can use, **Forget keys** deletes them. Without keys, nothing changes.

**A live example.** B01EIG6A4Q (Miss Mouth's stain remover) was checked on Amazon Australia with Sydney 2000 on 14 October 2026: six offers, the buy box with MangaNaturals AUS from stock in Amazon's Australian warehouse, and page 1 of "stain remover clothes" full of local listings. The Gap Finder calls it **Crowded** (not a gap) with the **chemical / dangerous goods** warning. That recording is kept as a test (`tests/fixtures/gap_B01EIG6A4Q`).

## Market Gaps

What it looks for: niches of Amazon Australia and the UAE where a few sellers take the money and nobody local competes yet. In Australia, for example, Amazon AU itself, then Amazon's **Global Store** (Amazon US, UK, Germany and Japan selling here and shipping it in), take about half of the money, and below them there is a lot of room.

**1. The baseline: your SmartScout exports.** Press **Import SmartScout files** and give the folder that holds the brands and sellers exports (`smartscout - AU brands.xlsx`, `SmartScout - UAE Sellers.xlsx`, the US brands...). Each file is read once, kept with its date (the market comes from `AU`, `UAE` or `US` in its name, else from the Amazon links inside it) and shown as "Baseline: SmartScout, 15 Oct 2026". A SmartScout file left in Downloads is picked up like a Helium 10 export. The Global Store sellers of a sellers export are remembered as sellers based in their country.

From the brands export, every brand's revenue is put in its main niche (SmartScout's primary subcategory) with its dominant seller, and each niche gets one of:

| Gap | What it means |
|---|---|
| **Shipped in from abroad** | 40%+ of the niche is sold by Amazon's Global Store or import resellers (DesertcartAE, JetSouq...): shoppers wait 1–3 weeks, so local stock would win them |
| **Amazon, then a gap** | Amazon itself takes 50%+ and the next brand makes less than a quarter of what Amazon makes |
| **Few brands** | 1 to 3 brands with a real share (5%+), the top 3 take 70%+, 1.5 sellers each or fewer |
| **Bigger in the USA** | the niche's share of the US market is 3+ times its share here (needs the US brands export) |
| Spread out | not a gap: many brands and sellers share it |

The **score** (0–100) adds demand (the niche's revenue, 30), openness (35), the reviews to beat (15), a price in the workable band (10, the Go or no-go band) and 12-month growth (10). Phones, laptops, books, renewed and similar niches are marked risky and hidden unless you tick **Show phones, books and other risky niches**. SmartScout's "Generic" brand (every unbranded listing, filed under one niche) is left out. **Brands missing here** lists brands selling over US$200K a month in the USA that are not in the Australian or UAE export; **Top sellers** shows how few sellers take half of the money.

**2. Live: Refresh AU & UAE.** SmartScout was a one-off, so the app keeps the picture fresh by reading Amazon itself:
- **Best Sellers now:** the Best Sellers lists of your categories (the ones picked in Product ideas, else Home, Kitchen, Sports, Pets, Office, Garden and Home Improvement; a list read in the last 20 hours is reused) and the buy box of the top 5 of each: each brand's share (from the sales curve, else by rank) and who holds the buy boxes: Amazon, the Global Store, sellers abroad or local sellers. A category read in the last 3 days is skipped.
- **Niches:** the best-scored open niches never checked, or checked over a week ago: the phrase shoppers type there (Amazon's suggestions), page 1 of its search and the buy boxes of its top 3 listings. **Open** = 4 or fewer fast listings and 40%+ of page 1's sales ship from abroad (or the top buy boxes are Amazon's Global Store or sellers abroad); **Amazon only**; **Some local sellers**; **Filling up** (5–9 fast listings); **Crowded** (10+); **Not read** (blocked, paused or out of budget: never shown as open). **Check now** on a row checks that niche at once; each check is kept, so the row shows how it changed.
- A niche that opens, or an open one that starts filling up, raises a Home alert (and a Windows notification).
- With the automatic refresh on (Settings → Gap Finder → **Re-check Market Gaps niches every day**, on by default), 3 categories and 4 niches per market are read again each day, within their own daily budget (Australia 25 and the UAE 20 page reads).

Each row has **Amazon** (the search), **Report** (the keyword report of its phrase), **Check now**, **Watch** (the phrase goes on the Watchlist, checked every day) and **Board**. **Save to Excel** saves the niches, the brands missing here and the top sellers.

**The Gap Finder uses it too:** proven products whose title holds every word of a strong Market Gaps niche (an open gap scoring 70+) are checked first in that market. And a listing sold only by Amazon's Global Store ("Amazon US" on Amazon Australia) is now an **Imported only** gap, not "Amazon only".

## Market intelligence

Seven tabs, for Australia, the UAE or the USA. Nothing extra is read from Amazon for them: they use the product pages, seller pages and searches the app reads anyway (a product page now also keeps its "customers also bought / also viewed", "frequently bought together" and sponsored carousels and how many pictures, bullet points, a video and A+ content it has; a search keeps its brand banner, video ads and sponsored carousels).

| Tab | What it shows |
|---|---|
| **Sellers** | every seller read on a product page in the last 90 days: country, rating, listings, buy boxes, brands, FBA or FBM, sales a month (badges as a range, else the sales-rank curve) and share, first seen. A listing's sales go to its buy-box seller; Amazon itself is one row. With the SP-API, every seller on a listing counts too. |
| **Brands** | per niche (a keyword you searched): each brand's share of page-1 sales from the "bought in past month" badges, its listings, growth against a search 3 to 6 weeks older, and whether Amazon itself sells there. Across all niches: the brands found in the most niches. A brand comes from the listing, or a brand name already known in the market; otherwise it is the title's first word, marked **from the title** (a word of the keyword itself is never taken for a brand). |
| **Category gaps** | the Gap Finder's proven products per category and market: how many are open, imported, Amazon only, missing or sold locally; not-read checks are counted apart. |
| **Seller origins** | where sellers are based (flags and bars): every seller read in the market by share of sales, or one niche's 10 best listings. |
| **Related products** | a product's network from those carousels: products like it are **competitors**, products bought with it but unlike it are **add-on and bundle ideas**; a thick line is "frequently bought together". **Read its page now** reads it again. |
| **Ad watch** | one ad-pressure score (0–100) per keyword: the share of the first 20 places that are ads, a brand banner at the top, video ads and how many brands advertise, with the brands and the ad formats they buy. Searches saved before this version show the sponsored listings only (low confidence). |
| **Listing scorecard** | type your ASIN (and its keyword, else the board card's or your phrase is used): your title, bullet points, pictures, video, A+ content, rating, reviews and price against the top 10, each ahead / level / behind with what to do, and a score. **Read the top-10 pages still missing** reads them. |

**Amazon out of stock:** when Amazon itself sold a product you watch (a watched ASIN, a watched keyword's top listings, a lone exporter you watch, or a product on your board) in Australia or the UAE and its newest page shows it unavailable or sold by someone else, you get a Home alert (and a Windows notification), once per outage.

## Go or no-go, and the first year

At the top of **Money** in every keyword report, **Go or no-go** gives one plain decision with the reason, then every rule with what it found and what to do.

- **Deal-breakers** (one fail makes it a **No-go**, whatever the score): the usual page-1 price outside a workable band (US$15–70, A$20–110, AED 55–260), more reviews to beat than 2,000 / 800 / 500 (USA / Australia / UAE), page-1 sales under 1,500 / 300 / 200 a month by the badges, a margin under 15% with your supplier price (after Amazon's fees, freight, duty and ads at 12%), Amazon itself selling over 30% of the top listings read, restricted words in the top titles (medical, fits another brand), and demand in one season only (Google Trends, when the pattern repeats every year).
- **Warnings**: many fast-delivery listings on page 1 (over 40 / 20 / 15) and falling Google searches. Each lowers the decision one step.
- With no deal-breaker failing, the opportunity score gives **Go** (60+), **Maybe** (40–59) or **Weak**. Something not read (no badges, sellers not read, no Google Trends, no supplier price) shows as **not read**: never a pass and never a fail, and three or more make it Maybe at best. The confidence says how much was read.
- **Your rules**: Settings → **Go or no-go rules** changes every limit per market, which restricted words stop you, which trends count as falling, or switches a rule off. Each decision keeps the rules it was made under (a short fingerprint is shown); when a keyword was decided before under other rules, or got another decision, the report says so.

**The first year, week by week** (under the profit calculator, with your supplier price): the first order and 52 weeks in a **good**, **likely** and **poor** case. Sales start at 20% of the pace a new listing might win (from page-1 badges and the reviews to beat) and reach it after 6 / 10 / 16 weeks as reviews come in; ads cost 35% of sales in weeks 1–4, 20% in weeks 5–12 and 10% after (likely case; more in the poor case, which also sells 5% cheaper); the Google Trends season moves sales month by month; a re-order is placed when stock would run out before new stock arrives, paid when placed, and covers the restocking time plus 8 weeks. You see the profit in the year, the week your money is back, the most money tied up at once, units sold and lost while out of stock, re-orders and what is left. A line chart shows the money in or out week by week; where it crosses zero, your money is back. It is a rough plan, not a promise: fund the likely case and make sure you could carry the poor one.

Both ideas (deal-breakers with tiers and recorded rules, and good / likely / poor yearly projections) come from looking at the open project *amazon-omniscient*. Its code is not used: its licence does not allow that, and this is written for this app's markets and data.

## Interactive board

**On each card:**

- **Progress ring** (top right): how far the product is from idea to re-order, in %. It turns red when a deadline is late or stock needs a re-order now, and green when every stage is done.
- **Countdown chip:** the most urgent date in one line: "3 days late", "Re-order in 5 days", "Due in 4 days", "Order in 12 days" (for a product not ordered yet: the last day to order for its next seasonal peak) or "At Amazon in 20 days". Overdue dates come first, then the nearest. Hover it for the day and the market.
- **Tags:** type them in the card panel, next to the colours, with commas between them (up to 8). Click a tag on a card to show only cards with it.
- **Small charts** (once there are counts or sales): units left over time with a dashed red line at your low-stock level, and the units sold each day (last 3 weeks) with your monthly target as a dashed line (target ÷ 30.4 a day). Hover them for the numbers and where they come from.
- **Quick actions** (hover the card): **Amazon** (the listing, or a search when there is no ASIN), **Alibaba** (your supplier's link, or a search), email the team (the product update email), add a note, and **quick edit**.
- **Quick edit:** change the stage, the price per unit, the units per market, the deadline's due day and the go-live day without opening the card. Each change saves by itself; moving the due day moves its reminder emails too.

**Timeline** (the switch at the top of the board): a month calendar with every date on the board: deadlines, deposit / produced / shipped / arrived, go-live days, when a shipment should be at Amazon, the last day to re-order and, for a product not ordered yet, the last day to order for its next seasonal peak (by sea, or by air when sea is already too late; from the keyword's Google Trends season, as in the report's Launch plan). Drag a deadline, a shipment step or a go-live day to another day to change it; the dashed chips are worked out by the app, so they move by themselves. Click a chip to open the product.

**Filters:** find by name, keyword, ASIN or tag; one market; one stage; one tag; **Overdue only** (late deadline or a re-order due now). **Save filter** keeps the current filter under a name; pick it from **Saved filters** later (the bin button deletes the one picked). They work on the timeline too.

**Undo:** after you move a card (drag, quick edit or keyboard), remove it or delete it for good, an **Undo** button shows for 10 seconds (Ctrl+Z works too).

**Keyboard:** **N** adds a product, **/** jumps to Find, the arrow keys move between cards, **Shift + ← / →** moves the card to the stage before or after (only into stages it has unlocked), **Enter** opens it.

**On a phone or a narrow window** the columns stack, the quick actions are always shown, and the timeline lists only the days that have something.

**Stock and sales from Amazon (optional).** With your SP-API keys saved (Settings, Gap Finder panel), the app reads, every 4 hours while it runs, how many units are left in Amazon's warehouses (FBA inventory) and how many were ordered each day (order metrics, last 5 weeks) for every board product that has an ASIN, in each of its markets that your keys cover. The Re-order tab then says **Live from Amazon · updated …**, the selling pace comes from **Amazon orders** (the last 4 weeks, once a week of days is in) rather than from two of your counts, and **Update from Amazon now** reads it at once (the same button is at the top of the board). A count from Amazon is marked (Amazon) in the list; you can still type your own. Amazon's answer is never guessed: if Amazon refuses or a product is not in your inventory, nothing is saved and the reason is shown. Switch it off in Settings, Product board.

## AI: Ask AI and the Claude Desktop connector

Two ways to ask questions like "Which gaps in Australia have the fewest sellers?", "What is the price band and the reviews to beat for oven cleaner spray in the UAE?", "Is stain remover clothes a go in Australia at US$0.80 a unit?" or "Which board products need a re-order soon?". Both use the same eleven **read-only tools**: they read only what Product Checker saved in `market.db`. They never open Amazon, click, send an email, buy or change anything.

| Tool | What it answers |
|---|---|
| `list_keywords` | the keywords searched, with markets and dates |
| `search_products` | products read, by words of the title, a brand or an ASIN: price, rating, reviews, rank, sales a month, buy box |
| `get_product` | one listing: what its page showed, sales a month (range and basis), the last months, its sellers, other markets, any Gap Finder result |
| `keyword_report` | one keyword in one market: page-1 sales, price band, reviews to beat, who sells and how fast, the top 10, the Google trend |
| `find_gaps` | the Gap Finder's results in Australia and the UAE, with proof, sellers, price and the highest product cost for a 20% margin |
| `lookup_seller`, `lookup_brand` | sellers and brands from Market intelligence |
| `board_status`, `live_stock` | your board: stages, next dates, stock left, selling pace, re-order days |
| `recent_alerts` | the newest Home alerts |
| `go_no_go` | Go / Maybe / Weak / No-go for a keyword, every deal-breaker with why, and the first year in three cases (give your supplier price) |

Every answer carries where its numbers come from and when they were read; an estimate is a range with its basis ("bought in past month" badges, the sales-rank curve, Helium 10); something not read (blocked, paused, never searched) is reported as not read, never as 0. Text from Amazon pages (titles, seller names) is passed as data, and the AI is told never to follow instructions inside it.

**Claude Desktop connector (MCP, no key in this app).** `mcp_server.py` is a Model Context Protocol server (stdio). It opens `market.db` read-only, so it works while the app runs. To add it:

1. Install Claude Desktop and sign in.
2. In Claude Desktop: **Settings → Developer → Edit Config**. This opens `%APPDATA%\Claude\claude_desktop_config.json`.
3. Paste the block from **Settings → AI** in Product Checker (**Copy** button). It looks like this, with your folder:
   ```json
   {
     "mcpServers": {
       "product-checker": {
         "command": "C:\\...\\HuntyZone\\.venv\\Scripts\\python.exe",
         "args": ["C:\\...\\HuntyZone\\mcp_server.py"]
       }
     }
   }
   ```
   If the file already has `mcpServers`, add only the `"product-checker": {...}` part inside it.
4. Quit Claude Desktop completely (also from the tray) and start it again. **product-checker** shows under the tools button, with its eleven tools.
5. To test the setup without Claude: `.venv\Scripts\python.exe mcp_server.py --check` lists the tools and your newest keywords.

**Ask AI in the app (optional, costs a little).** Off until you save an **Anthropic API key** in **Settings → AI** (from console.anthropic.com; it is kept encrypted for your Windows user, like the SP-API keys, and is never shown again). Then type a question on the **Ask AI** page (Ctrl+Enter asks). Claude picks the tools, reads, and answers in plain English; under the answer you see which tools it read and what the question cost.
- **Model:** `claude-sonnet-4-5` by default; `claude-haiku-4-5` is cheaper, `claude-opus-4-1` is the strongest. Another Claude model name works too and is priced like Sonnet (an estimate).
- **Monthly limit:** US$5 by default (Settings → AI). Every call's tokens are counted and priced at Anthropic's list prices; at the limit Ask AI stops until next month. **Health** shows this month's total, calls and tokens.
- At most 6 rounds of reading per question; a question that needs more gets an answer from what was read.

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
- **Still the old version after an update** (check the version at the bottom of the left menu): start the app with `start.bat` in the new folder. A desktop shortcut made by an old folder still starts the old folder: delete it, or right-click it and check its folder. **Start with Windows** follows the folder you last started the app from.
- **If the background browser misbehaves:** press **Restart** under Settings → Background browser.
- **"No categories found" or a slow Australia / UAE scan:** watch the progress line. If it says Amazon wants a captcha, solve it in the browser window that opens; the scan then carries on. If Amazon's category menu can't be read, the app uses built-in categories and keeps a copy of the page in `%LOCALAPPDATA%\ProductChecker\debug\` (for example `categories_AU.html`). Send that file if it keeps happening. In **Product ideas** you can also pick other categories.

## For developers

| File | What it does |
|---|---|
| `app.py` | local web server and app window; API routes, history and jobs. Only this app's own page can call the API (per-run token plus Host/Origin checks) |
| `config.py` | settings schema with validation, keyword normalising, each marketplace's local date |
| `jobs.py` | background jobs started from the window (checks, scans), pruned after 30 minutes |
| `ui/index.html` | the page shell: sidebar, top bar and the views |
| `ui/css/tokens.css`, `ui/css/app.css` | colours for the light and dark themes (tested for contrast), layout and components |
| `ui/js/` | `api.js` (every request carries the token), `app.js` (routes, theme, status), `components.js`, `format.js`, `legacy.js` (the tools) |
| `storage.py` | `market.db` (SQLite): every search, list scan, Trends sample and import; one writer thread, backups, retention |
| `importer.py` | Helium 10 exports (Xray, Black Box, Magnet, Cerebro) into the database, once per file |
| `trends.py` | Google Trends through the background browser, one request at a time, paused when Google pushes back |
| `product.py` | Amazon product pages (rank, badge, price, offers, seller, brand, how it ships, size and weight, reviews), critical reviews pages and seller pages (business address) |
| `reports.py` | the keyword report and Library data, built from the database plus `engine/`; who sells a keyword's top listings and the one-sentence summary |
| `throttle.py`, `scheduler.py`, `collectors.py` | polite pacing, daily budgets and cooldowns per source; the background schedule; what each scheduled task does |
| `analysis.py`, `notify.py`, `toast.ps1` | alerts after each refresh, and the Windows notification |
| `winsys.py`, `singleton.py` | Start with Windows, battery check, one copy of the app at a time |
| `engine/` | analysis maths without any I/O: sales-badge ranges, Google Trends features and labels, list surges, alerts, who sells (`sellers.py`) and the one-sentence summary (`verdict.py`) |
| `ui/js/charts.js`, `ui/js/views/` | the charts (plain SVG) and the keyword report and Library screens |
| `amazon_check.py` | background browser (Playwright) that searches Amazon and reads delivery dates |
| `xray.py` | Xray export analysis and the Downloads watcher |
| `ideas.py` | Movers & Shakers / New Releases scan, idea scoring, and niche keywords from Amazon's search suggestions |
| `file_rank.py` | reads Helium 10 exports, flags and ranks products |
| `chrome_profiles.py` | finds Chrome/Edge profiles and opens links in one |
| `start.bat`, `deps.py` | the launcher: picks a Python that has what the app needs (the `.venv` folder, else your own), repairs or rebuilds `.venv`, then starts `app.py` |
| `board.py`, `mailer.py`, `engine/board.py`, `engine/hints.py`, `engine/remind.py`, `engine/inform.py` | the Product board: cards, orders, stock, hints, deadlines and their emails, the product update email (Inform attendees) and the deadlines email (Inform deadlines), each card's next step and the research answers on its approval checks, opening them in Gmail / Google Calendar or sending them through Gmail; the timeline, saved filters, moving a due day, and stock and daily sales from the SP-API (`board.sync_live`) |
| `gapfinder.py`, `engine/gaps.py`, `engine/offers.py`, `ui/js/views/gaps.js` | the Global Gap Finder: proof lists, proven products, the checks in Australia and the UAE, the lone-exporter watch (`engine/` holds the rules, without I/O) |
| `smartscout.py`, `marketgaps.py`, `engine/marketgaps.py`, `ui/js/views/mgaps.js` | Market Gaps: SmartScout exports as a dated baseline (`ss_brand`, `ss_seller`), niches and their gap and score, brands missing here, and the live readings of Australia and the UAE (`mg_check`, `mg_category`: Best Sellers concentration, niche verdicts, alerts) |
| `spapi.py`, `vault.py` | the optional Amazon Selling Partner API client (token, pacing, retries) and the small encrypted secrets file |
| `intel.py`, `engine/intel.py`, `engine/ads.py`, `ui/js/views/intel.js` | Market intelligence: sellers, brands, category gaps, seller origins, related products, ad pressure, the listing scorecard and the Amazon stock-out alert |
| `ai_tools.py`, `mcp_server.py`, `ai_chat.py`, `ui/js/views/ai.js` | AI: the eleven read-only tools (with sources), the MCP connector for Claude Desktop (stdio JSON-RPC, no extra package; market.db opened read-only), and Ask AI (Anthropic Messages API with tool use, monthly cap, usage counter) |
| `engine/decide.py`, `engine/yearplan.py`, `reports.decision`, `ui/js/views/rules.js` | Go or no-go (deal-breakers, warnings, tiers, your rules and their fingerprint) and the first year week by week in three cases |

Markets: `config.MARKETS` (USA, Australia, UAE) are the markets you sell in, with fees, reports and the board. `config.PROOF_MARKETS` (`GB` for the UK, `DE` for Germany) are read only by the Gap Finder; `amazon_check.MARKETS` knows all five, and amazon.de pages are asked for in English (`with_lang`).

To run the tests: `python -m pip install pytest`, then `python -m pytest -q`. On a development PC, set the environment variable `PC_NO_EMAIL=1` before starting the app: no email is then sent or opened there (the tests never send or open one either).
