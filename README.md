# Tickover

A watch deal finder. It finds watches that need minor repair, estimates what each one is worth once fixed, and ranks them by likely profit. It can also send the best finds to your phone.

## Where it searches

| Site | How | Why it's useful |
|---|---|---|
| **eBay UK** | Official eBay API | Biggest UK source of "spares or repair" watches |
| **eBay Germany, France, Italy, USA** | Same eBay keys | Germany especially: lots of cheap *für Bastler* (for tinkerers) watches, and fewer UK flippers watching. Searches in the local language and only shows sellers who post to the UK |
| **Etsy** | Official Etsy API | Vintage pieces from sellers who often don't repair watches themselves |
| **Anywhere else** (Vinted, Gumtree, Facebook, auctions) | `--check` mode | Paste the title and price and get an instant score |

These are the sites that officially allow automated searching. The tool doesn't scrape sites that don't, so it won't get blocked and won't put your accounts at risk.

## What it does

1. Runs your search phrases on each site, plus everything eBay lists as *For parts or not working*.
2. Reads each title (in English, German, French or Italian) to work out the **brand**, **movement** and **fault** (battery, glass, crown, strap, service…).
3. Converts foreign prices to pounds and adds **import VAT** for anything coming from abroad.
4. Estimates **repair cost**, **resale value**, **eBay fees** and **profit / ROI**.
5. Throws out the traps: fakes/homages, "movement only" parts, water damage, job lots, heavy damage.
6. Scores every deal 0–100 and writes **report.html** (open in any browser, filter by site) and **deals.csv** (Excel/Sheets).

## Setup (about 15 minutes, once)

**1. Install Python 3.11 or newer** from python.org (on Windows, tick "Add Python to PATH"). Nothing else to install.

**2. Get free eBay API keys**
- Sign up at https://developer.ebay.com and log in.
- Go to *Application Keysets* and create a **Production** keyset (not Sandbox).
- eBay will ask about "Marketplace Account Deletion" notifications. Choose the **exemption** option and say you don't store eBay user data.
- Copy the **App ID (Client ID)** and **Cert ID (Client Secret)** into `config.toml`.

**3. Choose your eBay sites.** In `config.toml`, `marketplaces = ["EBAY_GB", "EBAY_DE"]` is set by default. Add `"EBAY_FR"`, `"EBAY_IT"` or `"EBAY_US"` if you want them.

**4. Etsy (optional)**
- Go to https://www.etsy.com/developers, sign in, and create an app (a short form; say it's for personal use searching listings).
- Once approved, copy the **Keystring** and **Shared Secret** into the `[etsy]` section and set `enabled = true`.

**5. Try it**
```
python watch_finder.py --demo    # sample data, no keys needed
python watch_finder.py           # live search
python watch_finder.py --all     # show everything, including rejects
```
Then open `report.html`.

## How Tickover gets its numbers

For every run:

1. **Searches** eBay and filters out parts (movements, dials, links, cases), fakes, water damage and job lots.
2. **Reads the full listing** for the top 40 watches: the seller's description and item details. This catches hidden problems such as "new battery fitted, still not working" and finds the exact reference number.
3. **Prices each watch against similar working watches** for sale on eBay UK, using the reference number when the seller gives one, then knocks 20% off because asking prices run higher than sale prices. Each card says where its resale figure came from: "from 6 same ref for sale", "from 5 similar for sale" or "brand average" (treat that one with caution).
4. **Learns from your own sales** (see below).

## Your flip log (flips.csv)

Tickover creates `flips.csv` in this folder. Open it in Numbers or Excel and add a row for each watch you buy:

| Column | What to put |
|---|---|
| Date bought | e.g. 2026-11-02 |
| Watch | Brand and model, e.g. Tissot PR100 |
| Paid £ (incl. postage) | What you paid in total |
| Parts £ | Batteries, crowns, straps, etc. |
| Tickover resale estimate £ | The Resale figure on its card when you bought it |
| Date sold, Sold £ | Fill in when it sells |
| Selling fees and postage £ | eBay/Vinted fees plus what you paid to post it |

Save it as CSV. Once you've sold **3 watches of a brand** (or 5 in total), Tickover compares what they really sold for with what it estimated, and adjusts its future estimates to match. Cards then say "tuned to your sales". Each run also prints your running total: watches sold, profit made, and how many you're still holding.

## Parts checklist

Every deal has a **"What you'll need"** section. Tap it to see:

- each part or job the watch needs (battery, crystal, crown, bracelet link, service…), with an estimated cost
- **eBay and Amazon links** for each part, pre-filled with the watch's brand and model (or a search for a watchmaker, for jobs you'd outsource)
- a **"Hard to find"** warning on parts that are scarce, like genuine links for premium or vintage bracelets
- **tools** you'll need, listed separately because you only buy them once

Tick items off as you get them; the ticks are remembered in that browser. The parts costs are what the profit figure uses, so the numbers reflect what the repair will really cost.

Battery sizes: the `[batteries]` section in `config.toml` links the exact battery for models you know (e.g. Tissot PRX quartz = Renata 371). Otherwise it searches "[model] battery size", and you can read the size off the old battery once the back is off. Add models as you learn them.

## Check a listing from any website

Spotted something on Vinted, Gumtree, Facebook or at an auction? Paste the title and price:

```
python watch_finder.py --check "Omega Seamaster automatic not running" --price 220 --postage 8
python watch_finder.py --check "Longines Automatik läuft nicht" --price 150 --abroad
```

You'll get the brand, the faults it spotted, the cost breakdown, estimated profit and a verdict. Add `--abroad` if it ships from outside the UK so import VAT is included.

## Phone alerts (optional, free)

1. Install the **ntfy** app (iOS / Android).
2. Make up a hard-to-guess topic name, e.g. `mackenzy-watch-deals-8f3k`, and subscribe to it in the app.
3. Put the same name in `config.toml` → `ntfy_topic`.

Deals scoring above `alert_min_score` are pushed to your phone once each. Tap the alert to open the listing.

## Run it automatically

- **Windows:** Task Scheduler → Create Basic Task → every 1–2 hours → Action: start `python` with argument `C:\path\to\watch_finder.py`.
- **Mac:** run `crontab -e` and add `0 * * * * /usr/bin/python3 /path/to/watch_finder.py`

eBay allows 5,000 searches a day on the free tier. A run with UK and Germany uses about 20, so hourly is fine.

## Tuning it (this is where the money is)

- **`[brands]` prices are rough starting points.** For the brands you buy most, check eBay *Sold* listings for working examples and update the number. Better numbers mean better scores.
- **`[adjust]`** knocks value off ladies' models and quartz versions of expensive brands, since those sell for less.
- **`[fx]`** exchange rates were set on 28 Oct 2026. Update them every month or two.
- **`[imports]`** is set to 20% VAT plus a £12 courier fee on parcels over £135.
- **`max_price`** limits your spend per watch (in £, also applied to overseas sites).
- **`min_profit` / `min_roi_percent`** control how picky the report is.
- **`queries`** (and `queries_de`, `queries_fr`…): add model names you know well.
- **`ebay_fee_percent`**: set to 0 if you're selling as a private seller in the UK.

## Reading the score

| Score | Meaning |
|---|---|
| 70+ | Strong: good margin, easy fix, decent seller |
| 50–69 | Worth a look |
| under 50 | Thin margin, harder fix, or more risk |

Easy fixes (battery, strap, glass) score higher than "needs service". Mechanical non-runners have a £90 service cost added automatically. Quartz watches that have "stopped" are flagged as "often just a battery". Overseas buys lose a few points because returns are harder.

## Limits to know

- It only reads the **title**. Always read the full listing and look at the photos before buying.
- Resale values are estimates. The tool can't see dial condition, originality or box/papers.
- Etsy doesn't give postage or shop country in search results, so it assumes £5 UK / £15 abroad and uses the listing currency as a guide to where it's from.
- Rolex and other heavily faked brands get a score penalty. Verify serials and movement photos.
- Vinted, Gumtree, Facebook, Catawiki and UK auction sites (the-saleroom, John Pye) don't offer public APIs, so use their own saved-search alerts and `--check` mode for those.
