#!/usr/bin/env python3
"""
Amazon Creators API enrichment for Halcott Magazine.

Reads every product in data/products/*.json, looks up each ASIN via the Amazon
Creators API, and writes a cache file per ASIN to data/enrichment/<ASIN>.json with
the official image URL, current price, and a cleaned-up title.

Credentials come ONLY from environment variables (GitHub Actions secrets / vars):
    AMAZON_ACCESS_KEY      -> Creators API credential id   (your "Credential")
    AMAZON_SECRET_KEY      -> Creators API credential secret (your "Secret")
    AMAZON_API_VERSION     -> the version Amazon issued (2.1/2.2/2.3/3.1/3.2/3.3)
    AMAZON_PARTNER_TAG     -> affiliate tag (default halcottmag-20)
    AMAZON_COUNTRY         -> marketplace (default US)

Set PAAPI_PROBE=1 to run in probe mode: fetch a small sample, print the results,
write nothing. Used to confirm the API is active and inspect the response shape.

This script never prints credential values.
"""
import os
import re
import sys
import json
import glob
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS_DIR = ROOT / "data" / "products"
ENRICH_DIR = ROOT / "data" / "enrichment"

PROBE = os.environ.get("PAAPI_PROBE", "").strip() in ("1", "true", "yes")
CRED_ID = os.environ.get("AMAZON_ACCESS_KEY", "").strip()
CRED_SECRET = os.environ.get("AMAZON_SECRET_KEY", "").strip()
VERSION = os.environ.get("AMAZON_API_VERSION", "").strip()
PARTNER_TAG = os.environ.get("AMAZON_PARTNER_TAG", "halcottmag-20").strip()
COUNTRY = (os.environ.get("AMAZON_COUNTRY", "US").strip() or "US").upper()
THROTTLE_SECONDS = float(os.environ.get("PAAPI_THROTTLE", "1.5"))


def die(msg, code=1):
    print(msg)
    sys.exit(code)


def load_products():
    out = []
    for path in sorted(glob.glob(str(PRODUCTS_DIR / "*.json"))):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ! could not read {path}: {e}")
            continue
        if not isinstance(data, list):
            continue
        for p in data:
            if isinstance(p, dict):
                asin = ((p.get("network_ids") or {}).get("amazon") or "").strip()
                if asin:
                    out.append((asin, p))
    return out


# ---------------- title cleaning ----------------
_FILLER = re.compile(
    r"\b(brand new|latest model|newest version|official|genuine|us version|"
    r"amazon renewed|renewed|factory sealed|free shipping)\b",
    re.I,
)
_STOP = {"with", "for", "and", "the", "a", "an", "to", "in", "by", "of", "plus", "w"}


def clean_title(raw):
    if not raw:
        return ""
    t = " ".join(str(raw).split())
    for sep in (" - ", " – ", " — ", " | ", " ｜ "):
        i = t.find(sep)
        if i > 15:
            t = t[:i]
            break
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]\s*$", "", t).strip()
    if len(t) > 60 and "," in t:
        head = t.split(",", 1)[0].strip()
        if len(head) >= 20:
            t = head
    t = " ".join(_FILLER.sub("", t).split())
    if len(t) > 70:
        t = t[:70].rsplit(" ", 1)[0].strip()
    words = t.strip(" ,-–—|").split()
    while words and words[-1].lower().strip(",") in _STOP:
        words.pop()
    return " ".join(words).strip(" ,-–—|")


# ---------------- response extraction (defensive) ----------------
def _get(obj, *path):
    cur = obj
    for key in path:
        if cur is None:
            return None
        cur = getattr(cur, key, None)
    return cur


def extract(item):
    asin = getattr(item, "asin", None)
    image = _get(item, "images", "primary", "large", "url") or _get(item, "images", "primary", "medium", "url")
    title = _get(item, "item_info", "title", "display_value")
    price_amount = None
    price_display = None
    o2 = getattr(item, "offers_v2", None)
    listings = getattr(o2, "listings", None) if o2 else None
    if listings:
        money = _get(listings[0], "price", "money")
        if money is not None:
            price_amount = getattr(money, "amount", None)
            disp = getattr(money, "display_amount", None) or getattr(money, "display", None)
            if disp:
                price_display = disp
            elif price_amount is not None:
                price_display = f"${price_amount}"
    return {
        "asin": asin,
        "image": image,
        "title_raw": title,
        "title_clean": clean_title(title),
        "price_usd": price_amount,
        "price_display": price_display,
    }


def main():
    if not CRED_ID or not CRED_SECRET:
        die("FAILURE: AMAZON_ACCESS_KEY / AMAZON_SECRET_KEY not set in environment.")
    if not VERSION:
        die("FAILURE: AMAZON_API_VERSION not set. Amazon issued this with your credentials "
            "(one of 2.1, 2.2, 2.3, 3.1, 3.2, 3.3).")
    print(f"Partner tag: {PARTNER_TAG} | country: {COUNTRY} | version: {VERSION} | probe: {PROBE}")

    try:
        from amazon_creatorsapi import AmazonCreatorsApi, Country
    except Exception as e:
        die(f"FAILURE: could not import amazon_creatorsapi ({e}). Is python-amazon-paapi installed?")

    country = getattr(Country, COUNTRY, None)
    if country is None:
        die(f"FAILURE: unsupported country '{COUNTRY}'.")

    products = load_products()
    asins = []
    seen = set()
    for asin, _ in products:
        if asin not in seen:
            seen.add(asin)
            asins.append(asin)
    print(f"Found {len(asins)} unique ASIN(s) across {len(products)} product entries.")
    if not asins:
        die("FAILURE: no ASINs found in data/products/.")

    if PROBE:
        asins = asins[:6]
        print(f"PROBE MODE: testing {len(asins)} ASIN(s): {', '.join(asins)}")

    try:
        api = AmazonCreatorsApi(
            credential_id=CRED_ID,
            credential_secret=CRED_SECRET,
            version=VERSION,
            tag=PARTNER_TAG,
            country=country,
            throttling=THROTTLE_SECONDS,
        )
    except Exception as e:
        die(f"FAILURE: could not initialize AmazonCreatorsApi ({type(e).__name__}: {e}).")

    try:
        items = api.get_items(asins)  # library auto-splits into batches of 10
    except Exception as e:
        die(f"FAILURE: get_items errored ({type(e).__name__}: {e}). "
            "If this mentions credentials/version/eligibility, that's the cause.")

    ENRICH_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    fail = 0
    for item in (items or []):
        rec = extract(item)
        if not rec.get("asin"):
            fail += 1
            continue
        ok += 1
        if PROBE:
            print(f"  ✓ {rec['asin']}")
            print(f"      title_raw  : {rec['title_raw']}")
            print(f"      title_clean: {rec['title_clean']}")
            print(f"      price      : {rec['price_display']} ({rec['price_usd']})")
            print(f"      image      : {rec['image']}")
        else:
            rec["fetched_at"] = datetime.now(timezone.utc).isoformat()
            (ENRICH_DIR / f"{rec['asin']}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nDONE: {ok} item(s) returned, {fail} without an ASIN.")
    if PROBE:
        if ok > 0:
            print("SUCCESS: Creators API returned product data. The API is active.")
        else:
            die("FAILURE: no items returned. Check version/eligibility or the ASINs.")


if __name__ == "__main__":
    main()
