#!/usr/bin/env python3
"""
Amazon Product Advertising API (PA-API 5.0) enrichment for Halcott Magazine.

Reads every product in data/products/*.json, looks up each ASIN via PA-API,
and writes a cache file per ASIN to data/enrichment/<ASIN>.json containing the
official image URL, current price, and a cleaned-up title.

Credentials come ONLY from environment variables (GitHub Actions secrets):
    AMAZON_ACCESS_KEY, AMAZON_SECRET_KEY, AMAZON_PARTNER_TAG (default halcottmag-20)

Set PAAPI_PROBE=1 to run in probe mode: fetch a small sample, print the results,
and write nothing. Used to confirm the API is active and inspect the response shape.

This script never prints credential values.
"""
import os
import re
import sys
import json
import time
import glob
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS_DIR = ROOT / "data" / "products"
ENRICH_DIR = ROOT / "data" / "enrichment"

PROBE = os.environ.get("PAAPI_PROBE", "").strip() in ("1", "true", "yes")
ACCESS_KEY = os.environ.get("AMAZON_ACCESS_KEY", "").strip()
SECRET_KEY = os.environ.get("AMAZON_SECRET_KEY", "").strip()
PARTNER_TAG = os.environ.get("AMAZON_PARTNER_TAG", "halcottmag-20").strip()
COUNTRY = os.environ.get("AMAZON_COUNTRY", "US").strip() or "US"

THROTTLE_SECONDS = float(os.environ.get("PAAPI_THROTTLE", "1.3"))
BATCH_SIZE = 10


def die(msg, code=1):
    print(msg)
    sys.exit(code)


def load_products():
    """Return list of (asin, product_dict) for every product that has an Amazon ASIN."""
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
            if not isinstance(p, dict):
                continue
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

def clean_title(raw, brand=""):
    """Trim marketing cruft from an Amazon title into something that reads cleanly on-site."""
    if not raw:
        return ""
    t = " ".join(str(raw).split())
    # cut at the first strong separator
    for sep in (" - ", " – ", " — ", " | ", " ｜ "):
        i = t.find(sep)
        if i > 15:
            t = t[:i]
            break
    # drop trailing bracketed/parenthetical groups, repeatedly
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]\s*$", "", t).strip()
    # if still long and there's a comma, keep the part before the first comma
    if len(t) > 60 and "," in t:
        head = t.split(",", 1)[0].strip()
        if len(head) >= 20:
            t = head
    t = _FILLER.sub("", t)
    t = " ".join(t.split())
    # hard cap at a word boundary
    if len(t) > 70:
        cut = t[:70].rsplit(" ", 1)[0]
        t = cut.strip()
    # drop dangling trailing connector words
    _stop = {"with", "for", "and", "the", "a", "an", "to", "in", "by", "of", "plus", "w"}
    words = t.strip(" ,-–—|").split()
    while words and words[-1].lower().strip(",") in _stop:
        words.pop()
    return " ".join(words).strip(" ,-–—|")


# ---------------- PA-API extraction (defensive) ----------------
def _get(obj, *path):
    cur = obj
    for key in path:
        if cur is None:
            return None
        cur = getattr(cur, key, None)
    return cur


def extract(item):
    asin = getattr(item, "asin", None)
    image = _get(item, "images", "primary", "large", "url")
    if not image:
        image = _get(item, "images", "primary", "medium", "url")
    title = _get(item, "item_info", "title", "display_value")
    price_amount = None
    price_display = None
    listings = _get(item, "offers", "listings")
    if listings:
        price = getattr(listings[0], "price", None)
        if price is not None:
            price_amount = getattr(price, "amount", None)
            price_display = getattr(price, "display_amount", None)
    return {
        "asin": asin,
        "image": image,
        "title_raw": title,
        "title_clean": clean_title(title),
        "price_usd": price_amount,
        "price_display": price_display,
    }


def main():
    if not ACCESS_KEY or not SECRET_KEY:
        die("FAILURE: AMAZON_ACCESS_KEY / AMAZON_SECRET_KEY not set in environment.")
    print(f"Partner tag: {PARTNER_TAG} | country: {COUNTRY} | probe: {PROBE}")

    try:
        from amazon_paapi import AmazonApi
    except Exception as e:
        die(f"FAILURE: could not import amazon_paapi ({e}). Is python-amazon-paapi installed?")

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
        api = AmazonApi(ACCESS_KEY, SECRET_KEY, PARTNER_TAG, COUNTRY)
    except Exception as e:
        die(f"FAILURE: could not initialize AmazonApi ({e}).")

    ENRICH_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    fail = 0
    for start in range(0, len(asins), BATCH_SIZE):
        batch = asins[start:start + BATCH_SIZE]
        try:
            items = api.get_items(items=batch)
        except Exception as e:
            fail += len(batch)
            print(f"  ! batch {batch} failed: {type(e).__name__}: {e}")
            if PROBE:
                die("FAILURE: PA-API request errored. See message above. "
                    "If this says the associate is not eligible, the account is still under review.")
            time.sleep(THROTTLE_SECONDS)
            continue
        got = {}
        for item in (items or []):
            rec = extract(item)
            if rec.get("asin"):
                got[rec["asin"]] = rec
        for asin in batch:
            rec = got.get(asin)
            if not rec:
                fail += 1
                print(f"  - {asin}: no data returned")
                continue
            ok += 1
            if PROBE:
                print(f"  ✓ {asin}")
                print(f"      title_raw : {rec['title_raw']}")
                print(f"      title_clean: {rec['title_clean']}")
                print(f"      price      : {rec['price_display']} ({rec['price_usd']})")
                print(f"      image      : {rec['image']}")
            else:
                rec["fetched_at"] = datetime.now(timezone.utc).isoformat()
                (ENRICH_DIR / f"{asin}.json").write_text(
                    json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(THROTTLE_SECONDS)

    print(f"\nDONE: {ok} ok, {fail} missing/failed.")
    if PROBE:
        if ok > 0:
            print("SUCCESS: PA-API returned product data. The API is active.")
        else:
            die("FAILURE: no items returned. API may still be under review or keys are wrong.")


if __name__ == "__main__":
    main()
