#!/usr/bin/env python3
"""
Tickover — watch deal finder
----------------------------
Searches eBay (UK, plus Germany/France/Italy/US if you want) and Etsy for
watches that need minor repair, works out what each one is likely worth once
fixed — including import costs for overseas buys — and ranks them by profit.

Usage:
    python watch_finder.py                 # live search (keys in config.toml)
    python watch_finder.py --demo          # try it with sample listings, no keys
    python watch_finder.py --check "Omega Seamaster automatic not running" --price 220
                                           # score any listing from any site
                                           # (Vinted, Gumtree, Facebook, auctions…)

Outputs (same folder):
    report.html   ranked deals with photos — open in any browser
    deals.csv     the same, for Excel / Google Sheets
    seen.json     remembers what you've already been alerted about

Standard library only — no installs needed (Python 3.11+).
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WRISTWATCH_CATEGORY = "31387"   # eBay "Wristwatches"
PARTS_CONDITION_ID = "7000"     # eBay "For parts or not working"

MARKET_INFO = {
    # marketplace : (country code, flag/label, currency)
    "EBAY_GB": ("GB", "eBay UK", "GBP"),
    "EBAY_DE": ("DE", "eBay DE", "EUR"),
    "EBAY_FR": ("FR", "eBay FR", "EUR"),
    "EBAY_IT": ("IT", "eBay IT", "EUR"),
    "EBAY_US": ("US", "eBay US", "USD"),
}

# ─────────────────────────────────────────────────────────────────────────────
#  Fault knowledge: title words → likely fix, cost, difficulty
#  English, German, French and Italian so overseas listings are understood.
#  cost = rough £ for parts/outsourcing.  difficulty 1 (easy) … 5 (watchmaker)
# ─────────────────────────────────────────────────────────────────────────────
FAULTS = [
    ("Needs battery",
     r"needs? (a |new )?batter|flat batter|batter(y|ies) (dead|flat|needed)|no batter"
     r"|batterie (leer|fehlt|wechsel|tauschen|erneuern)|braucht (eine |neue )?batterie|neue batterie n[öo]tig"
     r"|pile ([àa] changer|vide|hs|morte)|sans pile|batteria (scarica|da sostituire)|senza batteria", 5, 1),
    ("Stopped / not running",
     r"not (working|running|ticking)|stopped|doesn'?t (work|run|tick)|non[- ]?runner|\bdead\b"
     r"|l[äa]uft nicht|steht still|funktioniert nicht|\bdefekt\b|ohne funktion"
     r"|ne fonctionne (pas|plus)|ne marche (pas|plus)|en panne|hors service|\bhs\b"
     r"|non funziona|non funzionante|ferm[oa]\b|guast[oa]", 15, 2),
    ("Glass / crystal",
     r"(scratch|crack|chip|broken|damaged)\w* (glass|crystal)|(glass|crystal) (scratch|crack|chip|broken|damaged)"
     r"|glas (zerkratzt|gesprungen|kaputt|besch[äa]digt)|(zerkratzt|gesprungen)\w* glas"
     r"|verre (ray[ée]|cass[ée]|f[êe]l[ée])|vetro (graffiato|rotto|incrinato)", 25, 2),
    ("Crown missing/loose",
     r"(missing|no|loose|broken) crown|crown (missing|loose|broken)"
     r"|krone (fehlt|lose|defekt|kaputt)|ohne krone|couronne (manquante|cass[ée]e)|sans couronne"
     r"|corona (mancante|rotta)|senza corona", 25, 3),
    ("Bracelet link missing",
     r"(missing|no|needs? (an? )?(extra|more)|short of a?) ?(bracelet )?links?\b|links? (missing|removed)|bracelet (too )?(small|short)"
     r"|glied(er)? fehl|fehlende glieder|armband zu kurz|maillons? manquants?|maglie mancanti", 20, 2),
    ("Strap / bracelet",
     r"(broken|missing|no|worn|loose) (bracelet |strap )?(strap|bracelet|clasp|pins?)"
     r"|(strap|bracelet|clasp) (broken|missing|damaged)"
     r"|armband (defekt|kaputt|fehlt)|ohne armband|bracelet cass[ée]|sans bracelet|cinturino (rotto|mancante)", 15, 1),
    ("Timekeeping",
     r"(runs?|running|loses|gains|losing|gaining) (fast|slow|time)|stops? (after|when)|intermittent"
     r"|geht (nach|vor|falsch)|bleibt stehen|avance|retarde|s'arr[êe]te|ritarda|anticipa", 25, 3),
    ("Needs service",
     r"needs? (a )?(service|servicing|clean)|overdue (a )?service"
     r"|revision (n[öo]tig|f[äa]llig|erforderlich)|braucht (eine )?revision|\b[üu]berholung"
     r"|r[ée]vision (à|a) pr[ée]voir|besoin d'une r[ée]vision|da revisionare|revisione", 130, 4),
    ("Loose / missing hand",
     r"(loose|missing|fallen|detached) hands?|hands? (loose|missing|fallen|detached)"
     r"|zeiger (lose|fehlt|ab)|aiguille (d[ée]croch[ée]e|manquante)|lancetta (staccata|mancante)", 45, 4),
    ("Date/day not changing",
     r"(date|day) (doesn'?t |not |won'?t |isn'?t )+(chang|work|advanc)"
     r"|datum (schaltet|springt) nicht|date ne (change|saute) pas|data non (cambia|scatta)", 40, 4),
    ("Bezel issue",
     r"bezel (stuck|loose|missing|doesn'?t turn|damaged)|(stuck|loose|missing) bezel"
     r"|l[üu]nette (fest|lose|fehlt)|lunette (bloqu[ée]e|manquante)|ghiera (bloccata|mancante)", 35, 3),
    ("Untested",
     r"untested|not tested|unable to test|can'?t test|sold as seen"
     r"|ungepr[üu]ft|nicht getestet|non test[ée]|non testato", 20, 2),
    ("Sold as faulty",
     r"f[üu]r bastler|bastleruhr|als ersatzteil|ersatzteilspender|pour pi[èe]ces|pour bricoleur"
     r"|per ricambi|per pezzi di ricambio|da riparare|spares\s*(or|and|&|/|\+)?\s*repairs?|\bspares?\b(?!\s+links?)|"
     r"for (repair|restoration|parts)|\brestoration\b|\bproject\b|\bdefekt\b|\bbastler\b|\bnon[- ]?runner\b", 40, 3),
]

# Things that usually mean "walk away" — big risk or not a real watch
RED_FLAGS = [
    ("Fake / homage",
     r"replica|fake|homage|\bstyle\b|inspired|copy|\bnot genuine\b|tribute|unbranded|\baaa\b"
     r"|replik|f[äa]lschung|nachbau|im stil|contrefa[çc]on|r[ée]plique|imitazione|stile"),
    ("Parts only",
     r"\b(movement|case|dial|hands|crown|bezel|strap|bracelet|box|papers) only\b|empty case|no movement|for movement"
     r"|nur (das )?(werk|uhrwerk|geh[äa]use|zifferblatt|armband)|\b(uhr)?werk nur\b|ohne (uhr)?werk|leergeh[äa]use"
     r"|mouvement seul|bo[îi]tier seul|cadran seul|solo (movimento|cassa|quadrante)"),
    ("Water / rust",
     r"water damage|water ingress|\brust|corro|condensation|moisture"
     r"|wasserschaden|wasser im|\brost|rouille|oxyd|eau dans|ruggine|acqua dentro"),
    ("Heavy damage",
     r"smashed|shattered|badly damaged|heavily damaged|bent case|missing parts|incomplete|beyond repair"
     r"|stark besch[äa]digt|unvollst[äa]ndig|tr[èe]s ab[îi]m[ée]|incompl[èe]te|molto danneggiat"),
    ("Job lot",
     r"job ?lot|bundle|joblot|\blot of\b|x\s?\d{2,}\b|\d{2,} ?(watches|uhren|montres|orologi)"
     r"|konvolut|sammlung von|\blot de\b|\blotto di\b"),
    ("Box / accessory",
     r"\bwatch box\b|watch winder|display case|watch roll|tool kit|strap only"
     r"|uhrenbox|uhrenbeweger|nur box|bo[îi]te seule|scatola"),
]

QUARTZ_RE = re.compile(r"quartz|quarz|quarzo|battery|\bkinetic\b|solar|eco[- ]?drive", re.I)
AUTO_RE = re.compile(r"automatic|automatik|automatique|automatico|self[- ]?winding|\bauto\b|mechanical"
                     r"|hand[- ]?wind|manual wind|handaufzug|remontage manuel|carica manuale|m[ée]canique|meccanico", re.I)
LADIES_RE = re.compile(r"\b(ladies|lady|ladys|women'?s|womens|damen(uhr)?|femme|donna)\b", re.I)
GOLD_RE = re.compile(r"\b(9|14|18)\s?(ct|k|kt|carat|kar[äa]t)\b|solid gold|\b(585|750)(er)? gold|gold (585|750)|or 18|oro 18", re.I)


@dataclass
class Listing:
    item_id: str
    title: str
    price: float                 # converted to £
    shipping: float              # converted to £
    url: str
    image: str = ""
    condition: str = ""
    buying: str = "Buy It Now"
    bids: int = 0
    ends: str = ""
    seller: str = ""
    feedback_pct: float = 100.0
    feedback_score: int = 0
    listed: str = ""
    source: str = "eBay UK"
    country: str = "GB"          # where the item ships from
    orig_price: str = ""         # e.g. "€120" when not in pounds
    description: str = ""        # seller's full description (filled in for shortlisted watches)
    aspects: dict = field(default_factory=dict)   # eBay item specifics: Model, Reference Number, Movement…


@dataclass
class Deal:
    listing: Listing
    brand: str = ""
    resale: float = 0.0
    movement: str = "Unknown"
    faults: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    repair_cost: float = 0.0
    import_cost: float = 0.0
    difficulty: int = 0
    total_cost: float = 0.0
    profit: float = 0.0
    roi: float = 0.0
    score: int = 0
    resale_basis: str = "Brand average"
    notes: list[str] = field(default_factory=list)
    checklist: list[dict] = field(default_factory=list)   # parts/jobs, each with cost + links
    tools: list[dict] = field(default_factory=list)       # one-off tools (not added to cost)


def to_gbp(amount: float, currency: str, fx: dict) -> float:
    if currency in ("GBP", "", None):
        return amount
    rate = fx.get(currency)
    if rate is None:
        return amount   # unknown currency — leave as is
    return round(amount * float(rate), 2)


def fmt_orig(amount: float, currency: str) -> str:
    sym = {"EUR": "€", "USD": "$"}.get(currency, currency + " ")
    return f"{sym}{amount:,.0f}"


def _get_json(url: str, headers: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ─────────────────────────────────────────────────────────────────────────────
#  Source 1: eBay Browse API (official)
# ─────────────────────────────────────────────────────────────────────────────
class EbayClient:
    TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
    SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

    def __init__(self, cfg: dict, fx: dict):
        self.cfg, self.fx = cfg, fx
        self.token = None

    def _get_token(self) -> str:
        # Keys can come from environment variables (GitHub Secrets) or config.toml
        cid = os.environ.get("EBAY_CLIENT_ID") or self.cfg["client_id"]
        secret = os.environ.get("EBAY_CLIENT_SECRET") or self.cfg["client_secret"]
        if not cid or "PASTE" in cid:
            sys.exit("✗ Add your eBay client_id and client_secret to config.toml first "
                     "(or run with --demo to try it out).")
        auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }).encode()
        req = urllib.request.Request(self.TOKEN_URL, data=body, headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)["access_token"]
        except urllib.error.HTTPError as e:
            sys.exit(f"✗ eBay rejected your keys ({e.code}). Check client_id / client_secret "
                     f"are the PRODUCTION keys, not Sandbox.\n{e.read().decode()[:300]}")

    def search(self, market: str, query: str | None, min_gbp: float, max_gbp: float,
               parts_only: bool = False, limit: int = 200) -> list[Listing]:
        if not self.token:
            self.token = self._get_token()
        country, label, cur = MARKET_INFO[market]
        # convert the £ budget into the marketplace's currency
        rate = float(self.fx.get(cur, 1)) if cur != "GBP" else 1.0
        lo, hi = round(min_gbp / rate), round(max_gbp / rate)
        filters = [f"price:[{lo}..{hi}]", f"priceCurrency:{cur}", "buyingOptions:{FIXED_PRICE|AUCTION}",
                   f"itemLocationCountry:{country}"]
        if market != "EBAY_GB":
            filters.append("deliveryCountry:GB")           # only sellers who post to the UK
        if parts_only:
            filters.append(f"conditionIds:{{{PARTS_CONDITION_ID}}}")
        params = {"category_ids": WRISTWATCH_CATEGORY, "filter": ",".join(filters),
                  "sort": "newlyListed", "limit": str(limit)}
        if query:
            params["q"] = query
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-EBAY-C-MARKETPLACE-ID": market,
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=GB",   # postage quoted to the UK
            "Accept": "application/json",
        }
        try:
            data = _get_json(f"{self.SEARCH_URL}?{urllib.parse.urlencode(params)}", headers)
        except urllib.error.HTTPError as e:
            print(f"  ! {label} search '{query}' failed ({e.code}) — skipping")
            return []
        return [self._to_listing(it, label, country) for it in data.get("itemSummaries", [])]

    ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/"

    def get_details(self, l: "Listing") -> bool:
        """Fill in the seller's description and item specifics for one listing."""
        if not l.item_id.startswith("ebay:"):
            return False
        if not self.token:
            self.token = self._get_token()
        market = {"eBay UK": "EBAY_GB", "eBay DE": "EBAY_DE", "eBay FR": "EBAY_FR", "eBay IT": "EBAY_IT",
                  "eBay US": "EBAY_US"}.get(l.source, "EBAY_GB")
        url = self.ITEM_URL + urllib.parse.quote(l.item_id[5:], safe="")
        headers = {"Authorization": f"Bearer {self.token}", "X-EBAY-C-MARKETPLACE-ID": market,
                   "Accept": "application/json"}
        try:
            it = _get_json(url, headers)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return False
        raw = " ".join(x for x in (it.get("conditionDescription", ""), it.get("shortDescription", ""),
                                    it.get("description", "")) if x)
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        l.description = re.sub(r"\s+", " ", text).strip()[:6000]
        l.aspects = {a.get("name", ""): a.get("value", "") for a in it.get("localizedAspects", []) or []}
        return True

    def comparable_prices(self, query: str) -> list[float]:
        """Prices (in £) of similar WORKING watches currently for sale on eBay UK."""
        if not self.token:
            self.token = self._get_token()
        params = {"q": query, "category_ids": WRISTWATCH_CATEGORY, "limit": "50",
                  "filter": "conditionIds:{1000|1500|2750|3000},buyingOptions:{FIXED_PRICE},"
                            "itemLocationCountry:GB,priceCurrency:GBP,price:[20..20000]"}
        headers = {"Authorization": f"Bearer {self.token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
                   "Accept": "application/json"}
        try:
            data = _get_json(f"{self.SEARCH_URL}?{urllib.parse.urlencode(params)}", headers)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return []
        words = [w for w in query.lower().split() if len(w) > 2 and w != "ladies"]
        out = []
        for it in data.get("itemSummaries", []):
            title = it.get("title", "")
            if _NOT_WORKING_RE.search(title) or part_reason(title):
                continue
            need = len(words) if len(words) <= 3 else len(words) - 1
            if sum(w in title.lower() for w in words) < max(1, need):
                continue                      # not really the same model
            p = it.get("price") or {}
            try:
                out.append(float(p.get("value", 0)))
            except (TypeError, ValueError):
                pass
        return out

    def _to_listing(self, it: dict, label: str, country: str) -> Listing:
        opts = it.get("buyingOptions", [])
        is_auction = "AUCTION" in opts and "FIXED_PRICE" not in opts
        p = it.get("currentBidPrice") if is_auction and it.get("currentBidPrice") else it.get("price", {})
        amount, cur = float(p.get("value", 0) or 0), p.get("currency", "GBP")
        ship, ship_cur = None, cur
        for opt in it.get("shippingOptions", []) or []:
            sc = opt.get("shippingCost") or {}
            if sc.get("value") is not None:
                ship, ship_cur = float(sc["value"]), sc.get("currency", cur)
                break
        if ship is None:     # not quoted — assume a typical tracked parcel
            ship_gbp = 4.0 if country == "GB" else 18.0
        else:
            ship_gbp = to_gbp(ship, ship_cur, self.fx)
        seller = it.get("seller", {}) or {}
        return Listing(
            item_id=f"ebay:{it.get('itemId', '')}",
            title=it.get("title", ""),
            price=to_gbp(amount, cur, self.fx),
            shipping=ship_gbp,
            url=it.get("itemWebUrl", ""),
            image=(it.get("image") or {}).get("imageUrl", ""),
            condition=it.get("condition", ""),
            buying="Auction" if is_auction else "Buy It Now",
            bids=int(it.get("bidCount", 0) or 0),
            ends=it.get("itemEndDate", ""),
            seller=seller.get("username", ""),
            feedback_pct=float(seller.get("feedbackPercentage", 100) or 100),
            feedback_score=int(seller.get("feedbackScore", 0) or 0),
            listed=it.get("itemCreationDate", ""),
            source=label,
            country=country,
            orig_price="" if cur == "GBP" else fmt_orig(amount, cur),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Source 2: Etsy Open API v3 (official) — good for vintage pieces
# ─────────────────────────────────────────────────────────────────────────────
class EtsyClient:
    SEARCH_URL = "https://openapi.etsy.com/v3/application/listings/active"

    def __init__(self, cfg: dict, fx: dict):
        self.cfg, self.fx = cfg, fx
        key, secret = cfg.get("keystring", ""), cfg.get("shared_secret", "")
        if not key or "PASTE" in key:
            raise ValueError("no key")
        self.api_key = f"{key}:{secret}" if secret else key

    def search(self, query: str, min_gbp: float, max_gbp: float) -> list[Listing]:
        base = {"keywords": query, "limit": "100", "sort_on": "created", "sort_order": "desc"}
        if self.cfg.get("uk_only", False):
            base["shop_location"] = "United Kingdom"
        headers = {"x-api-key": self.api_key, "Accept": "application/json"}
        try:
            data = _get_json(f"{self.SEARCH_URL}?{urllib.parse.urlencode(base)}", headers)
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:200]
            if e.code in (401, 403):
                print(f"  ! Etsy rejected the key ({e.code}) — check keystring/shared_secret. {msg}")
            else:
                print(f"  ! Etsy search '{query}' failed ({e.code}) — skipping")
            return []
        out = []
        for it in data.get("results", []):
            p = it.get("price") or {}
            try:
                amount = int(p.get("amount", 0)) / int(p.get("divisor", 100) or 100)
            except (TypeError, ValueError):
                continue
            cur = p.get("currency_code", "GBP")
            gbp = to_gbp(amount, cur, self.fx)
            if not (min_gbp <= gbp <= max_gbp):
                continue
            uk = cur == "GBP"   # Etsy search doesn't return shop country; currency is a fair guide
            out.append(Listing(
                item_id=f"etsy:{it.get('listing_id', '')}",
                title=html.unescape(it.get("title", "")),
                price=gbp,
                shipping=float(self.cfg.get("assumed_postage_uk" if uk else "assumed_postage_abroad", 5 if uk else 15)),
                url=it.get("url", ""),
                condition="",
                buying="Buy It Now",
                listed=datetime.fromtimestamp(it.get("created_timestamp", 0) or 0, timezone.utc).isoformat(),
                seller=f"shop {it.get('shop_id', '')}",
                source="Etsy",
                country="GB" if uk else "XX",
                orig_price="" if uk else fmt_orig(amount, cur),
            ))
        return out


# ─────────────────────────────────────────────────────────────────────────────
#  Analysis
# ─────────────────────────────────────────────────────────────────────────────
def detect_brand(title: str, brands: dict[str, float]) -> tuple[str, float]:
    t = title.lower()
    best = ("", 0.0)
    for name, value in brands.items():          # longest match wins ("grand seiko" beats "seiko")
        if re.search(rf"\b{re.escape(name)}\b", t) and len(name) > len(best[0]):
            best = (name, float(value))
    return best


def import_costs(l: Listing, imp: dict) -> float:
    """UK import VAT (+ any duty/courier fee) on watches bought from abroad."""
    if l.country == "GB":
        return 0.0
    goods = l.price + l.shipping
    vat = goods * imp.get("vat_percent", 20) / 100
    duty = goods * imp.get("duty_percent", 0) / 100
    fee = imp.get("courier_fee", 12) if goods > 135 else 0      # under £135 the seller collects VAT, no fee
    return round(vat + duty + fee, 2)


# ─────────────────────────────────────────────────────────────────────────────
#  Parts checklist: what each fault needs, rough cost, and where to buy it
# ─────────────────────────────────────────────────────────────────────────────
BRAND_NAMES = {"heuer": "TAG Heuer", "jaeger": "Jaeger-LeCoultre", "iwc": "IWC", "g-shock": "Casio G-Shock",
               "baume": "Baume & Mercier", "raymond weil": "Raymond Weil", "grand seiko": "Grand Seiko"}
STOPWORDS = set("""mens men's men gents gent ladies lady ladys womens women's unisex watch watches wrist wristwatch
    automatic auto quartz manual hand wind winding mechanical vintage antique rare genuine original swiss made
    stainless steel gold plated silver dial black white blue green day date chronograph spares repair repairs
    for parts not working needs battery service the a an and with of in on new old used boxed retro classic
    herrenuhr damenuhr uhr armbanduhr automatik quarz montre homme femme orologio uomo donna""".split())
REF_RE = re.compile(r"^(?=.*\d)[a-z0-9][a-z0-9.\-/]{3,}$", re.I)
YEARS_RE = re.compile(r"^(1[89]\d0s?|19\d\d|20\d\d|\d+mm|\d+(ct|k|kt)|\d+s)$", re.I)

TOOLS = {
    "opener":   ("Case-back opener (screw and snap-off)", "watch case back opener tool kit"),
    "grease":   ("Silicone grease for seals", "watch gasket silicone grease"),
    "press":    ("Crystal press with dies", "watch crystal press"),
    "springbar":("Spring bar tool", "spring bar tool watch"),
    "pin":      ("Bracelet pin pusher / link remover", "watch bracelet link remover tool"),
    "demag":    ("Watch demagnetiser", "watch demagnetiser"),
    "levers":   ("Hand levers and movement holder", "watch hand remover levers movement holder"),
}


def search_links(query: str, google: bool = False) -> list[tuple[str, str]]:
    q = urllib.parse.quote_plus(query)
    if google:
        return [("Find one", f"https://www.google.com/search?q={q}")]
    return [("eBay", f"https://www.ebay.co.uk/sch/i.html?_nkw={q}"),
            ("Amazon", f"https://www.amazon.co.uk/s?k={q}")]


def model_query(title: str, brand: str) -> str:
    """Best guess at 'Brand Model Ref' from a listing title, for parts searches."""
    name = BRAND_NAMES.get(brand, brand.title())
    words = re.findall(r"[\w.\-/']+", title)
    low = [w.lower() for w in words]
    refs = [w for w in words if REF_RE.match(w) and not YEARS_RE.match(w)
            and w.lower() not in brand.split() and w.lower() not in ("g-shock", "eco-drive")]
    model = []
    if brand:
        bw = brand.split()[-1]
        if bw in low:
            for w in words[low.index(bw) + 1:]:
                lw = w.lower()
                if lw in STOPWORDS or lw in name.lower().split() or REF_RE.match(w):
                    if model:
                        break
                    continue
                model.append(w)
                if len(model) == 2:
                    break
    parts = [name] + model + refs[:1]
    return " ".join(p for p in parts if p).strip()


def battery_for(title: str, table: dict) -> str:
    t = title.lower()
    for key, cell in table.items():
        if all(w in t for w in key.lower().split()):
            return cell
    return ""


def build_checklist(d: Deal, cfg: dict, brand_value: float) -> None:
    title, mv = d.listing.title, d.movement
    mq = model_query(title, d.brand) or "watch"
    premium = brand_value >= 600
    vintage = bool(re.search(r"vintage|antique|19[4-8]\d|[4-8]0s\b", title, re.I))
    items, tools = [], []

    def add(item, cost, query=None, note="", google=False, hard=False):
        items.append({"item": item, "cost": float(cost), "note": note, "hard": hard,
                      "links": search_links(query, google) if query else []})

    def battery_item(note=""):
        cell = battery_for(title, cfg.get("batteries", {}))
        if cell:
            add(f"Battery {cell}", 3, f"{cell} watch battery", note or "Known size for this model")
        else:
            add("Watch battery", 4, f"{mq} battery size",
                note or "Size is printed on the old battery — check once the back is off")
        tools.extend(["opener", "grease"])

    for f in d.faults:
        if f == "Needs battery":
            battery_item()
        elif f == "Stopped / not running" and "Battery already tried" in d.faults:
            continue                       # the movement-swap item already covers it
        elif f == "Stopped / not running":
            if mv == "Quartz":
                if "Needs battery" not in d.faults:
                    battery_item("Try a battery first — most stopped quartz watches just need one")
                add("Movement replacement (only if a battery doesn't fix it)", 0, f"{mq} quartz movement",
                    "Not counted in the cost — check the price before buying if you're unsure")
            elif mv.startswith("Automatic"):
                if "Needs service" not in d.faults:
                    add("Service by a watchmaker (mechanical non-runner)", 200 if premium else 105,
                        f"independent watchmaker {mq} service UK", google=True)
            else:
                add("Battery (if it's quartz)", 4, f"{mq} battery size", "Check the dial or case back for 'quartz'")
                tools.append("opener")
        elif f == "Battery already tried":
            add(f"Replacement quartz movement for {mq}", 35, f"{mq} quartz movement",
                "Match the calibre number printed on the old movement. Try a demagnetiser first")
            tools.extend(["opener", "levers", "demag"])
        elif f == "Glass / crystal":
            add(f"Crystal for {mq}", 35 if premium else 18, f"{mq} crystal glass",
                "Measure the diameter. Scratched acrylic can often just be polished out")
            add("Acrylic polish (if it's acrylic)", 0, "polywatch acrylic watch glass polish",
                "Costs a few pounds, lasts for years")
            tools.append("press")
        elif f == "Crown missing/loose":
            add(f"Crown and stem for {mq}", 60 if premium else 22, f"{mq} crown stem",
                "Genuine crowns for premium brands are pricier", hard=premium and vintage)
        elif f == "Strap / bracelet":
            add(f"Strap or bracelet for {mq}", 15, f"{mq} strap", "Check the lug width (mm) in the listing photos")
            add("Spring bars", 2, "watch spring bars assorted")
            tools.append("springbar")
        elif f == "Bracelet link missing":
            hard = premium or vintage
            add(f"Bracelet link for {mq}", 90 if premium else 20, f"{mq} bracelet link",
                "Hard-to-find part — genuine links can be scarce" if hard else "Match the link width and end shape",
                hard=hard)
            add("Plan B: replacement bracelet or strap", 0, f"{mq} replacement bracelet",
                "Not counted — use if no matching link turns up")
            tools.append("pin")
        elif f == "Timekeeping":
            add("Demagnetise first (often fixes fast running)", 0, None, "Uses the demagnetiser below")
            add("Regulation by a watchmaker (if that doesn't fix it)", 25, f"watch regulation service {mq}", google=True)
            tools.append("demag")
        elif f == "Needs service":
            add(f"Full service by a watchmaker", 220 if premium else 130,
                f"independent watchmaker {mq} service UK", google=True)
        elif f == "Loose / missing hand":
            add(f"Hand set for {mq}", 25 if premium else 15, f"{mq} hands", hard=vintage)
            add("Hand fitting by a watchmaker (or DIY with tools)", 25)
            tools.append("levers")
        elif f == "Date/day not changing":
            add("Date mechanism repair by a watchmaker", 45, f"{mq} date not changing repair", google=True)
        elif f == "Bezel issue":
            add(f"Bezel / insert / click spring for {mq}", 35, f"{mq} bezel insert")
        elif f == "Untested":
            add("Risk buffer — unknown faults", 20, None, "Untested watches often hide a second problem")
            if mv != "Automatic / Mechanical":
                battery_item("Fit a battery to test it")
        elif f in ("Sold as faulty", "Fault not stated") and mv.startswith("Automatic"):
            add("Service by a watchmaker (fault not stated)", 220 if premium else 130,
                f"independent watchmaker {mq} service UK",
                "Most faulty mechanical watches need at least a service — ask the seller for details", google=True)
        elif f in ("Sold as faulty", "Fault not stated"):
            add("Unknown fault — ask the seller exactly what's wrong", 40, None,
                "This is a guess; the real cost could be higher or lower")

    d.checklist = items
    seen = []
    for t in tools:
        if t not in seen:
            seen.append(t)
    d.tools = [{"item": TOOLS[t][0], "links": search_links(TOOLS[t][1])} for t in seen]


# ─────────────────────────────────────────────────────────────────────────────
#  Parts detector: listings that are a PART of a watch, not a whole watch
# ─────────────────────────────────────────────────────────────────────────────
_MOVEMENT_RE = re.compile(r"(?<!swiss )(?<!japan )(?<!japanese )(?<!miyota )\b(movements?|mvt|uhrwerk|werke?|workings|"
                          r"mouvement|movimento)\b", re.I)
_LINKS_RE = re.compile(r"\blinks?\b", re.I)
_LINK_FAULT_RE = re.compile(r"(missing|needs?|no|short of)\s+(\w+\s+)?links?", re.I)
_LINK_INCLUDED_RE = re.compile(r"(&|\band\b|\bwith\b|\+|\bplus\b|\bincl\w*)\s+(\w+\s+){0,2}links?", re.I)
_OTHER_PART_RE = re.compile(
    r"\bcrowns\b|\bpart\b|\bdials?\s*(\+|&|\band\b|-\s*spares|\bonly\b|\bfor\b)|\bdials?\s*/\s*hands|\bdials?\b[^,]{0,8}(\+|&)\s*hands|\b(dial|case|hands|bezel|crystal|glass|strap|"
    r"bracelet|clasp|buckle|box|papers)\s+only\b|\bcase\s*(\+|&|\band\b|\bfor\b|-\s*spare|\bonly\b)|"
    r"\bwatch case\b|geh[äa]use|zifferblatt|\bspare part\b|\b(to fit|fits)\b|\bcase ?back\b|\bbezel insert\b|"
    r"\b(clasp|buckle|deployant)\b(?!.*\bwatch\b)", re.I)


_BATTERY_TRIED_RE = re.compile(
    r"(tried|fitted|put|replaced|changed|installed|inserted)\s+(in\s+)?(a\s+)?(brand\s+)?(new|fresh)\s+(battery|cell)|"
    r"new (battery|cell) (has been |was )?(fitted|installed|tried|put in)|"
    r"(battery|cell) (has been |was |been )?(replaced|changed|tried)|even with (a )?new (battery|cell)|"
    r"(tried|tested)\s+(replacing|changing|swapping|putting in|fitting)\s+(in\s+)?(the\s+|a\s+|its\s+)?(battery|cell)|"
    r"(replac|chang|swapp|fitt)\w*\s+(the\s+|a\s+|its\s+)?(battery|cell)\s+(with|for)\s+(a\s+)?(new|fresh)|"
    r"neue batterie|nouvelle pile|batteria nuova", re.I)
_STILL_DEAD_RE = re.compile(r"not (work|run|tick|go)|doesn'?t (work|run|tick|move|go|start)|still (not|doesn|won)|"
                            r"stopped|twitch|won'?t (run|start|work|go)|no (life|movement)|non[- ]?runner|"
                            r"l[äa]uft nicht|ne fonctionne|non funziona", re.I)
_NEGATION_RE = re.compile(r"\b(no|not|without|never|free of|nor|zero)\b(\W+\w+){0,3}\W*$", re.I)
_DESC_FLAGS = [lbl for lbl, _ in RED_FLAGS if lbl in ("Water / rust", "Heavy damage")]
_DESC_SKIP_FAULTS = {"Untested", "Sold as faulty"}


def _desc_hit(rx: str, text: str) -> bool:
    """True if the pattern appears in the description and isn't negated ('no water damage')."""
    for m in re.finditer(rx, text, re.I):
        if not _NEGATION_RE.search(text[max(0, m.start() - 40):m.start()]):
            return True
    return False


def read_description(d: "Deal", cost_diff: list) -> None:
    """Add faults and warnings found in the seller's full description."""
    text = d.listing.description
    if not text:
        return
    if _BATTERY_TRIED_RE.search(text) and _STILL_DEAD_RE.search(text) and not d.movement.startswith("Automatic"):
        if "Battery already tried" not in d.faults:
            d.faults.append("Battery already tried")
            if "Needs battery" in d.faults:
                d.faults.remove("Needs battery")
            cost_diff[1] = max(cost_diff[1], 3)
            d.notes.append("Seller says a new battery didn't fix it: likely needs a movement swap or a clean")
    for label, rx, c, df in FAULTS:
        if label in _DESC_SKIP_FAULTS or label in d.faults:
            continue
        if label == "Needs battery" and "Battery already tried" in d.faults:
            continue
        if _desc_hit(rx, text):
            d.faults.append(label)
            cost_diff[1] = max(cost_diff[1], df)
            d.notes.append(f"{label}: found in the seller's description")
    for label, rx in RED_FLAGS:
        if label in _DESC_FLAGS and label not in d.flags and _desc_hit(rx, text):
            d.flags.append(f"{label} (in description)")


def reference_of(l: "Listing") -> str:
    """Best reference/model number from eBay item specifics, if the seller filled it in."""
    for key in ("Reference Number", "Model Number", "MPN", "Reference number", "Referenznummer", "Herstellernummer"):
        v = str(l.aspects.get(key, "")).strip()
        if len(v) >= 4 and re.search(r"\d", v) and v.lower() not in ("does not apply", "n/a", "unknown", "nicht zutreffend"):
            return v
    return ""


def part_reason(title: str) -> str:
    """Return a short reason if the listing looks like a watch part rather than a whole watch."""
    t = title.lower()
    if _MOVEMENT_RE.search(t):
        return "Movement only"
    if _LINKS_RE.search(t) and not _LINK_FAULT_RE.search(t) and not _LINK_INCLUDED_RE.search(t):
        return "Bracelet links only"
    if _OTHER_PART_RE.search(t):
        return "Part, not a whole watch"
    return ""


def analyse(l: Listing, cfg: dict) -> Deal:
    d = Deal(listing=l)
    text = l.title.lower()          # faults come from the title; eBay's condition label is generic
    money = cfg["money"]

    d.brand, base = detect_brand(l.title, cfg["brands"])
    if not d.brand and l.aspects.get("Brand"):
        d.brand, base = detect_brand(str(l.aspects["Brand"]), cfg["brands"])
    if QUARTZ_RE.search(text):
        d.movement = "Quartz"
    elif AUTO_RE.search(text):
        d.movement = "Automatic / Mechanical"
    else:
        mv = str(l.aspects.get("Movement", "")).lower()
        if QUARTZ_RE.search(mv):
            d.movement = "Quartz"
        elif AUTO_RE.search(mv):
            d.movement = "Automatic / Mechanical"

    for label, rx in RED_FLAGS:
        if re.search(rx, text, re.I):
            d.flags.append(label)
    pr = part_reason(l.title)
    if pr and "Parts only" not in d.flags:
        d.flags.append(pr)

    cost, diff = 0.0, 0
    for label, rx, c, df in FAULTS:
        if re.search(rx, text, re.I):
            if label == "Strap / bracelet" and "Bracelet link missing" in d.faults \
                    and not re.search(r"strap|clasp|armband|cinturino", text):
                continue             # "missing bracelet link" is the link fault, not a broken strap
            if label == "Sold as faulty" and any(f != "Untested" for f in d.faults):
                continue             # a specific fault is already known — don't double count
            d.faults.append(label)
            cost += c
            diff = max(diff, df)

    cd = [cost, diff]
    read_description(d, cd)
    cost, diff = cd
    if d.movement.startswith("Automatic") and "Stopped / not running" in d.faults:
        diff = max(diff, 4)
    if not d.faults and "parts" in l.condition.lower():
        d.faults.append("Fault not stated")
        diff = 3
    brand_value = base
    build_checklist(d, cfg, brand_value)
    if not d.faults and l.source == "Etsy":
        d.notes.append("No fault in title — Etsy sellers often describe it in the listing")
    adj = cfg.get("adjust", {})
    if LADIES_RE.search(text) or str(l.aspects.get("Department", "")).lower() in ("women", "ladies", "damen", "femme", "donna"):
        base *= adj.get("ladies_factor", 0.6)
        d.notes.append("Ladies' size: resale value reduced")
    if d.movement == "Quartz" and base >= 300:
        base *= adj.get("luxury_quartz_factor", 0.75)
    if GOLD_RE.search(text):
        d.notes.append("Gold case: scrap value gives a safety net")
        base += 150
    if l.country != "GB":
        d.notes.append("Ships from abroad: import VAT included, allow extra delivery time")

    d.resale = round(base * 0.85, 2) if base else 0.0
    d.resale_basis = "Brand average"
    d.repair_cost = round(sum(i["cost"] for i in d.checklist), 2)
    d.difficulty = diff
    d.import_cost = import_costs(l, cfg.get("imports", {}))
    finalise(d, cfg)
    return d


def finalise(d: Deal, cfg: dict) -> None:
    """Work out fees, profit, return and score from the current resale estimate."""
    money, l = cfg["money"], d.listing
    fees = d.resale * money["ebay_fee_percent"] / 100 + (0.30 if money["ebay_fee_percent"] else 0)
    d.total_cost = round(l.price + l.shipping + d.import_cost + d.repair_cost, 2)
    d.profit = round(d.resale - d.total_cost - fees - money["resale_shipping"], 2)
    d.roi = round(d.profit / d.total_cost * 100, 1) if d.total_cost else 0.0
    d.score = score(d)


# ─────────────────────────────────────────────────────────────────────────────
#  Market check: price each promising watch against similar WORKING listings
# ─────────────────────────────────────────────────────────────────────────────
_NOT_WORKING_RE = re.compile(r"spares|repair|not working|non[- ]?runner|for parts|parts only|untested|faulty|defekt|"
                             r"bastler|broken|needs battery|stopped|strap only|box only|links?|movement|dial only", re.I)


def market_check(deals: list[Deal], cfg: dict, client: "EbayClient") -> int:
    mc = cfg.get("market_check", {})
    if not mc.get("enabled", True):
        return 0
    factor = float(mc.get("asking_to_sold", 0.8))
    min_n = int(mc.get("min_comparables", 4))
    checked, cache = 0, {}
    for d in deals[: int(mc.get("max_checks", 40))]:
        name = BRAND_NAMES.get(d.brand, d.brand.title())
        ref = reference_of(d.listing)
        q = f"{name} {ref}" if ref else model_query(d.listing.title, d.brand)
        if not q or q.strip().lower() == name.lower():
            d.notes.append("Model not clear from title: resale is a brand average, check sold prices")
            continue
        if LADIES_RE.search(d.listing.title) and not ref:
            q += " ladies"
        if not ref:
            if d.movement == "Quartz":
                q += " quartz"
            elif d.movement.startswith("Automatic"):
                q += " automatic" if re.search(r"automat", d.listing.title, re.I) else " manual"
            if re.search(r"vintage|antique|19[2-8]\d|[2-8]0s\b", d.listing.title, re.I):
                q += " vintage"
        if q not in cache:
            cache[q] = client.comparable_prices(q)
            checked += 1
            time.sleep(0.3)
        prices = cache[q]
        if len(prices) >= min_n:
            prices = sorted(prices)
            low_q = prices[len(prices) // 4]            # lower quartile: a cautious, realistic price
            cap = float(cfg["brands"].get(d.brand, 0)) * float(mc.get("max_vs_brand", 1.5))
            if LADIES_RE.search(d.listing.title):
                cap *= cfg.get("adjust", {}).get("ladies_factor", 0.6)
            est = low_q * factor
            if cap and est > cap:
                est = cap
                d.notes.append("Resale capped: similar listings vary a lot, check sold prices")
            d.resale = round(est, 2)
            d.resale_basis = (f"Based on {len(prices)} similar working watches for sale"
                              + (f" (ref {ref})" if ref else ""))
            finalise(d, cfg)
        else:
            d.notes.append("Few similar watches for sale: resale is a brand average, check sold prices")
    return checked




# ─────────────────────────────────────────────────────────────────────────────
#  Your flip log: Tickover learns from what your watches really sell for
# ─────────────────────────────────────────────────────────────────────────────
FLIPS_HEADER = ["Date bought", "Watch", "Paid £ (incl. postage)", "Parts £", "Tickover resale estimate £",
                "Date sold", "Sold £", "Selling fees and postage £", "Notes"]


def _num(v) -> float | None:
    try:
        return float(str(v).replace("£", "").replace(",", "").strip())
    except ValueError:
        return None


def load_flips(path: Path, brands: dict) -> tuple[dict, dict]:
    """Read flips.csv. Returns (calibration ratios by brand + '*', summary)."""
    if not path.exists():
        path.write_text(",".join(FLIPS_HEADER) + "\n", encoding="utf-8")
        return {}, {}
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    ratios: dict[str, list] = {}
    done, profit, open_ = 0, 0.0, 0
    for r in rows:
        paid, sold = _num(r.get("Paid £ (incl. postage)")), _num(r.get("Sold £"))
        est = _num(r.get("Tickover resale estimate £"))
        if paid is None:
            continue
        if sold is None:
            open_ += 1
            continue
        done += 1
        profit += sold - paid - (_num(r.get("Parts £")) or 0) - (_num(r.get("Selling fees and postage £")) or 0)
        if est and est > 0:
            b = detect_brand(r.get("Watch", ""), brands)[0] or "?"
            ratios.setdefault(b, []).append(sold / est)
            ratios.setdefault("*", []).append(sold / est)
    cal = {}
    for b, xs in ratios.items():
        if (b == "*" and len(xs) >= 5) or (b not in ("*", "?") and len(xs) >= 3):
            xs = sorted(xs)
            cal[b] = (max(0.5, min(1.5, xs[len(xs) // 2])), len(xs))
    return cal, {"sold": done, "holding": open_, "profit": round(profit, 2)}


def apply_calibration(deals: list, cal: dict, cfg: dict) -> None:
    for d in deals:
        hit = cal.get(d.brand) or cal.get("*")
        if not hit or d.resale <= 0:
            continue
        ratio, n = hit
        d.resale = round(d.resale * ratio, 2)
        who = BRAND_NAMES.get(d.brand, d.brand.title()) if d.brand in cal else "all"
        d.resale_basis += f", adjusted by your {n} {who} sale{'s' if n != 1 else ''}"
        finalise(d, cfg)


def score(d: Deal) -> int:
    if not d.brand or d.resale <= 0:
        return 0
    s = 0.0
    s += min(d.profit / 8, 40)                 # up to 40 for £320+ profit
    s += min(max(d.roi, 0) / 5, 25)            # up to 25 for 125%+ ROI
    s += {0: 10, 1: 15, 2: 12, 3: 6, 4: 0, 5: -5}.get(d.difficulty, 0)   # easy fixes favoured
    l = d.listing
    if l.source.startswith("eBay"):
        if l.feedback_pct >= 99 and l.feedback_score >= 50:
            s += 5
        elif l.feedback_pct < 97:
            s -= 10
    if l.buying == "Auction" and l.bids == 0:
        s += 5
    if l.buying == "Auction" and l.bids >= 8:
        s -= 8
    if "Untested" in d.faults:
        s -= 5
    if l.country != "GB":
        s -= 5                                  # slower, harder to return
    if d.brand == "rolex":
        s -= 10                                 # fake risk — verify carefully
    if d.flags:
        s = min(s - 25 * len(d.flags), 10)      # red flags: never rank these highly
    return int(max(0, min(100, round(s))))


# ─────────────────────────────────────────────────────────────────────────────
#  Output
# ─────────────────────────────────────────────────────────────────────────────
def write_csv(deals: list[Deal], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Score", "Source", "Title", "Brand", "Movement", "Price £", "Original price", "Postage £",
                    "Import £", "Est repair £", "Est resale £", "Est profit £", "ROI %", "Faults", "Warnings",
                    "Notes", "Resale based on", "Parts needed", "Type", "Bids", "Ends", "Seller", "Feedback %", "Link"])
        for d in deals:
            l = d.listing
            w.writerow([d.score, l.source, l.title, d.brand.title(), d.movement, l.price, l.orig_price,
                        l.shipping, d.import_cost, d.repair_cost, d.resale, d.profit, d.roi,
                        "; ".join(d.faults), "; ".join(d.flags), "; ".join(d.notes), d.resale_basis,
                        "; ".join(f"{i['item']} (£{i['cost']:.0f})" for i in d.checklist), l.buying, l.bids,
                        l.ends, l.seller, l.feedback_pct if l.source.startswith("eBay") else "", l.url])


def _ends_in(iso: str) -> str:
    if not iso:
        return ""
    try:
        end = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    secs = (end - datetime.now(timezone.utc)).total_seconds()
    if secs <= 0:
        return "ended"
    h = int(secs // 3600)
    return f"{h // 24}d {h % 24}h" if h >= 24 else f"{h}h {int(secs % 3600 // 60)}m"


def _gauge(score: int) -> str:
    """Score as a fine watch bezel: 60 minute marks, gold arc to the score, numeral in the centre."""
    tier = "hot" if score >= 70 else "good" if score >= 50 else "low"
    r, c = 25, 2 * 3.14159 * 25
    ticks = "".join(
        f'<line x1="32" y1="{2.5 if i % 5 == 0 else 3.5}" x2="32" y2="{7 if i % 5 == 0 else 5}" '
        f'class="{"maj" if i % 5 == 0 else "min"}" transform="rotate({i * 6} 32 32)"/>' for i in range(60))
    return (f'<svg class="gauge {tier}" viewBox="0 0 64 64" role="img" aria-label="Score {score} out of 100">'
            f'<g class="ticks">{ticks}</g>'
            f'<circle class="track" cx="32" cy="32" r="{r}"/>'
            f'<circle class="arc" cx="32" cy="32" r="{r}" stroke-dasharray="{c * score / 100:.1f} {c:.1f}" '
            f'transform="rotate(-90 32 32)"/>'
            f'<text x="32" y="38">{score}</text></svg>')


WATCH_GLYPH = ('<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M18 4h12l1.5 7h-15z"/><path d="M18 44h12l1.5-7h-15z"/>'
               '<circle cx="24" cy="24" r="12.5"/><path d="M24 16.5V24l4.5 3"/><path d="M37 22.5h2v3h-2"/></svg>')

def basis_short(d: "Deal") -> str:
    b = d.resale_basis
    m = re.match(r"Based on (\d+) similar", b)
    out = f"from {m.group(1)} similar for sale" if m else "brand average"
    if "(ref " in b:
        out = out.replace("similar", "same ref")
    if "adjusted by your" in b:
        out += ", tuned to your sales"
    return out


_NUM_WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"]


def write_html(deals: list[Deal], path: Path, demo: bool, scanned: int, app_name: str = "Tickover") -> None:
    e = html.escape

    def links_html(pairs):
        return "".join(f'<a href="{e(u)}" target="_blank" rel="noopener">{e(t)}</a>' for t, u in pairs)

    sources = sorted({d.listing.source for d in deals})
    cards = []
    for n_card, d in enumerate(deals):
        l = d.listing
        maker = BRAND_NAMES.get(d.brand, d.brand.title()) or "Unknown maker"
        chips = "".join(f'<span class="chip">{e(x)}</span>' for x in d.faults)
        chips += "".join(f'<span class="chip warn">{e(x)}</span>' for x in d.flags)
        notes = "".join(f"<li>{e(n)}</li>" for n in d.notes)
        if l.buying == "Auction":
            sale = f'Auction, {l.bids} bid{"s" if l.bids != 1 else ""}'
            left = _ends_in(l.ends)
            if left:
                sale += f", ends in {left}" if left != "ended" else ", ended"
        else:
            sale = "Buy It Now"
        meta = [f'<span class="src">{e(l.source)}</span>']
        meta += [f"<span>{e(x)}</span>" for x in (d.movement if d.movement != "Unknown" else "", sale) if x]
        thumb = (f'<img src="{e(l.image)}" alt="" loading="lazy">' if l.image else WATCH_GLYPH)
        buy = f"£{l.price + l.shipping:,.0f}" + (f'<small>{e(l.orig_price)} plus post</small>' if l.orig_price
                                                  else '<small>including post</small>')
        figs = [("Purchase", buy)]
        if d.import_cost:
            figs.append(("Import", f"£{d.import_cost:,.0f}<small>VAT and fees</small>"))
        figs += [("Repairs", f"£{d.repair_cost:,.0f}<small>parts and labour</small>"),
                 ("Resale", f"£{d.resale:,.0f}<small>{e(basis_short(d))}</small>"),
                 ("Return", f"{d.roi:.0f}%<small>on money in</small>")]
        figs_html = "".join(f"<div><dt>{a}</dt><dd>{b}</dd></div>" for a, b in figs)
        seller = (f'Sold by {e(l.seller)}, {l.feedback_pct:.1f}% positive from {l.feedback_score:,} ratings'
                  if l.source.startswith("eBay") else "")

        need = ""
        if d.checklist:
            rows = []
            for n, it in enumerate(d.checklist):
                cost = f"£{it['cost']:,.0f}" if it["cost"] else "not counted"
                hard = '<span class="hard">Hard to find</span>' if it["hard"] else ""
                note = f'<p class="inote">{e(it["note"])}</p>' if it["note"] else ""
                lk = f'<p class="ilinks">{links_html(it["links"])}</p>' if it["links"] else ""
                rows.append(
                    f'<li><label><input type="checkbox" data-k="{e(l.item_id)}:{n}">'
                    f'<span class="iname">{e(it["item"])}</span></label>'
                    f'<span class="icost{" nil" if not it["cost"] else ""}">{cost}</span>{hard}{note}{lk}</li>')
            tl = "".join(f'<li><span>{e(t["item"])}</span><span class="ilinks">{links_html(t["links"])}</span></li>'
                         for t in d.tools)
            tools_html = (f'<div class="tools"><h4>Tools you buy once</h4><ul>{tl}</ul></div>' if tl else "")
            need = (f'<details class="need"><summary><svg class="chev" viewBox="0 0 14 14" aria-hidden="true">'
                    f'<path d="M5 2.5 9.5 7 5 11.5"/></svg><span>What you\'ll need</span>'
                    f'<span class="sumcost">£{d.repair_cost:,.0f}</span></summary>'
                    f'<ul class="check">{"".join(rows)}</ul>{tools_html}</details>')

        cards.append(f"""
<article class="deal" style="--i:{min(n_card, 8)}" data-score="{d.score}" data-profit="{d.profit}" data-roi="{d.roi}"
  data-diff="{d.difficulty}" data-mov="{e(d.movement)}" data-src="{e(l.source)}" data-text="{e((l.title + ' ' + d.brand).lower())}">
  <div class="head">
    <a class="thumb" href="{e(l.url)}" target="_blank" rel="noopener" tabindex="-1">{thumb}</a>
    <div class="what">
      <p class="maker">{e(maker)}</p>
      <a class="title" href="{e(l.url)}" target="_blank" rel="noopener">{e(l.title)}</a>
      <p class="meta">{''.join(meta)}</p>
    </div>
    <div class="verdict">{_gauge(d.score)}
      <p class="profit"><span>Estimated profit</span><b>£{d.profit:,.0f}</b></p></div>
  </div>
  {f'<div class="chips">{chips}</div>' if chips else ''}
  <dl class="figs">{figs_html}</dl>
  {f'<ul class="notes">{notes}</ul>' if notes else ''}
  {need}
  {f'<p class="seller">{seller}</p>' if seller else ''}
</article>""")

    now = datetime.now()
    stamp = f"{now.day} {now.strftime('%B')}, {now.strftime('%H:%M')}"
    if deals:
        best = max(d.profit for d in deals)
        word = _NUM_WORDS[len(deals)] if len(deals) < len(_NUM_WORDS) else f"{len(deals)}"
        lede = (f"{word} watch{'es' if len(deals) != 1 else ''} worth your time, chosen from {scanned:,} listings.")
        sub = f"The strongest could return about £{best:,.0f} once restored."
    else:
        lede = "Nothing worth your time today."
        sub = f"{scanned:,} listings checked. Tickover will keep looking."
    banner = ('<p class="demo">You are viewing sample listings. Add your eBay keys to config.toml '
              'to see real ones.</p>') if demo else ""
    src_opts = "".join(f"<option>{e(s)}</option>" for s in sources)
    empty = ('<div class="empty"><p>No watches cleared your profit and return targets.</p>'
             '<p>Lower <code>min_profit</code> or <code>min_roi_percent</code> in config.toml, '
             'or add more searches, then run it again.</p></div>')

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(app_name)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400..700;1,8..60,400..600&family=Instrument+Sans:wght@400..700&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:#ECEEF1; --plate:#FFFFFF; --well:#F4F5F7; --ink:#0D121A; --mute:#3B434F; --faint:#555E6B; --rule:#CBD0D7;
  --gold:#7A5A1E; --gold-soft:rgba(122,90,30,.16); --lume:#1F5E47; --signal:#962F25; --signal-bg:#F6E3DF;
  --serif:"Source Serif 4",Georgia,"Times New Roman",serif; --sans:"Instrument Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  color-scheme: light;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#0B0F16; --plate:#161C27; --well:#10151E; --ink:#F4F0E8; --mute:#BCC3CD; --faint:#9AA2AE; --rule:#2E3747;
  --gold:#DDBD78; --gold-soft:rgba(221,189,120,.18); --lume:#86D6B4; --signal:#F2A79C; --signal-bg:#3A1C20;
  color-scheme: dark; }} }}
:root[data-theme="dark"] {{ --bg:#0B0F16; --plate:#161C27; --well:#10151E; --ink:#F4F0E8; --mute:#BCC3CD; --faint:#9AA2AE;
  --rule:#2E3747; --gold:#DDBD78; --gold-soft:rgba(221,189,120,.18); --lume:#86D6B4; --signal:#F2A79C; --signal-bg:#3A1C20;
  color-scheme: dark; }}
* {{ box-sizing:border-box }}
html {{ -webkit-text-size-adjust:100% }}
body {{ margin:0; background:var(--bg); color:var(--ink); font-family:var(--sans); font-size:16px; line-height:1.55;
  -webkit-font-smoothing:antialiased }}
a {{ color:inherit }}
:focus-visible {{ outline:1.5px solid var(--gold); outline-offset:3px; border-radius:2px }}
.wrap {{ max-width:880px; margin:0 auto; padding:0 20px }}

/* masthead */
.top {{ display:flex; align-items:center; justify-content:space-between; padding-top:26px; padding-bottom:22px;
  border-bottom:1px solid var(--rule) }}
.mark {{ display:flex; align-items:center; gap:12px; text-decoration:none }}
.mark svg {{ width:30px; height:30px }}
.mark svg circle {{ fill:none; stroke:var(--gold); stroke-width:1.2 }}
.mark svg line {{ stroke:var(--ink); stroke-width:1.6; stroke-linecap:round }}
.mark svg .pin {{ fill:var(--gold); stroke:none }}
.mark span {{ font-family:var(--serif); font-size:25px; font-weight:600; letter-spacing:.02em }}
.stamp {{ font-size:13.5px; color:var(--mute); text-align:right; line-height:1.4 }}
.stamp b {{ display:block; font-weight:500; color:var(--ink) }}

/* hero: the guilloché dial is the one statement on the page */
.hero {{ position:relative; overflow:hidden }}
.hero .wrap {{ position:relative; padding-top:64px; padding-bottom:56px }}
.dial {{ position:absolute; right:-110px; top:50%; width:330px; height:330px; transform:translateY(-50%);
  border-radius:50%; pointer-events:none; opacity:.75;
  background:
    radial-gradient(circle at 50% 50%, var(--bg) 0 8%, transparent 8.4%),
    repeating-conic-gradient(from 0deg, var(--gold-soft) 0deg 1.2deg, transparent 1.2deg 3deg),
    repeating-radial-gradient(circle at 50% 50%, transparent 0 7px, var(--gold-soft) 7px 8px);
  -webkit-mask: radial-gradient(circle closest-side, #000 0 82%, transparent 100%); mask: radial-gradient(circle closest-side, #000 0 82%, transparent 100%) }}
.dial::after {{ content:""; position:absolute; inset:0; border-radius:50%; box-shadow:inset 0 0 0 1px var(--gold-soft) }}
h1 {{ position:relative; margin:0; max-width:15em; font-family:var(--serif); font-weight:600; font-size:42px;
  line-height:1.15; letter-spacing:-.01em }}
.sub {{ position:relative; margin:18px 0 0; max-width:34em; font-size:17px; color:var(--mute) }}
.demo {{ position:relative; display:inline-block; margin:26px 0 0; padding:9px 14px; font-size:14px; color:var(--mute);
  border:1px solid var(--rule); border-radius:999px; background:var(--plate) }}
.demo::before {{ content:""; display:inline-block; width:6px; height:6px; border-radius:50%; background:var(--gold);
  margin-right:9px; vertical-align:2px }}

/* controls */
.bar {{ position:sticky; top:0; z-index:5; background:color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter:saturate(1.3) blur(14px); -webkit-backdrop-filter:saturate(1.3) blur(14px);
  border-top:1px solid var(--rule); border-bottom:1px solid var(--rule) }}
.bar .wrap {{ display:grid; grid-template-columns:1fr repeat(3, auto); gap:10px; padding-top:12px; padding-bottom:12px }}
.bar input,.bar select {{ font:inherit; font-size:15px; height:40px; padding:0 14px; border-radius:999px;
  border:1px solid var(--rule); background:var(--plate); color:var(--ink); min-width:0 }}
.bar input::placeholder {{ color:var(--faint) }}
.bar select {{ padding-right:32px; appearance:none; -webkit-appearance:none; cursor:pointer;
  background-image:linear-gradient(45deg,transparent 50%,var(--gold) 50%),linear-gradient(135deg,var(--gold) 50%,transparent 50%);
  background-position:calc(100% - 17px) 17px, calc(100% - 12px) 17px; background-size:5px 5px; background-repeat:no-repeat }}
@media (max-width:660px) {{ .bar .wrap {{ grid-template-columns:repeat(3, 1fr); gap:8px }} .bar input {{ grid-column:1 / -1 }}
  .bar select {{ font-size:14px; padding-left:12px; padding-right:26px; background-position:calc(100% - 15px) 17px, calc(100% - 10px) 17px }} }}

/* deals */
main.wrap {{ padding-top:28px; padding-bottom:72px; display:grid; gap:20px }}
.deal {{ background:var(--plate); border:1px solid var(--rule); border-radius:22px; padding:26px 26px 22px;
  animation:rise .6s cubic-bezier(.2,.7,.2,1) both; animation-delay:calc(var(--i) * 60ms) }}
@keyframes rise {{ from {{ opacity:0; transform:translateY(10px) }} to {{ opacity:1; transform:none }} }}
.head {{ display:grid; grid-template-columns:84px 1fr auto; gap:22px; align-items:start }}
.thumb {{ width:84px; height:84px; border-radius:50%; overflow:hidden; background:var(--well);
  display:grid; place-items:center; border:1px solid var(--rule); box-shadow:inset 0 0 0 5px var(--plate) }}
.thumb img {{ width:100%; height:100%; object-fit:cover; display:block }}
.thumb svg {{ width:34px; height:34px; fill:none; stroke:var(--gold); stroke-width:1.4; stroke-linejoin:round; stroke-linecap:round }}
.maker {{ margin:2px 0 4px; font-family:var(--serif); font-style:italic; font-weight:600; font-size:16px; color:var(--gold) }}
.title {{ display:block; font-family:var(--serif); font-size:21px; line-height:1.3; font-weight:600; text-decoration:none;
  color:var(--ink) }}
.title:hover {{ text-decoration:underline; text-decoration-thickness:1px; text-underline-offset:4px;
  text-decoration-color:var(--gold) }}
.meta {{ margin:10px 0 0; display:flex; flex-wrap:wrap; gap:4px 14px; font-size:14px; color:var(--mute) }}
.src {{ color:var(--ink); font-weight:600 }}
.verdict {{ display:flex; flex-direction:column; align-items:flex-end; gap:10px; min-width:96px }}
.gauge {{ width:64px; height:64px }}
.gauge .ticks .min {{ stroke:var(--faint); stroke-width:.7; opacity:.6 }}
.gauge .ticks .maj {{ stroke:var(--mute); stroke-width:1.2 }}
.gauge .track {{ fill:none; stroke:var(--rule); stroke-width:1.2 }}
.gauge .arc {{ fill:none; stroke-width:3; stroke-linecap:round }}
.gauge text {{ font:700 19px var(--serif); text-anchor:middle; fill:var(--ink); font-variant-numeric:lining-nums }}
.gauge.hot .arc {{ stroke:var(--lume) }} .gauge.good .arc {{ stroke:var(--gold) }} .gauge.low .arc {{ stroke:var(--mute) }}
.profit {{ margin:0; text-align:right; line-height:1.15 }}
.profit span {{ display:block; font-size:13px; color:var(--mute) }}
.profit b {{ display:block; font-family:var(--serif); font-size:31px; font-weight:700; color:var(--lume);
  font-variant-numeric:lining-nums tabular-nums }}
.chips {{ margin:20px 0 0; display:flex; flex-wrap:wrap; gap:8px }}
.chip {{ font-size:13.5px; font-weight:500; padding:4px 12px; border-radius:999px; border:1px solid var(--faint); color:var(--ink) }}
.chip.warn {{ border-color:transparent; background:var(--signal-bg); color:var(--signal) }}
.figs {{ margin:22px 0 0; padding:18px 0 0; border-top:1px solid var(--rule);
  display:grid; grid-template-columns:repeat(auto-fit, minmax(96px, 1fr)); gap:14px 18px }}
.figs dt {{ font-size:13.5px; font-weight:500; color:var(--mute) }}
.figs dd {{ margin:4px 0 0; font-family:var(--serif); font-size:23px; font-weight:700;
  font-variant-numeric:lining-nums tabular-nums }}
.figs small {{ display:block; margin-top:2px; font-family:var(--sans); font-size:12.5px; font-weight:400; color:var(--faint) }}
.notes {{ margin:18px 0 0; padding:0; list-style:none; font-size:14.5px; color:var(--mute) }}
.notes li {{ position:relative; padding-left:18px }}
.notes li + li {{ margin-top:4px }}
.notes li::before {{ content:""; position:absolute; left:2px; top:.62em; width:6px; height:6px; border-radius:50%;
  border:1px solid var(--gold) }}
.seller {{ margin:16px 0 0; font-size:13.5px; color:var(--faint) }}

/* checklist */
.need {{ margin-top:20px; border-radius:16px; background:var(--well); border:1px solid var(--rule) }}
.need summary {{ list-style:none; cursor:pointer; display:flex; align-items:center; gap:12px; padding:14px 18px;
  font-weight:600; font-size:15.5px }}
.need summary::-webkit-details-marker {{ display:none }}
.chev {{ width:14px; height:14px; flex:0 0 auto; fill:none; stroke:var(--gold); stroke-width:1.8;
  stroke-linecap:round; stroke-linejoin:round; transition:transform .2s ease }}
.need[open] .chev {{ transform:rotate(90deg) }}
.sumcost {{ margin-left:auto; font-family:var(--serif); font-weight:700; font-size:18px; color:var(--ink); font-variant-numeric:lining-nums }}
.check {{ list-style:none; margin:0; padding:0 18px 6px }}
.check li {{ display:grid; grid-template-columns:1fr auto; column-gap:12px; padding:12px 0; border-top:1px solid var(--rule) }}
.check label {{ display:flex; gap:12px; align-items:flex-start; cursor:pointer; font-size:15.5px }}
.check input {{ width:17px; height:17px; margin:3px 0 0; flex:0 0 auto; accent-color:var(--gold) }}
.check input:checked + .iname {{ text-decoration:line-through; text-decoration-color:var(--gold); color:var(--mute) }}
.icost {{ font-family:var(--serif); font-size:17px; font-weight:700; font-variant-numeric:lining-nums tabular-nums }}
.icost.nil {{ font-family:var(--sans); font-weight:400; font-size:12.5px; color:var(--faint) }}
.hard {{ grid-column:1; justify-self:start; margin:6px 0 0 29px; font-size:11.5px; font-weight:500; color:var(--signal);
  background:var(--signal-bg); border-radius:999px; padding:1px 9px }}
.inote,.ilinks {{ grid-column:1 / -1; margin:5px 0 0 29px; font-size:14px; color:var(--mute) }}
.ilinks {{ display:flex; gap:16px }}
.ilinks a {{ color:var(--ink); font-weight:500; text-decoration:underline; text-decoration-color:var(--gold);
  text-underline-offset:3px }}
.tools {{ padding:6px 18px 18px; border-top:1px solid var(--rule) }}
.tools h4 {{ margin:12px 0 8px; font-family:var(--serif); font-style:italic; font-weight:600; font-size:16px; color:var(--gold) }}
.tools ul {{ list-style:none; margin:0; padding:0; display:grid; gap:7px; font-size:14.5px }}
.tools li {{ display:flex; flex-wrap:wrap; gap:4px 14px; justify-content:space-between }}
.tools .ilinks {{ margin:0 }}

.empty {{ background:var(--plate); border:1px solid var(--rule); border-radius:22px; padding:40px 24px; text-align:center }}
.empty p {{ margin:0 auto; max-width:30em }} .empty p:first-child {{ font-family:var(--serif); font-size:20px }}
.empty p + p {{ margin-top:10px; color:var(--mute); font-size:14px }}
code {{ font-size:.9em; background:var(--well); padding:1px 6px; border-radius:4px }}
footer.wrap {{ padding-bottom:48px; font-size:13.5px; color:var(--faint); text-align:center }}
footer .orn {{ display:block; margin:0 auto 14px; width:40px; height:1px; background:var(--gold) }}

@media (max-width:660px) {{
  .wrap {{ padding:0 16px }}
  .top {{ padding-top:20px; padding-bottom:18px }}
  .hero .wrap {{ padding-top:40px; padding-bottom:40px }}
  .dial {{ width:300px; height:300px; right:-130px }}
  h1 {{ font-size:31px }} .sub {{ font-size:16px }}
  .deal {{ padding:20px 18px 18px; border-radius:18px }}
  .head {{ grid-template-columns:60px 1fr; gap:14px }}
  .thumb {{ width:60px; height:60px; box-shadow:inset 0 0 0 3px var(--plate) }}
  .thumb svg {{ width:26px; height:26px }}
  .title {{ font-size:19px }}
  .verdict {{ grid-column:1 / -1; flex-direction:row; align-items:center; justify-content:space-between;
    padding-top:14px; border-top:1px solid var(--rule); min-width:0 }}
  .gauge {{ width:56px; height:56px }}
  .figs {{ grid-template-columns:repeat(2, 1fr); gap:16px 12px }}
  .figs dd {{ font-size:21px }}
}}
@media (prefers-reduced-motion: reduce) {{ * {{ animation:none !important; transition:none !important }} }}
</style></head><body>
<header class="wrap top">
  <a class="mark" href="#" aria-label="{e(app_name)}">
    <svg viewBox="0 0 30 30" aria-hidden="true"><circle cx="15" cy="15" r="13.5"/>
      <line x1="15" y1="15" x2="15" y2="6.5"/><line x1="15" y1="15" x2="20.5" y2="18"/><circle class="pin" cx="15" cy="15" r="1.6"/></svg>
    <span>{e(app_name)}</span></a>
  <p class="stamp"><b>Updated</b>{e(stamp)}</p>
</header>
<section class="hero"><div class="wrap">
  <div class="dial" aria-hidden="true"></div>
  <h1>{e(lede)}</h1>
  <p class="sub">{e(sub)}</p>
  {banner}
</div></section>
<div class="bar"><div class="wrap">
  <input type="search" id="q" placeholder="Search maker or model" aria-label="Search maker or model">
  <select id="sort" aria-label="Sort"><option value="score">Top picks</option><option value="profit">Most profit</option>
    <option value="roi">Best return</option><option value="diff">Easiest fix</option></select>
  <select id="src" aria-label="Site"><option value="">All sites</option>{src_opts}</select>
  <select id="mov" aria-label="Movement"><option value="">All types</option><option>Quartz</option>
    <option>Automatic / Mechanical</option><option value="Unknown">Not stated</option></select>
</div></div>
<main class="wrap" id="list">{''.join(cards) or empty}</main>
<footer class="wrap"><span class="orn"></span>Figures are estimates from the listing title. Check the photos, description and recent sold prices before you buy.</footer>
<script>
const list=document.getElementById('list'), cards=[...list.querySelectorAll('.deal')];
function apply(){{
  const q=document.getElementById('q').value.toLowerCase().trim(), s=document.getElementById('sort').value,
        m=document.getElementById('mov').value, src=document.getElementById('src').value;
  cards.sort((a,b)=> s==='diff' ? (+a.dataset.diff - +b.dataset.diff) || (+b.dataset.score - +a.dataset.score)
                                : (+b.dataset[s]) - (+a.dataset[s]));
  cards.forEach(c=>{{ c.hidden = !((!q||c.dataset.text.includes(q)) && (!m||c.dataset.mov===m) && (!src||c.dataset.src===src));
    c.style.animation='none'; list.appendChild(c); }});
}}
['q','sort','mov','src'].forEach(id=>document.getElementById(id).addEventListener('input',apply));
let done={{}}; try{{ done=JSON.parse(localStorage.getItem('tickover-done')||'{{}}') }}catch(e){{}}
document.querySelectorAll('.check input').forEach(cb=>{{ cb.checked=!!done[cb.dataset.k];
  cb.addEventListener('change',()=>{{ done[cb.dataset.k]=cb.checked;
    try{{ localStorage.setItem('tickover-done',JSON.stringify(done)) }}catch(e){{}} }}); }});
</script></body></html>"""
    path.write_text(page, encoding="utf-8")


def send_alerts(deals: list[Deal], cfg: dict, seen_path: Path) -> int:
    topic = (os.environ.get("NTFY_TOPIC") or cfg["alerts"].get("ntfy_topic", "")).strip()
    if not topic:
        return 0
    try:
        seen = set(json.loads(seen_path.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        seen = set()
    sent = 0
    for d in deals:
        l = d.listing
        if d.score < cfg["alerts"].get("alert_min_score", 60) or l.item_id in seen:
            continue
        msg = (f"{l.source}: £{l.price + l.shipping:.0f} → est. £{d.resale:.0f} resale, ~£{d.profit:.0f} profit\n"
               f"{', '.join(d.faults) or 'Fault not stated'}")
        req = urllib.request.Request(f"https://ntfy.sh/{urllib.parse.quote(topic)}", data=msg.encode(), headers={
            "Title": f"[{d.score}] {l.title[:80]}".encode("ascii", "ignore").decode(),
            "Click": l.url, "Tags": "watch", "Priority": "high" if d.score >= 85 else "default",
        })
        try:
            urllib.request.urlopen(req, timeout=15).close()
            seen.add(l.item_id)
            sent += 1
        except urllib.error.URLError as ex:
            print(f"  ! alert failed: {ex}")
    seen_path.write_text(json.dumps(sorted(seen)))
    return sent


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────
def collect(cfg: dict, demo: bool) -> list[Listing]:
    if demo:
        data = json.loads((HERE / "sample_listings.json").read_text(encoding="utf-8"))
        return [Listing(**x) for x in data]

    fx = cfg.get("fx", {})
    s = cfg["search"]
    found: dict[str, Listing] = {}

    # eBay — every marketplace you've switched on
    ebay = EbayClient(cfg["ebay"], fx)
    for market in cfg["ebay"].get("marketplaces", ["EBAY_GB"]):
        if market not in MARKET_INFO:
            print(f"  ! unknown marketplace {market} — skipping")
            continue
        queries = s.get(f"queries_{MARKET_INFO[market][0].lower()}", s["queries"]) if market != "EBAY_GB" else s["queries"]
        jobs = [(q, False) for q in queries]
        if s.get("include_parts_condition", True):
            jobs.append((None, True))
        for q, parts in jobs:
            print(f"  {MARKET_INFO[market][1]}: {q or '[all faulty wristwatches]'}")
            for l in ebay.search(market, q, s["min_price"], s["max_price"], parts_only=parts):
                found.setdefault(l.item_id, l)
            time.sleep(0.4)

    # Etsy — only if keys are set
    etsy_cfg = cfg.get("etsy", {})
    if etsy_cfg.get("enabled", False):
        try:
            etsy = EtsyClient(etsy_cfg, fx)
        except ValueError:
            print("  ! Etsy is switched on but has no keys in config.toml — skipping")
        else:
            for q in etsy_cfg.get("queries", []):
                print(f"  Etsy: {q}")
                for l in etsy.search(q, s["min_price"], s["max_price"]):
                    found.setdefault(l.item_id, l)
                time.sleep(0.3)
    return list(found.values())


def check_one(cfg: dict, title: str, price: float, postage: float, abroad: bool) -> None:
    l = Listing(item_id="check", title=title, price=price, shipping=postage, url="",
                source="Manual check", country="XX" if abroad else "GB")
    d = analyse(l, cfg)
    print(f"\n  {title}")
    print(f"  Brand: {d.brand.title() or 'not recognised — add it to [brands] in config.toml'}"
          f"   Movement: {d.movement}")
    print(f"  Faults: {', '.join(d.faults) or 'none spotted in the title'}")
    if d.flags:
        print(f"  ⚠ Warnings: {', '.join(d.flags)}")
    print(f"\n  Buy £{price + postage:,.0f}" + (f" + import £{d.import_cost:,.0f}" if d.import_cost else "")
          + f" + fix ~£{d.repair_cost:,.0f} = £{d.total_cost:,.0f}")
    print(f"  Resale ~£{d.resale:,.0f} → profit ~£{d.profit:,.0f} ({d.roi:.0f}% ROI)")
    for n in d.notes:
        print(f"  • {n}")
    if d.checklist:
        print("\n  What you'll need:")
        for it in d.checklist:
            cost = f"£{it['cost']:.0f}" if it["cost"] else "  —"
            print(f"   [ ] {it['item']:<58} {cost:>5}" + ("  (hard to find)" if it["hard"] else ""))
            if it["note"]:
                print(f"       {it['note']}")
            for t, u in it["links"][:1]:
                print(f"       {u}")
        if d.tools:
            print("  Tools (buy once): " + ", ".join(t["item"] for t in d.tools))
    m = cfg["money"]
    if d.flags or not d.brand:
        verdict = "Pass" if d.flags else "Can't value it — brand not recognised"
    elif d.profit >= m["min_profit"] and d.roi >= m["min_roi_percent"]:
        verdict = ("Strong buy" if d.score >= 60 else "Worth buying if the photos check out" if d.score >= 40
                   else "Possible, but a harder or riskier fix — only if you're confident")
    elif d.profit > 0:
        verdict = "Thin margin — only if you can get it cheaper"
    else:
        verdict = "Pass — it would lose money"
    print(f"\n  Score {d.score}/100 — {verdict}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Find repairable watches worth flipping.")
    ap.add_argument("--demo", action="store_true", help="use sample listings instead of searching")
    ap.add_argument("--config", default=str(HERE / "config.toml"))
    ap.add_argument("--all", action="store_true", help="show everything, ignore profit/ROI thresholds")
    ap.add_argument("--check", metavar="TITLE", help="score one listing from any website")
    ap.add_argument("--price", type=float, help="asking price in £ (with --check)")
    ap.add_argument("--postage", type=float, default=0, help="postage in £ (with --check)")
    ap.add_argument("--abroad", action="store_true", help="item ships from outside the UK (with --check)")
    args = ap.parse_args()

    cfg = tomllib.loads(Path(args.config).read_text(encoding="utf-8"))

    if args.check:
        if args.price is None:
            sys.exit('Add the price, e.g.  --check "Seiko 5 automatic not running" --price 25')
        check_one(cfg, args.check, args.price, args.postage, args.abroad)
        return

    app_name = cfg.get("app", {}).get("name", "Tickover")
    print(app_name + (" (demo)" if args.demo else ""))
    listings = collect(cfg, args.demo)
    print(f"  {len(listings)} listings found, analysing…")

    m = cfg["money"]
    deals = [analyse(l, cfg) for l in listings]
    if not args.demo:
        client = EbayClient(cfg["ebay"], cfg.get("fx", {}))
        pre = sorted([d for d in deals if d.score > 0 and not d.flags], key=lambda d: d.score, reverse=True)
        det = cfg.get("details", {})
        if det.get("enabled", True):
            top = pre[: int(det.get("max_items", 40))]
            print(f"  reading the full listing for the top {len(top)}…")
            idx = {id(d): i for i, d in enumerate(deals)}
            for d in top:
                if client.get_details(d.listing):
                    deals[idx[id(d)]] = analyse(d.listing, cfg)
                time.sleep(0.2)
            pre = sorted([d for d in deals if d.score > 0 and not d.flags], key=lambda d: d.score, reverse=True)
        print(f"  checking resale prices against similar working watches…")
        n = market_check(pre, cfg, client)
        print(f"  {n} price checks done")
    cal, summary = load_flips(HERE / "flips.csv", cfg["brands"])
    if cal:
        apply_calibration(deals, cal, cfg)
    if summary.get("sold"):
        print(f"  your flips: {summary['sold']} sold, £{summary['profit']:,.0f} profit, {summary['holding']} still to sell")
    if not args.all:
        deals = [d for d in deals if d.score > 0 and d.profit >= m["min_profit"]
                 and d.roi >= m["min_roi_percent"] and not d.flags
                 and d.score >= m.get("min_score", 0)
                 and (not m.get("max_buy_price") or d.listing.price + d.listing.shipping <= m["max_buy_price"])]
    deals.sort(key=lambda d: (d.score, d.profit), reverse=True)
    if not args.all and m.get("max_results"):
        deals = deals[: int(m["max_results"])]

    write_html(deals, HERE / "report.html", args.demo, len(listings), app_name)
    write_csv(deals, HERE / "deals.csv")
    sent = 0 if args.demo else send_alerts(deals, cfg, HERE / "seen.json")

    print(f"  {len(deals)} worthwhile deals → report.html, deals.csv"
          + (f", {sent} phone alert(s) sent" if sent else ""))
    for d in deals[:5]:
        print(f"   [{d.score:>3}] £{d.profit:>6.0f}  {d.listing.source:<8} {d.listing.title[:62]}")


if __name__ == "__main__":
    main()
