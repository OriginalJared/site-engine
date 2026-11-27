import json
import os
import re
import sys
import shutil
from datetime import date
from pathlib import Path
from html import escape
from typing import Any, Dict, List, Optional
from collections import OrderedDict

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"
GENERATED_DIR = ROOT / "generated"

CATEGORIES_JSON = DATA_DIR / "categories.json"
COLLECTIONS_JSON = DATA_DIR / "collections.json"
PRODUCTS_DIR = DATA_DIR / "products"
ENRICH_DIR = DATA_DIR / "enrichment"
GUIDES_DIR = DATA_DIR / "guides"
AFFILIATES_JSON = DATA_DIR / "affiliates.json"
SITE_JSON = DATA_DIR / "site.json"

CATEGORY_TEMPLATE = TEMPLATES_DIR / "category.html"
PRODUCT_TEMPLATE = TEMPLATES_DIR / "product.html"
INDEX_TEMPLATE = TEMPLATES_DIR / "index.html"
GUIDE_TEMPLATE = TEMPLATES_DIR / "guide.html"

SITEMAP_XML = ROOT / "sitemap.xml"
ROBOTS_TXT = ROOT / "robots.txt"
INDEX_HTML = ROOT / "index.html"

DEFAULT_SITE_URL = "https://halcottmagazine.com"

# A niche only gets an auto-generated "Best {niche} for {tag}" page when at least
# this many products in that niche share the tag. Prevents thin, single-product
# category pages (the dominant "AI slop" signal). Curated categories defined in
# categories.json are always kept regardless of this threshold.
MIN_AUTO_TAG_CATEGORY_PRODUCTS = 2

_slug_re = re.compile(r"[^a-z0-9-]+")


def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_all_products() -> List[Any]:
    if not PRODUCTS_DIR.exists():
        return []
    all_products = []
    for f in sorted(PRODUCTS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Warning: Skipping {f.name}: {e}")
            continue
        if isinstance(data, list):
            all_products.extend(data)
        elif isinstance(data, dict):
            if "products" in data and isinstance(data["products"], list):
                all_products.extend(data["products"])
            else:
                all_products.append(data)
    return all_products


def read_text_required(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return path.read_text(encoding="utf-8")


def read_text_optional(path: Path, fallback: str) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else fallback


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str):
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def as_str(v: Any) -> str:
    return ("" if v is None else str(v)).strip()


def normalize_slug(v: Any) -> str:
    s = as_str(v).lower()
    s = s.replace("_", "-")
    s = "-".join(s.split())
    s = _slug_re.sub("-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


_DISPLAY_FIXES = {"Hdr": "HDR", "Usb": "USB", "Oled": "OLED", "Ips": "IPS", "Anc": "ANC", "Wifi": "Wi-Fi", "Tv": "TV", "Tvs": "TVs", "Led": "LED", "Gps": "GPS", "Rgb": "RGB", "Ssd": "SSD", "Hepa": "HEPA", "Uv": "UV", "Ai": "AI", "Pc": "PC", "Diy": "DIY", "Mesh": "Mesh", "Fps": "FPS", "Mmo": "MMO", "Lte": "LTE", "Adas": "ADAS"}


def slug_to_display(slug: str) -> str:
    words = [_DISPLAY_FIXES.get(w, w) for w in slug.replace("-", " ").title().split()]
    out = " ".join(words)
    return out.replace("USB C", "USB-C")


def require_list(v: Any, name: str) -> List[Any]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError(f"Expected '{name}' to be a list, got {type(v).__name__}")
    return v


def require_dict(v: Any, name: str) -> Dict[str, Any]:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError(f"Expected '{name}' to be a dict, got {type(v).__name__}")
    return v


def assert_unique_slugs(items: List[Dict[str, Any]], kind: str):
    seen = set()
    dups = set()
    for x in items:
        s = as_str(x.get("slug"))
        if not s:
            continue
        if s in seen:
            dups.add(s)
        seen.add(s)
    if dups:
        raise ValueError(f"Duplicate {kind} slug(s) found: {sorted(dups)}")


def load_site_config() -> Dict[str, Any]:
    if not SITE_JSON.exists():
        return {"name": "Site Engine", "tagline": "", "description": "", "url": DEFAULT_SITE_URL, "team": []}
    return load_json(SITE_JSON)


def build_niche_to_reviewer(site_config: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    mapping: Dict[str, Dict[str, str]] = {}
    for member in site_config.get("team", []):
        reviewer_info = {
            "name": as_str(member.get("name")),
            "slug": as_str(member.get("slug")),
            "role": as_str(member.get("role")),
            "bio": as_str(member.get("bio")),
        }
        for niche_slug in member.get("covers", []):
            mapping[normalize_slug(niche_slug)] = reviewer_info
    return mapping


def load_affiliates_config() -> Dict[str, Any]:
    if not AFFILIATES_JSON.exists():
        return {}
    return load_json(AFFILIATES_JSON)


def build_affiliate_url(network_ids: Dict[str, str], aff_config: Dict[str, Any]) -> str:
    if not aff_config:
        return "#"
    active = as_str(aff_config.get("active_network"))
    if not active:
        return "#"
    networks = aff_config.get("networks") or {}
    net_cfg = networks.get(active)
    if not net_cfg:
        return "#"
    product_id = as_str((network_ids or {}).get(active))
    if not product_id:
        return "#"
    pattern = as_str(net_cfg.get("url_pattern"))
    if not pattern:
        return "#"
    subs = {k: as_str(v) for k, v in net_cfg.items()}
    subs["product_id"] = product_id
    try:
        url = pattern.format_map(subs)
    except (KeyError, ValueError):
        return "#"
    tracking = aff_config.get("tracking") or {}
    utm_parts = []
    for key in ("utm_source", "utm_medium", "sub_id"):
        val = as_str(tracking.get(key))
        if val:
            utm_parts.append(f"{key}={val}")
    if utm_parts:
        separator = "&" if "?" in url else "?"
        url += separator + "&".join(utm_parts)
    return url


def normalize_products(raw_products: Any) -> List[Dict[str, Any]]:
    products = require_list(raw_products, "products")
    out: List[Dict[str, Any]] = []
    for i, p in enumerate(products):
        if not isinstance(p, dict):
            continue
        slug = normalize_slug(p.get("slug"))
        if not slug:
            continue
        name = as_str(p.get("name")) or slug
        brand = as_str(p.get("brand"))
        category = as_str(p.get("category"))
        description = as_str(p.get("description"))
        image_url = as_str(p.get("image_url"))
        price_usd = p.get("price_usd")
        rating = p.get("rating")
        verdict = as_str(p.get("verdict"))
        network_ids = require_dict(p.get("network_ids"), f"products[{i}].network_ids")
        best_for = [normalize_slug(x) for x in require_list(p.get("best_for"), f"products[{i}].best_for") if as_str(x)]
        specs = require_dict(p.get("specs"), f"products[{i}].specs")
        features = require_list(p.get("features"), f"products[{i}].features")
        pros = require_list(p.get("pros"), f"products[{i}].pros")
        cons = require_list(p.get("cons"), f"products[{i}].cons")
        out.append({
            "slug": slug, "name": name, "brand": brand, "category": category,
            "description": description, "price_usd": price_usd, "rating": rating,
            "verdict": verdict, "network_ids": network_ids, "affiliate_url": "",
            "image_url": image_url, "best_for": best_for, "specs": specs,
            "features": features, "pros": pros, "cons": cons,
        })
    out.sort(key=lambda x: x["slug"])
    assert_unique_slugs(out, "product")
    return out


TAG_CATEGORY_TEMPLATES = {
    "best-overall": {"slug_pattern": "best-{niche_slug}", "name": "Best {niche} {year} (Top Picks)", "description": "Our top-rated {niche} based on performance, features, and value. These are the products we recommend most often."},
    "budget": {"slug_pattern": "budget-{niche_slug}", "name": "Best Budget {niche}", "description": "Great {niche} that deliver solid performance without breaking the bank. Proof that you don't need to overspend to get quality."},
    "premium": {"slug_pattern": "premium-{niche_slug}", "name": "Premium {niche}", "description": "Top-of-the-line {niche} with the best features, build quality, and warranties available. For those who want the absolute best."},
}

# Natural-sounding page names for tag-based categories ({niche} is the niche display name).
# Tags not listed fall back to "Best {niche} for {tag}". Slugs (URLs) are unaffected.
TAG_NAME_PHRASES = {
    "compact": "Best Compact {niche}", "folding": "Best Folding {niche}", "2-in-1": "Best 2-in-1 {niche}", "versatile": "Most Versatile {niche}", "budget-premium": "Best Mid-Range {niche}",
    "non-wifi": "Best Non-Wi-Fi {niche}", "lightweight": "Best Lightweight {niche}", "wireless": "Best Wireless {niche}",
    "portable": "Best Portable {niche}", "automatic": "Best Automatic {niche}", "single-serve": "Best Single-Serve {niche}",
    "one-touch": "Best One-Touch {niche}", "cushioned": "Best Cushioned {niche}", "ergonomic": "Best Ergonomic {niche}",
    "personal": "Best Personal {niche}", "smart-features": "Best Smart {niche}", "battery-life": "{niche} With the Best Battery Life",
    "health": "Best {niche} for Health Tracking", "no-subscription": "Best {niche} Without a Subscription",
    "under-500": "Best {niche} Under $500", "drip-coffee": "Best Drip {niche}", "gas": "Best Gas {niche}",
    "indoor": "Best Indoor {niche}", "outdoor": "Best Outdoor {niche}", "solar": "Best Solar-Ready {niche}",
    "wifi-6e": "Best Wi-Fi 6E {niche}", "home-barista": "Best {niche} for Home Baristas", "beginner": "Best {niche} for Beginners",
    "first-keyboard": "Best {niche} for First-Timers", "large-home": "Best {niche} for Large Homes",
    "large-family": "Best {niche} for Large Families", "easy-setup": "Easiest {niche} to Set Up", "party": "Best {niche} for Parties",
    "audiophile": "Best {niche} for Audiophiles", "apple": "Best {niche} for Apple Users", "bedroom": "Best {niche} for Bedrooms",
    "noise-canceling": "Best Noise-Canceling {niche}", "long-range": "Best Long-Range {niche}", "fps-gaming": "Best {niche} for FPS Games",
    "commute": "Best {niche} for Commuting", "smart-home": "Best {niche} for Smart Homes", "students": "Best {niche} for Students",
}

DEFAULT_TAG_TEMPLATE = {"slug_pattern": "{niche_slug}-for-{tag_slug}", "name": "Best {niche} for {tag}", "description": "The best {niche} for {tag} — curated based on real specs, reviews, and performance data."}


def auto_generate_categories(products_by_niche: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    year = date.today().year
    auto_cats: List[Dict[str, Any]] = []
    for niche_slug in sorted(products_by_niche.keys()):
        if not niche_slug:
            continue
        niche_display = slug_to_display(niche_slug)
        niche_products = products_by_niche[niche_slug]
        tag_counts: Dict[str, int] = {}
        for p in niche_products:
            for t in (p.get("best_for") or []):
                tag = normalize_slug(t)
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        all_tags_sorted = sorted(tag_counts.keys())
        auto_cats.append({"slug": niche_slug, "name": niche_display, "niche": niche_slug, "description": f"Compare the best {niche_display.lower()} of {year} side by side. Browse all categories and find the right product for your needs and budget.", "tags": all_tags_sorted, "_auto": True})
        for tag_slug in all_tags_sorted:
            if tag_counts[tag_slug] < MIN_AUTO_TAG_CATEGORY_PRODUCTS:
                continue
            tag_display = slug_to_display(tag_slug)
            tmpl = TAG_CATEGORY_TEMPLATES.get(tag_slug, DEFAULT_TAG_TEMPLATE)
            cat_slug = tmpl["slug_pattern"].format(niche_slug=niche_slug, tag_slug=tag_slug)
            cat_name = tmpl["name"].format(niche=niche_display, tag=tag_display, year=str(year))
            if tag_slug in TAG_NAME_PHRASES:
                cat_name = TAG_NAME_PHRASES[tag_slug].format(niche=niche_display)
            cat_desc = tmpl["description"].format(niche=niche_display.lower(), tag=tag_display.lower(), year=str(year))
            auto_cats.append({"slug": cat_slug, "name": cat_name, "niche": niche_slug, "description": cat_desc, "tags": [tag_slug], "_auto": True})
    return auto_cats


# Price-tier gift guides, rebuilt every day from live Amazon prices. They only exist while
# fresh API prices are available, so "Under $50" is always true when a shopper sees it.
PRICE_GIFT_GUIDES = [
    {"slug": "gifts-under-50", "cap": 50, "name": "Gifts Under $50", "home_name": "Stocking Stuffers & Small Gifts",
     "description": "Small gifts and stocking stuffers that don't cost a fortune. This list updates daily with live Amazon prices, so every pick is in stock and within budget today."},
    {"slug": "gifts-under-100", "cap": 100, "name": "Gifts Under $100", "home_name": "Great Gifts for Less",
     "description": "Great gifts that feel generous without the big price tag, from headphones to kitchen upgrades. This list updates daily with live Amazon prices, so every pick is in stock and within budget today."},
]
PRICE_GIFT_SKIP_NICHES = {"gifts", "mattresses", "electric-bikes", "electric-scooters", "office-chairs", "standing-desks", "outdoor-grills"}


def build_price_gift_guides(products: List[Dict[str, Any]], per_guide: int = 12) -> List[Dict[str, Any]]:
    out = []
    priced = []
    for p in products:
        if not p.get("_api_price") or as_str(p.get("category")) in PRICE_GIFT_SKIP_NICHES:
            continue
        try:
            priced.append((float(p.get("price_usd")), p))
        except (TypeError, ValueError):
            continue
    for g in PRICE_GIFT_GUIDES:
        lo = 0 if g["cap"] <= 50 else 50  # "Under $100" focuses on the $50-$100 range so the two guides differ
        pool = [(pr, p) for pr, p in priced if lo < pr <= g["cap"]]
        def score(item):
            try:
                return float(item[1].get("rating") or 0)
            except (TypeError, ValueError):
                return 0.0
        pool.sort(key=score, reverse=True)
        picks, seen_niche = [], {}
        for pr, p in pool:  # at most two per department, for variety
            n = as_str(p.get("category"))
            if seen_niche.get(n, 0) >= 2:
                continue
            seen_niche[n] = seen_niche.get(n, 0) + 1
            picks.append(as_str(p.get("slug")))
            if len(picks) >= per_guide:
                break
        if len(picks) >= 6:
            out.append({"slug": g["slug"], "name": g["name"], "home_name": g["home_name"], "niche": "gifts",
                        "description": g["description"], "tags": [], "products": picks, "_auto": False})
    if out:
        summary = ", ".join("%s (%d)" % (c["slug"], len(c["products"])) for c in out)
        print(f"Price gift guides: {summary}")
    return out


def load_manual_categories() -> List[Dict[str, Any]]:
    out = []
    for _path in (CATEGORIES_JSON, COLLECTIONS_JSON):
        if not _path.exists():
            continue
        raw = load_json(_path)
        cats = require_list(raw, "categories")
        for i, c in enumerate(cats):
            if not isinstance(c, dict):
                continue
            slug = normalize_slug(c.get("slug"))
            if not slug:
                continue
            entry = {"slug": slug, "name": as_str(c.get("name")) or slug, "niche": as_str(c.get("niche")), "description": as_str(c.get("description")), "tags": [normalize_slug(t) for t in require_list(c.get("tags"), f"categories[{i}].tags") if as_str(t)], "_auto": False}
            if as_str(c.get("home_name")):
                entry["home_name"] = as_str(c.get("home_name"))
            prods = c.get("products")
            if isinstance(prods, list):
                entry["products"] = [as_str(s) for s in prods if as_str(s)]
            out.append(entry)
    return out


def merge_categories(auto_cats: List[Dict[str, Any]], manual_cats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    # A hand-made page and an auto page covering the same niche + tag (e.g. "for bedroom" vs "for bedrooms")
    # would be near-duplicates; keep the hand-made one.
    manual_keys = {(c.get("niche"), tuple(sorted(c.get("tags") or []))) for c in manual_cats
                   if c.get("tags") and c.get("slug") != c.get("niche")}
    def _norm_tags(tags):
        return tuple(sorted(t[:-1] if t.endswith("s") else t for t in tags))
    manual_norm = {(n, _norm_tags(t)) for n, t in manual_keys}
    for c in auto_cats:
        if c.get("slug") != c.get("niche") and (c.get("niche"), _norm_tags(c.get("tags") or [])) in manual_norm:
            continue
        merged[c["slug"]] = c
    for c in manual_cats:
        merged[c["slug"]] = c
    out = sorted(merged.values(), key=lambda x: x["slug"])
    return out


def build_sidebar_links(niche: str, categories: List[Dict[str, Any]], exclude_slug: str = "") -> str:
    niche_cats = [c for c in categories if as_str(c.get("niche")) == niche and as_str(c.get("slug")) != exclude_slug]
    if not niche_cats:
        return "<li>No related categories yet.</li>"
    items = []
    for c in niche_cats:
        slug = as_str(c.get("slug"))
        name = as_str(c.get("name"))
        href = escape(f"/generated/categories/{slug}/", quote=True)
        items.append(f'<li><a href="{href}">{escape(name)}</a></li>')
    return "\n            ".join(items)


DEPARTMENTS = OrderedDict([
    ("Tech", ["wireless-earbuds", "over-ear-headphones", "headphones", "bluetooth-speakers", "laptops", "tablets", "smartwatches", "smart-watches", "mesh-wifi-routers", "mechanical-keyboards", "gaming-mice", "4k-monitors", "monitors", "projectors", "dash-cams", "home-security-cameras", "baby-monitors", "portable-power-stations"]),
    ("Home", ["robot-vacuums", "cordless-stick-vacuums", "stick-vacuums", "air-purifiers", "mattresses", "office-chairs", "standing-desks"]),
    ("Kitchen", ["coffee-makers", "espresso-machines", "blenders", "air-fryers"]),
    ("Outdoor", ["outdoor-grills", "running-shoes", "electric-bikes", "electric-scooters"]),
])


def real_image(u: str) -> bool:
    """Amazon serves a tiny 'No image available' GIF when a listing has no photo."""
    return u.startswith("https://") and not u.lower().endswith(".gif") and "01RmK+J4pJL" not in u


PASTELS = ["#f6ecd2", "#e3efe6", "#e6ecf5", "#f5e4df", "#ece6f3", "#e2f0f1"]


def _pimg(p: Dict[str, Any]) -> str:
    u = as_str(p.get("image_url"))
    return u if real_image(u) else ""


def photo_trio_html(prods: List[Dict[str, Any]], n: int = 3) -> str:
    """Up to n product photos as a small collage on a white plate."""
    imgs = []
    for q in prods:
        u = _pimg(q)
        if u and u not in imgs:
            imgs.append(u)
        if len(imgs) >= n:
            break
    if not imgs:
        return ""
    cells = "".join(f'<span class="trio-cell"><img src="{escape(u, quote=True)}" alt="" loading="lazy" /></span>' for u in imgs)
    return f'<div class="photo-trio trio-{len(imgs)}">{cells}</div>'


def _rated(prods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(prods, key=lambda q: -(float(q.get("rating")) if isinstance(q.get("rating"), (int, float)) else 0))


def cat_products(cat: Dict[str, Any], products_by_niche: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    explicit = cat.get("products") if isinstance(cat.get("products"), list) else None
    if explicit:
        by_slug = {as_str(q.get("slug")): q for lst in products_by_niche.values() for q in lst}
        return [by_slug[as_str(x)] for x in explicit if as_str(x) in by_slug]
    nprods = products_by_niche.get(as_str(cat.get("niche")), [])
    tags = [normalize_slug(t) for t in (cat.get("tags") or []) if as_str(t)]
    m = [q for q in nprods if product_matches(q, tags)] if tags else list(nprods)
    return m or list(nprods)


def photo_card_html(href: str, title: str, desc: str, prods: List[Dict[str, Any]], i: int, cta: str = "", kicker: str = "") -> str:
    tint = PASTELS[i % len(PASTELS)]
    trio = photo_trio_html(_rated(prods))
    k = f'<p class="pc-kicker">{escape(kicker)}</p>' if kicker else ""
    c = f'<span class="pc-cta">{cta}</span>' if cta else ""
    return (f'<a class="photo-card" href="{escape(href, quote=True)}" style="--tint:{tint}">'
            f'<div class="pc-media">{trio}</div><div class="pc-body">{k}<h3>{escape(title)}</h3>'
            f'<p>{escape(desc)}</p>{c}</div></a>')


def build_homepage(categories: List[Dict[str, Any]], products_by_niche: Dict[str, List[Dict[str, Any]]], niche_to_reviewer: Dict[str, Dict[str, str]], guides: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
    guides = guides or []
    niches: Dict[str, List[Dict[str, Any]]] = OrderedDict()
    for c in categories:
        niche = as_str(c.get("niche"))
        if niche not in niches:
            niches[niche] = []
        niches[niche].append(c)

    def hub_href_for(niche: str) -> str:
        hub = None
        for c in niches.get(niche, []):
            if as_str(c.get("slug")) == niche:
                hub = c
                break
        slug = as_str(hub.get("slug")) if hub else niche
        return escape(f"/generated/categories/{slug}/", quote=True)

    def dept_dropdown(label, members):
        links = "".join(f'<a href="{hub_href_for(n)}">{escape(slug_to_display(n))}</a>' for n in members)
        return ('<div class="catnav-dd">'
                f'<button type="button" class="catnav-link catnav-dd-btn">{escape(label)} <span class="catnav-caret" aria-hidden="true">&#9662;</span></button>'
                f'<div class="catnav-dd-menu">{links}</div></div>')

    assigned = set()
    dept_items = []
    for dept, members in DEPARTMENTS.items():
        present = [n for n in members if n in niches]
        for n in present:
            assigned.add(n)
        if present:
            dept_items.append(dept_dropdown(dept, present))
    leftovers = [n for n in niches.keys() if n and n != "gifts" and n not in assigned]
    if leftovers:
        dept_items.append(dept_dropdown("More", sorted(leftovers)))

    nav_items = []
    if "gifts" in niches:
        nav_items.append(f'<a class="catnav-link catnav-accent" href="{hub_href_for("gifts")}">Gift Guides</a>')
    if guides:
        nav_items.append('<a class="catnav-link" href="/generated/guides/">Buying Guides</a>')
    nav_items.extend(dept_items)
    nav_items.append('<a class="catnav-link catnav-all" href="/generated/categories/">All Categories</a>')
    category_nav = '<nav class="category-nav" aria-label="Browse categories"><div class="container catnav-inner">' + "".join(nav_items) + '</div></nav>'

    gift_block = ""
    gift_cats = niches.get("gifts", [])
    if gift_cats:
        gift_subs = [c for c in gift_cats if as_str(c.get("slug")) != "gifts"]
        gift_subs.sort(key=lambda c: {"gifts-under-50": 0, "gifts-under-100": 1}.get(as_str(c.get("slug")), 2))
        gift_subs = gift_subs[:8]
        cards = []
        for sc in gift_subs:
            sc_slug = as_str(sc.get("slug"))
            sc_name = as_str(sc.get("home_name")) or as_str(sc.get("name"))
            sc_desc = as_str(sc.get("description"))
            if len(sc_desc) > 140:
                sc_desc = sc_desc[:137].rsplit(" ", 1)[0] + "..."
            href = escape(f"/generated/categories/{sc_slug}/", quote=True)
            cards.append(photo_card_html(f"/generated/categories/{sc_slug}/", sc_name, sc_desc,
                                         cat_products(sc, products_by_niche), len(cards), "Shop this guide &rarr;"))
        gift_block = ('<section class="home-block gift-block">'
                      '<div class="block-head"><h2>Holiday Gift Guides</h2>'
                      f'<a class="block-head-link" href="{hub_href_for("gifts")}">See all gift guides &rarr;</a></div>'
                      '<p class="block-sub">Hand-picked, verified-in-stock gifts for everyone on your list &mdash; ready for Black Friday.</p>'
                      '<div class="photo-grid photo-grid-4">' + "".join(cards) + '</div></section>')

    guides_block = ""
    if guides:
        cards = []
        for g in guides[:6]:
            gslug = as_str(g.get("slug"))
            gtitle = as_str(g.get("title"))
            gdesc = as_str(g.get("description"))
            if len(gdesc) > 130:
                gdesc = gdesc[:127].rsplit(" ", 1)[0] + "..."
            href = escape(f"/generated/guides/{gslug}/", quote=True)
            cards.append(photo_card_html(f"/generated/guides/{gslug}/", gtitle, gdesc,
                                         products_by_niche.get(as_str(g.get("niche")), []), len(cards) + 2,
                                         "Read the guide &rarr;", "Buying guide"))
        guides_block = ('<section class="home-block guides-block">'
                        '<div class="block-head"><h2>Buying Guides</h2>'
                        '<a class="block-head-link" href="/generated/guides/">See all guides &rarr;</a></div>'
                        '<p class="block-sub">What actually matters before you buy &mdash; no jargon, no fluff.</p>'
                        '<div class="photo-grid photo-grid-3">' + "".join(cards) + '</div></section>')

    # ---- Featured highlights (Wirecutter-style top block) ----
    MARQUEE = ["wireless-earbuds", "robot-vacuums", "mattresses", "coffee-makers", "running-shoes", "outdoor-grills", "mesh-wifi-routers", "air-purifiers", "laptops", "office-chairs"]
    def _fprice(p):
        v = p.get("price_usd")
        try:
            v = float(v)
        except (TypeError, ValueError):
            return ""
        if v <= 0:
            return ""
        return "$" + (str(int(v)) if v == int(v) else f"{v:.2f}")
    def _fpick(n):
        pl = products_by_niche.get(n) or []
        return _best_pick(pl) if pl else None
    marquee_picks = []
    for _n in MARQUEE:
        _p = _fpick(_n)
        if _p and as_str(_p.get("slug")):
            marquee_picks.append((_n, _p))
    featured_block = ""
    if marquee_picks:
        latest_items = ""
        for g in (guides or [])[:5]:
            _gp = _rated([q for q in products_by_niche.get(as_str(g.get("niche")), []) if _pimg(q)])
            _gt = (f'<span class="hl-thumb"><img src="{escape(_pimg(_gp[0]), quote=True)}" alt="" loading="lazy" /></span>') if _gp else ""
            latest_items += f'<li><a href="/generated/guides/{as_str(g.get("slug"))}/">{_gt}<span>{escape(as_str(g.get("title")))}</span></a></li>'
        latest_html = (f'<div class="home-latest"><h2>Buying Guides</h2><ul class="latest-list">{latest_items}</ul>'
                       '<a class="home-latest-more" href="/generated/guides/">See all guides &rarr;</a></div>') if latest_items else '<div class="home-latest"></div>'
        ln, lp = marquee_picks[0]
        lslug = as_str(lp.get("slug"))
        limg = _pimg(lp)
        feat_img = (f'<a class="feat-img" href="/generated/products/{lslug}/">'
                    f'<img src="{escape(limg, quote=True)}" alt="{escape(as_str(lp.get("name")))}" loading="lazy" /></a>') if limg else ''
        feature_html = (f'<div class="home-feature">'
                        f'{feat_img}'
                        f'<a class="feat-eyebrow" href="{hub_href_for(ln)}">{escape(slug_to_display(ln))}</a>'
                        f'<h2 class="feat-title"><a href="/generated/products/{lslug}/">{escape(as_str(lp.get("name")))}</a></h2>'
                        f'<p class="feat-verdict">{escape(as_str(lp.get("verdict")))}</p>'
                        f'<div class="feat-foot"><a class="feat-cta" href="/generated/products/{lslug}/">Read our review &rarr;</a></div>'
                        f'</div>')
        rail = ""
        for _n, _p in [mp for mp in marquee_picks[1:] if _pimg(mp[1])][:4]:
            _ps = as_str(_p.get("slug"))
            rail += (f'<a class="hp-tile" href="/generated/products/{_ps}/">'
                     f'<span class="hp-tile-img"><img src="{escape(_pimg(_p), quote=True)}" alt="{escape(as_str(_p.get("name")))}" loading="lazy" /></span>'
                     f'<span class="hp-tile-eyebrow">{escape(slug_to_display(_n))}</span>'
                     f'<span class="hp-tile-name">{escape(as_str(_p.get("name")))}</span></a>')
        rail_html = f'<div class="home-picks"><h2>Top Picks</h2><div class="hp-tiles">{rail}</div></div>'
        featured_block = f'<section class="home-feature-row">{latest_html}{feature_html}{rail_html}</section>'

    # ---- Bold CNET-style hero (data/hero.json); falls back to the featured block above ----
    try:
        _hero_cfg = json.loads((DATA_DIR / "hero.json").read_text(encoding="utf-8"))
    except Exception:
        _hero_cfg = None
    def _hx_np(t):
        return re.sub(r"\s*\$\s?\d[\d,]*(?:\.\d+)?", "", t)
    if isinstance(_hero_cfg, dict):
        _by_slug = {as_str(q.get("slug")): q for lst in products_by_niche.values() for q in lst}
        _cat_slugs = {as_str(c.get("slug")) for c in categories}
        def _hp(slug):
            q = _by_slug.get(as_str(slug))
            return q if q and _pimg(q) else None
        def _chref(slug):
            return escape(f"/generated/categories/{slug}/", quote=True) if as_str(slug) in _cat_slugs else ""
        rail_items = []
        for it in _hero_cfg.get("rail") or []:
            h = _chref(it.get("category"))
            if h and as_str(it.get("headline")):
                rail_items.append(f'<li><a href="{h}">{escape(_hx_np(as_str(it.get("headline"))))}</a></li>')
        L = _hero_cfg.get("lead") or {}
        lead_href = _chref(L.get("category"))
        lead_imgs = [q for q in (_hp(x) for x in (L.get("images") or [])) if q][:6]
        tiles = []
        for it in _hero_cfg.get("tiles") or []:
            if it.get("product"):
                q = _hp(it.get("product"))
                h = escape(f"/generated/products/{as_str(it.get('product'))}/", quote=True) if q else ""
            else:
                q = _hp(it.get("image"))
                h = _chref(it.get("category")) if q else ""
            if q and h and as_str(it.get("headline")):
                tiles.append((h, q, _hx_np(as_str(it.get("headline"))), as_str(it.get("color")) or "yellow"))
        if lead_href and lead_imgs and as_str(L.get("headline")) and len(tiles) >= 2:
            floats = "".join(
                f'<span class="hx-float hx-f{i + 1}"><img src="{escape(_pimg(q), quote=True)}" alt="{escape(as_str(q.get("name")), quote=True)}" /></span>'
                for i, q in enumerate(lead_imgs))
            lead_html = (f'<a class="hx-lead" href="{lead_href}">'
                         f'<div class="hx-lead-art"><span class="hx-burst">{escape(as_str(L.get("kicker")) or "Top Picks")}</span>{floats}</div>'
                         f'<div class="hx-lead-txt"><h2>{escape(_hx_np(as_str(L.get("headline"))))}</h2>'
                         f'<p>{escape(_hx_np(as_str(L.get("dek"))))}</p>'
                         f'<span class="hx-lead-cta">Shop the gift guides &rarr;</span></div></a>')
            tiles_html = "".join(
                f'<a class="hx-tile hx-{escape(col, quote=True)}" href="{h}"><div class="hx-tile-art">'
                f'<img src="{escape(_pimg(q), quote=True)}" alt="{escape(as_str(q.get("name")), quote=True)}" loading="lazy" />{home_price_chip(q)}</div>'
                f'<h3>{escape(t)}</h3></a>' for h, q, t, col in tiles[:4])
            rail_html2 = (f'<aside class="hx-rail"><div class="hx-rail-head"><p class="hx-rail-kicker">{escape(as_str(_hero_cfg.get("rail_title")) or "Best of the Best")}</p>'
                          f'<p class="hx-rail-sub">{escape(as_str(_hero_cfg.get("rail_sub")))}</p></div>'
                          f'<ul>{"".join(rail_items)}</ul>'
                          f'<a class="hx-rail-more" href="/generated/categories/">All categories &rarr;</a></aside>') if rail_items else ""
            _asof = next((sale_price_bits(q).get("asof") for _, q, _, _ in tiles[:4] if sale_price_bits(q)), "")
            asof_html = (f'<p class="hx-asof" title="{escape(AMAZON_PRICE_DISCLAIMER, quote=True)}">'
                         f'Prices are from Amazon, {_asof}, and may change. Hover a price for details.</p>') if _asof else ""
            featured_block = (f'<section class="hx">{rail_html2}<div class="hx-main">{lead_html}'
                              f'<div class="hx-tiles">{tiles_html}</div>{asof_html}</div></section>')
        else:
            print("WARNING: hero.json lead/tiles invalid; using automatic featured block")

    niche_sections = []
    def _no_price(t):
        t = re.sub(r"\s*\((?:[^()]*\$\d[^()]*)\)", "", t)
        return re.sub(r"\s*(?:under|below|less than|for|at)?\s*\$\d[\d,]*(?:\.\d+)?\+?", "", t, flags=re.I).strip()
    def _dept_headline(c, is_hub):
        if is_hub:
            return "The Best " + slug_to_display(as_str(c.get("niche")))
        nm = _no_price(as_str(c.get("name")))
        return ("The " + nm) if nm.startswith("Best ") else ("The Best " + nm)
    def _dept_thumb(p, href, cls):
        img = as_str(p.get("image_url"))
        if img:
            inner = f'<img src="{escape(img, quote=True)}" alt="{escape(as_str(p.get("name")))}" loading="lazy" />'
            return f'<a class="{cls} has-img" href="{href}" tabindex="-1">{inner}{home_price_chip(p)}</a>'
        label = as_str(p.get("brand")) or as_str(p.get("name")) or "?"
        accent = _accent_for(as_str(p.get("brand")) or as_str(p.get("slug")))
        return (f'<a class="{cls}" href="{href}" tabindex="-1" aria-hidden="true" style="--cover-accent:{accent}">'
                f'<span class="dept-mono">{escape(label)}</span></a>')
    def _dept_dek(p):
        name = as_str(p.get("name"))
        verdict = re.sub(r"[,]?\s*(?:at |for |around |about )?(?:under |less than |just )?\$\d[\d,]*(?:\.\d+)?\+?", "", as_str(p.get("verdict"))).strip()
        return f'Our pick: <strong>{escape(name)}</strong>. {escape(verdict)}' if verdict else f'Our pick: <strong>{escape(name)}</strong>.'
    TAG_PRIORITY = ["budget", "premium"]
    _stories_path = DATA_DIR / "homepage-stories.json"
    homepage_stories = {}
    if _stories_path.exists():
        try:
            homepage_stories = json.loads(_stories_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"WARNING: could not read homepage-stories.json: {e}")
    dept_idx = -1
    for niche, cats in niches.items():
        if niche == "gifts" or not niche:
            continue
        display = slug_to_display(niche)
        nprods = products_by_niche.get(niche, []) or []
        hub_cat = next((c for c in cats if as_str(c.get("slug")) == niche), None)
        hub_href = hub_href_for(niche)
        used = set()
        def _pick_for(c):
            tags = [normalize_slug(t) for t in (c.get("tags") or [])]
            matched = [p for p in nprods if product_matches(p, tags)] if tags else list(nprods)
            ranked = sorted(matched, key=lambda p: -(float(p.get("rating")) if isinstance(p.get("rating"), (int, float)) else 0))
            for p in ranked:
                if as_str(p.get("slug")) not in used:
                    return p
            return None
        lead_p = _pick_for(hub_cat) if hub_cat else _best_pick(nprods)
        if not lead_p:
            continue
        used.add(as_str(lead_p.get("slug")))
        subs = [c for c in cats if as_str(c.get("slug")) not in (niche, f"best-{niche}")]
        def _prio(c):
            tags = c.get("tags") or []
            t = tags[0] if len(tags) == 1 else ""
            return (TAG_PRIORITY.index(t) if t in TAG_PRIORITY else len(TAG_PRIORITY), 0)
        subs_sorted = sorted(subs, key=_prio)
        side = []
        for c in subs_sorted:
            if len(side) >= 3:
                break
            p = _pick_for(c)
            if p:
                used.add(as_str(p.get("slug")))
                side.append((c, p))
        side = [(c, p, None) for c, p in side]
        lead_story = None
        story = homepage_stories.get(niche) if isinstance(homepage_stories, dict) else None
        if isinstance(story, dict):
            prod_by_slug = {as_str(p.get("slug")): p for p in nprods}
            cat_by_slug = {as_str(c.get("slug")): c for c in cats}
            L = story.get("lead") or {}
            lp = prod_by_slug.get(as_str(L.get("pick")))
            if lp and as_str(L.get("headline")):
                lead_p, lead_story = lp, L
            else:
                print(f"WARNING: homepage story lead for {niche} invalid; using automatic lead")
            new_side = []
            for st in (story.get("side") or []):
                c = cat_by_slug.get(as_str(st.get("category")))
                p = prod_by_slug.get(as_str(st.get("pick")))
                ok = False
                if c and p and as_str(st.get("headline")):
                    explicit = c.get("products") if isinstance(c.get("products"), list) else None
                    if explicit:
                        ok = as_str(p.get("slug")) in [as_str(x) for x in explicit]
                    else:
                        ok = product_matches(p, [normalize_slug(t) for t in (c.get("tags") or [])])
                if ok:
                    new_side.append((c, p, st))
                else:
                    print(f"WARNING: homepage story {niche}/{as_str(st.get('category'))} invalid (pick not on that page); skipped")
            if new_side:
                side = new_side
        # Fill empty side slots with other well-rated picks from this department (links to their reviews).
        if len(side) < 3:
            _taken = {as_str(lead_p.get("slug"))} | {as_str(p.get("slug")) for _, p, _ in side}
            for q in _rated([q for q in nprods if _pimg(q)]):
                if len(side) >= 3:
                    break
                if as_str(q.get("slug")) not in _taken:
                    _taken.add(as_str(q.get("slug")))
                    side.append((None, q, None))
        dept_idx += 1
        three_up = (dept_idx % 2 == 1) and len(side) >= 2
        if three_up:
            side = side[:2]
        side_slugs = {as_str(c.get("slug")) for c, _, _ in side if c}
        rest = [c for c in subs if as_str(c.get("slug")) not in side_slugs]
        link_bits = []
        for c in rest:
            tags = c.get("tags") or []
            label = slug_to_display(tags[0]) if len(tags) == 1 else _no_price(as_str(c.get("name")))
            link_bits.append(f'<a href="{escape("/generated/categories/" + as_str(c.get("slug")) + "/", quote=True)}">{escape(label)}</a>')
        see_all = f'<a class="dept-seeall" href="{hub_href}">See all {escape(display)} &rarr;</a>'
        links_html = " ".join(link_bits + [see_all])
        updated = f'<p class="dept-updated">Updated {escape(UPDATED_LABEL)}</p>'
        lead_href = hub_href
        lead_title = escape(as_str(lead_story.get("headline"))) if lead_story else escape(_dept_headline(hub_cat or {"niche": niche}, True))
        lead_dek = escape(as_str(lead_story.get("dek"))) if lead_story and as_str(lead_story.get("dek")) else _dept_dek(lead_p)
        lead_html = (f'<article class="dept-lead">{_dept_thumb(lead_p, lead_href, "dept-img dept-img-lg")}'
                     f'<h3><a href="{lead_href}">{lead_title}</a></h3>'
                     f'<p class="dept-dek">{lead_dek}</p>{updated}</article>')
        side_html = ""
        def _side_href(c, p):
            if c is None:
                return escape("/generated/products/" + as_str(p.get("slug")) + "/", quote=True)
            return escape("/generated/categories/" + as_str(c.get("slug")) + "/", quote=True)
        def _side_title(c, p, st):
            if st:
                return escape(as_str(st.get("headline")))
            if c is None:
                return escape(as_str(p.get("name")))
            return escape(_dept_headline(c, False))
        for c, p, st in side:
            href = _side_href(c, p)
            s_title = _side_title(c, p, st)
            s_dek = (escape(as_str(st.get("dek"))) if st and as_str(st.get("dek"))
                     else escape(_no_price(as_str(p.get("verdict")))) if c is None else _dept_dek(p))
            side_html += (f'<article class="dept-item">{_dept_thumb(p, href, "dept-img")}'
                          f'<div class="dept-item-txt"><h3><a href="{href}">{s_title}</a></h3>'
                          f'<p class="dept-dek">{s_dek}</p>{updated}</div></article>')
        if three_up:
            cards = [(lead_href, lead_p, lead_title, lead_dek)]
            for c, p, st in side:
                href = _side_href(c, p)
                cards.append((href,
                              p,
                              _side_title(c, p, st),
                              escape(as_str(st.get("dek"))) if st and as_str(st.get("dek"))
                              else escape(_no_price(as_str(p.get("verdict")))) if c is None else _dept_dek(p)))
            body_html = '<div class="dept-grid3">' + "".join(
                f'<article class="dept-card">{_dept_thumb(p, h, "dept-img")}'
                f'<h3><a href="{h}">{t}</a></h3><p class="dept-dek">{d}</p>{updated}</article>'
                for h, p, t, d in cards) + '</div>'
        else:
            body_html = f'<div class="dept-grid">{lead_html}<div class="dept-side">{side_html}</div></div>'
        section = (f'<section class="dept" id="dept-{escape(niche, quote=True)}">'
                   f'<h2 class="dept-title"><a href="{hub_href}">{escape(display)}</a></h2>'
                   f'<p class="dept-links">{links_html}</p>'
                   f'{body_html}'
                   f'</section>')
        niche_sections.append(section)

    all_cats_parts = []
    for niche, cats in niches.items():
        display = slug_to_display(niche)
        all_cats_parts.append(f'<h3>{escape(display)}</h3>')
        all_cats_parts.append('<ul style="margin:6px 0 18px 18px;">')
        for c in cats:
            c_slug = as_str(c.get("slug"))
            c_name = as_str(c.get("name"))
            c_href = escape(f"/generated/categories/{c_slug}/", quote=True)
            all_cats_parts.append(f'<li><a href="{c_href}">{escape(_no_price(c_name))}</a></li>')
        all_cats_parts.append('</ul>')

    standards = [
        ("Research-driven", "We compare specifications, expert consensus, and verified owner feedback to reach each recommendation."),
        ("Independent", "We don't accept payment for placement, and we don't take free products in exchange for coverage. Rankings are earned on merit."),
        ("Transparent", "We earn affiliate commissions when you buy through our links, at no extra cost to you. It never changes our picks."),
        ("Kept current", "We update our guides and picks as products change and new models arrive."),
    ]
    team_parts = ['<div class="team-grid">']
    for _t, _b in standards:
        team_parts.append('<div class="team-card">')
        team_parts.append(f'<h3>{escape(_t)}</h3>')
        team_parts.append(f'<p class="team-bio">{escape(_b)}</p>')
        team_parts.append('</div>')
    team_parts.append('</div>')
    return {
        "CATEGORY_NAV": category_nav,
        "FEATURED_BLOCK": featured_block,
        "GIFT_BLOCK": gift_block,
        "GUIDES_BLOCK": guides_block,
        "NICHE_SECTIONS": "\n".join(niche_sections),
        "ALL_CATEGORIES_LIST": '<a class="all-cats-btn" href="/generated/categories/">Browse all categories &rarr;</a>',
        "TEAM_SECTION": "\n".join(team_parts),
    }
EDITORIAL_DIR = ROOT / "data" / "editorial"
THIN_CATEGORY_MIN = 3


def display_noun(display: str) -> str:
    return " ".join(w if (w.isupper() or any(ch.isdigit() for ch in w) or "-" in w and w.upper() == w) else w.lower() for w in display.split())


def load_editorial(niche: str) -> Optional[Dict[str, Any]]:
    path = EDITORIAL_DIR / f"{niche}.json"
    if not niche or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARNING: bad editorial file {path.name}: {e}")
        return None
    return data if isinstance(data, dict) else None


def build_editorial_html(ed: Optional[Dict[str, Any]], display: str, n_products: int) -> str:
    if not ed:
        return ""
    noun = display_noun(display)
    out = ['<div class="cat-editorial">']
    out.append('<section class="ed-section" id="why-trust-us"><h2>Why you should trust us</h2>'
               f'<p>We compared {n_products} {escape(noun)} using manufacturer specifications, published expert reviews, '
               'and verified owner feedback, and we revisit our picks as models change. '
               'We don\'t accept payment for placement or free products in exchange for coverage. '
               'Read our <a href="/editorial-policy/">editorial policy</a> for how we work.</p></section>')
    who = [as_str(x) for x in (ed.get("who_for") or []) if as_str(x)]
    if who:
        out.append('<section class="ed-section" id="who-this-is-for"><h2>Who this is for</h2>'
                   + "".join(f"<p>{escape(x)}</p>" for x in who) + '</section>')
    crit = [c for c in (ed.get("how_we_picked") or []) if isinstance(c, dict) and as_str(c.get("label"))]
    if crit:
        items = "".join(f'<li><strong>{escape(as_str(c.get("label")))}:</strong> {escape(as_str(c.get("text")))}</li>' for c in crit)
        out.append(f'<section class="ed-section" id="how-we-picked"><h2>How we picked</h2><ul class="ed-criteria">{items}</ul></section>')
    out.append('</div>')
    return "".join(out)


def data_driven_description(display: str, matched: List[Dict[str, Any]]) -> str:
    best = primary_pick(matched) or (matched[0] if matched else None)
    if not best:
        return ""
    name = as_str(best.get("name"))
    verdict = as_str(best.get("verdict")).rstrip(".")
    n = len(matched)
    lead = f"Our pick is the {name}"
    lead += f": {verdict[:1].lower() + verdict[1:]}." if verdict else "."
    return f"{lead} We compared {n} {display_noun(display)} on specs, expert reviews, and owner feedback."



# ---------------- Page embellishments (pick cards, share row, section bar, promo) ----------------
def build_section_bar(items: List[tuple]) -> str:
    """Sticky 'Sections in this article' bar shown under the header."""
    if not items:
        return ""
    lis = "".join(f'<li><a href="#{escape(a, quote=True)}">{escape(l)}</a></li>' for a, l in items)
    return ('<div class="section-bar"><div class="container sb-inner">'
            '<span class="sb-label">Browse</span>'
            '<details class="sb-menu"><summary>Sections in this article</summary>'
            f'<ul>{lis}</ul></details></div></div>'
            '<script>document.addEventListener("click",function(e){var a=e.target.closest(".sb-menu a");'
            'if(a){var d=a.closest("details");if(d)d.removeAttribute("open");}});</script>')


_SHARE_ICONS = {
    "link": '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M10.6 13.4a1 1 0 0 1 0-1.4l3.4-3.4a3 3 0 1 1 4.2 4.2l-1.8 1.8a1 1 0 1 1-1.4-1.4l1.8-1.8a1 1 0 1 0-1.4-1.4L12 13.4a1 1 0 0 1-1.4 0zm2.8-2.8a1 1 0 0 1 0 1.4L10 15.4a3 3 0 1 1-4.2-4.2l1.8-1.8a1 1 0 1 1 1.4 1.4l-1.8 1.8a1 1 0 1 0 1.4 1.4l3.4-3.4a1 1 0 0 1 1.4 0z"/></svg>',
    "x": '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M17.8 3h3.1l-6.8 7.8L22 21h-6.2l-4.9-6.4L5.3 21H2.2l7.3-8.3L2 3h6.4l4.4 5.8L17.8 3zm-1.1 16.2h1.7L7.4 4.7H5.6l11.1 14.5z"/></svg>',
    "fb": '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M14 8.5V6.8c0-.8.5-1 1-1h2V2.5h-2.8C11.1 2.5 10.5 4.8 10.5 6.3v2.2H8.3V12h2.2v9.5H14V12h2.7l.4-3.5H14z"/></svg>',
    "mail": '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm1 2.4V17h16V7.4l-8 5.3-8-5.3zM5.6 7l6.4 4.3L18.4 7H5.6z"/></svg>',
}


def share_row_html(url: str, title: str) -> str:
    from urllib.parse import quote
    u, t = quote(url, safe=""), quote(title, safe="")
    return ('<div class="share-row" aria-label="Share this page">'
            f'<button type="button" class="share-btn share-copy" data-url="{escape(url, quote=True)}" title="Copy link" aria-label="Copy link">{_SHARE_ICONS["link"]}<span class="share-copied">Link copied</span></button>'
            f'<a class="share-btn" href="https://twitter.com/intent/tweet?url={u}&amp;text={t}" target="_blank" rel="noopener" title="Share on X" aria-label="Share on X">{_SHARE_ICONS["x"]}</a>'
            f'<a class="share-btn" href="https://www.facebook.com/sharer/sharer.php?u={u}" target="_blank" rel="noopener" title="Share on Facebook" aria-label="Share on Facebook">{_SHARE_ICONS["fb"]}</a>'
            f'<a class="share-btn" href="mailto:?subject={t}&amp;body={u}" title="Share by email" aria-label="Share by email">{_SHARE_ICONS["mail"]}</a>'
            '</div>'
            '<script>document.addEventListener("click",function(e){var b=e.target.closest(".share-copy");if(!b)return;'
            'var u=b.getAttribute("data-url");var done=function(){b.classList.add("is-copied");setTimeout(function(){b.classList.remove("is-copied")},1800)};'
            'try{navigator.clipboard.writeText(u).then(done,function(){prompt("Copy this link:",u)})}catch(x){prompt("Copy this link:",u)}});</script>')


def promo_box_html() -> str:
    path = DATA_DIR / "promo.json"
    if not path.exists():
        return ""
    try:
        pr = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARNING: bad promo.json: {e}")
        return ""
    today = date.today().isoformat()
    if as_str(pr.get("start")) and today < as_str(pr.get("start")):
        return ""
    if as_str(pr.get("end")) and today > as_str(pr.get("end")):
        return ""
    if not as_str(pr.get("title")):
        return ""
    link = ""
    if as_str(pr.get("link")):
        link = f'<a class="promo-link" href="{escape(as_str(pr.get("link")), quote=True)}">{escape(as_str(pr.get("link_text")) or "Learn more")} &rarr;</a>'
    return (f'<div class="aside-box promo-box"><h2>{escape(as_str(pr.get("title")))}</h2>'
            f'<p>{escape(as_str(pr.get("text")))}</p>{link}</div>')


def _top_rated(products: List[Dict[str, Any]], exclude: set, tag: str = "") -> Optional[Dict[str, Any]]:
    best, best_r = None, None
    for p in products:
        if as_str(p.get("slug")) in exclude:
            continue
        if tag and tag not in [normalize_slug(t) for t in (p.get("best_for") or [])]:
            continue
        try:
            r = float(p.get("rating"))
        except (TypeError, ValueError):
            r = 0.0
        if best_r is None or r > best_r:
            best, best_r = p, r
    return best


def primary_pick(matched: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Top pick: highest-rated product tagged best-overall, else highest-rated overall."""
    return _top_rated(matched, set(), "best-overall") or _best_pick(matched)


def pick_buy_html(p: Dict[str, Any], aff: str) -> str:
    b = sale_price_bits(p)
    if not b:
        return f'<a class="pick-buy" href="{aff}" target="_blank" rel="nofollow sponsored noopener">Check price on Amazon</a>'
    was = f'<s class="pick-was">{b["was"]}</s> ' if b.get("was") else ""
    badge = f'<span class="deal-badge">{b["badge"]}</span>' if b.get("badge") else (f'<span class="deal-badge">Save {b["pct"]}</span>' if b.get("pct") else "")
    return (f'{badge}<a class="pick-buy" href="{aff}" target="_blank" rel="nofollow sponsored noopener">{was}{b["now"]} from Amazon</a>'
            f'<span class="pick-asof" title="{escape(AMAZON_PRICE_DISCLAIMER, quote=True)}">Price {b["asof"]} &middot; Details</span>')


def build_pick_cards_html(matched: List[Dict[str, Any]], noun: str, best_for: Optional[List[tuple]] = None) -> str:
    """Wirecutter-style tabbed pick cards: Top pick, Budget pick, Upgrade pick, plus 'Best for…' minis."""
    top = primary_pick(matched)
    if not top:
        return ""
    used = {as_str(top.get("slug"))}
    budget = _top_rated(matched, used, "budget")
    if budget:
        used.add(as_str(budget.get("slug")))
    upgrade = _top_rated(matched, used, "premium")
    if upgrade:
        used.add(as_str(upgrade.get("slug")))

    def nm(p):
        return as_str(p.get("name")) or as_str(p.get("slug"))
    intro = f"After comparing {len(matched)} {escape(noun)}, we recommend the <strong>{escape(nm(top))}</strong>."
    if budget:
        intro += f" If you want to spend less, the <strong>{escape(nm(budget))}</strong> is a strong value."
    if upgrade:
        intro += f" For the best of the best, consider the <strong>{escape(nm(upgrade))}</strong>."

    def card(p, tab, kicker, cls):
        slug = as_str(p.get("slug"))
        detail = escape(f"/generated/products/{slug}/", quote=True)
        aff = safe_url(as_str(p.get("affiliate_url")))
        return (f'<article class="pick-card {cls}"><span class="pick-tab">{tab}</span>'
                f'<div class="pick-media"><a href="{detail}" tabindex="-1">{cover_tile_html(p, large=True)}</a></div>'
                f'<div class="pick-body"><p class="pick-kicker">{kicker}</p>'
                f'<h3 class="pick-name"><a href="{detail}">{escape(nm(p))}</a></h3>'
                f'<p class="pick-verdict">{escape(as_str(p.get("verdict")))}</p>'
                f'{pick_buy_html(p, aff)}'
                f'<a class="pick-review" href="{detail}">Read our full review &rarr;</a></div></article>')
    parts = [f'<section class="pick-cards" id="top-pick" aria-label="Our picks"><p class="pick-intro">{intro}</p>']
    parts.append(card(top, "Top pick", "Our favorite overall", "pick-top"))
    if budget:
        parts.append(card(budget, "Budget pick", "Great value for less", "pick-budget"))
    if upgrade:
        parts.append(card(upgrade, "Upgrade pick", "Worth the splurge", "pick-upgrade"))
    minis = []
    for label, p, href in (best_for or []):
        if as_str(p.get("slug")) in used:
            continue
        used.add(as_str(p.get("slug")))
        aff = safe_url(as_str(p.get("affiliate_url")))
        minis.append(f'<div class="bestfor-card"><div class="bestfor-media">{cover_tile_html(p)}</div>'
                     f'<div class="bestfor-body"><p class="bestfor-kicker">Best for {escape(label.lower())}</p>'
                     f'<h3><a href="{escape(href, quote=True)}">{escape(nm(p))}</a></h3>'
                     f'<a class="bestfor-buy" href="{aff}" target="_blank" rel="nofollow sponsored noopener">Check price on Amazon &rarr;</a></div></div>')
        if len(minis) >= 4:
            break
    if minis:
        parts.append('<div class="bestfor-grid">' + "".join(minis) + '</div>')
    parts.append('</section>')
    return "".join(parts)


SEO_YEAR = date.today().year


def _seo_clean(name: str) -> str:
    n = re.sub(r"\s*\([^)]*\)", "", as_str(name)).strip()
    n = re.sub(r"^(the\s+)?best\s+", "", n, flags=re.I).strip()
    n = re.sub(r"\s+(of\s+)?20\d\d$", "", n).strip()
    return n


def category_seo_title(name: str, n: int, is_hub: bool, is_collection: bool, niche: str) -> str:
    core = _seo_clean(name) or slug_to_display(niche)
    if niche == "gifts" and is_hub:
        return f"Holiday Gift Guide {SEO_YEAR}: The Best Gifts for Everyone on Your List"
    if niche == "gifts" or core.lower().startswith("gift"):
        t = f"Best {core} ({SEO_YEAR}): {n} Picks They'll Actually Use" if n >= 3 else f"Best {core} ({SEO_YEAR})"
    elif is_hub:
        t = f"Best {core} of {SEO_YEAR}: {n} Top Picks Compared" if n >= 3 else f"Best {core} of {SEO_YEAR}"
    else:
        t = f"Best {core} ({SEO_YEAR}): {n} Top Picks" if n >= 3 else f"Best {core} ({SEO_YEAR})"
    return f"{t} | Halcott Magazine" if len(t) <= 58 else t


def category_seo_desc(name: str, matched: List[Dict[str, Any]], niche: str, fallback: str) -> str:
    top = primary_pick(matched) if matched else None
    if not top:
        return strip_price_text(fallback)[:158]
    used = {as_str(top.get("slug"))}
    budget = _top_rated(matched, used, "budget")
    core = _seo_clean(name).lower() or slug_to_display(niche).lower()
    if niche == "gifts" and core.startswith("holiday"):
        return "Gift ideas for everyone on your list, from stocking stuffers to splurges. Every pick is in stock with live Amazon prices, checked daily."
    if niche == "gifts":
        d = f"{len(matched)} {core} they'll actually use, all in stock with live Amazon prices checked daily. Top pick: the {as_str(top.get('name'))}."
        return d[:160]
    d = f"We compared {len(matched)} {core}. Our top pick: the {as_str(top.get('name'))}."
    if budget:
        d += f" Best value: the {as_str(budget.get('name'))}."
    d += " Live Amazon prices, checked daily."
    if len(d) > 160:
        d = f"We compared {len(matched)} {core}. Our top pick: the {as_str(top.get('name'))}. Live Amazon prices, checked daily."
    return d[:160]


def render_category_page(template: str, name: str, description: str, product_list_html: str) -> str:
    return template.replace("{{CATEGORY_NAME}}", escape(name)).replace("{{CATEGORY_DESCRIPTION}}", escape(description)).replace("{{PRODUCT_LIST}}", product_list_html)


def specs_table_html(specs: Dict[str, Any]) -> str:
    specs = require_dict(specs, "specs")
    if not specs:
        return "<p>No specs available.</p>"
    rows = []
    for k in sorted(specs.keys(), key=lambda x: str(x).lower()):
        rows.append(f"<tr><th>{escape(str(k))}</th><td>{escape(str(specs[k]))}</td></tr>")
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def best_for_html(best_for: Any) -> str:
    tags = require_list(best_for, "best_for")
    tags = [as_str(t) for t in tags if as_str(t)]
    if not tags:
        return "<p>—</p>"
    return "<ul>" + "".join(f"<li>{escape(slug_to_display(t))}</li>" for t in tags) + "</ul>"


def features_html(features: Any) -> str:
    items = require_list(features, "features")
    items = [as_str(f) for f in items if as_str(f)]
    if not items:
        return ""
    return "<ul>" + "".join(f"<li>{escape(f)}</li>" for f in items) + "</ul>"


def pros_cons_html(pros: Any, cons: Any) -> str:
    pro_list = [as_str(p) for p in require_list(pros, "pros") if as_str(p)]
    con_list = [as_str(c) for c in require_list(cons, "cons") if as_str(c)]
    if not pro_list and not con_list:
        return ""
    parts = []
    parts.append('<div class="pros-cons">')
    if pro_list:
        parts.append('<div class="pros"><h3>What we like</h3><ul>')
        parts.extend(f"<li>{escape(p)}</li>" for p in pro_list)
        parts.append('</ul></div>')
    if con_list:
        parts.append('<div class="cons"><h3>Worth noting</h3><ul>')
        parts.extend(f"<li>{escape(c)}</li>" for c in con_list)
        parts.append('</ul></div>')
    parts.append('</div>')
    return "\n".join(parts)


def safe_url(url: str) -> str:
    url = as_str(url)
    if not url:
        return "#"
    return escape(url, quote=True)


# --- Image-free "cover tile" treatment ---------------------------------------
# Product data has no images yet, so instead of blank space we render a branded
# monogram tile with a deterministic accent color. Reads as an intentional
# design choice rather than a missing image, at zero cost/legal risk.
COVER_PALETTE = [
    "#33658a", "#55828b", "#3b6064", "#2f4858", "#5e503f",
    "#6b4e71", "#4a5759", "#7d4f50", "#3d5a45", "#8a6d3b",
]


def _accent_for(key: str) -> str:
    key = key or "x"
    h = 0
    for ch in key:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return COVER_PALETTE[h % len(COVER_PALETTE)]


def cover_tile_html(p: Dict[str, Any], large: bool = False) -> str:
    brand = as_str(p.get("brand"))
    name = as_str(p.get("name"))
    label = brand or name or "?"
    initial = escape(label[:1].upper())
    accent = _accent_for(brand or as_str(p.get("slug")) or name)
    cls = "cover-tile cover-tile-lg" if large else "cover-tile"
    img = as_str(p.get("image_url"))
    if img:
        return (f'<div class="{cls} cover-tile-img">'
                f'<img src="{escape(img, quote=True)}" alt="{escape(name)}" loading="lazy" /></div>')
    return (f'<div class="{cls}" style="--cover-accent:{accent}" aria-hidden="true">'
            f'<span class="cover-mono">{initial}</span></div>')


UPDATED_LABEL = date.today().strftime("%B %Y")


def stars_html(rating: Any) -> str:
    try:
        rv = float(rating)
    except (TypeError, ValueError):
        return ""
    pct = max(0.0, min(100.0, rv / 5.0 * 100.0))
    stars = "\u2605\u2605\u2605\u2605\u2605"
    return ('<span class="star-rating" role="img" aria-label="' + escape(str(rating))
            + ' out of 5 stars"><span class="star-rating-base">' + stars + '</span>'
            + '<span class="star-rating-fill" style="width:' + f"{pct:.0f}" + '%">' + stars
            + '</span></span>')


BADGE_CLASS = {"Editor's Choice": "badge-editor", "Best Value": "badge-value", "Premium Pick": "badge-premium"}


def badge_html(badge: Any) -> str:
    b = as_str(badge)
    if not b:
        return ""
    return f'<span class="product-badge {BADGE_CLASS.get(b, "badge-generic")}">{escape(b)}</span>'


def price_as_of_text(as_of: Any) -> str:
    try:
        from datetime import datetime as _dt
        d = _dt.fromisoformat(as_str(as_of).replace("Z", "+00:00"))
        return d.strftime("as of %b %-d, %Y %H:%M UTC")
    except Exception:
        return "as of today"


AMAZON_PRICE_DISCLAIMER = ("Product prices and availability are accurate as of the date/time indicated and are subject to change. "
                           "Any price and availability information displayed on Amazon at the time of purchase will apply to the purchase of this product.")


def sale_price_bits(p: Dict[str, Any]) -> Dict[str, str]:
    """Escaped price pieces for one product (API prices only)."""
    price = p.get("price_usd")
    if price in (None, ""):
        return {}
    def fmt(v):
        try:
            f = float(v)
            return f"${f:,.0f}" if f == int(f) else f"${f:,.2f}"
        except (TypeError, ValueError):
            return f"${v}"
    out = {"now": escape(fmt(price)), "asof": escape(price_as_of_text(p.get("price_as_of")))}
    if p.get("was_price_usd") not in (None, ""):
        out["was"] = escape(fmt(p.get("was_price_usd")))
        try:
            pct = int(round(float(p.get("savings_percent"))))
            if pct > 0:
                out["pct"] = f"{pct}%"
        except (TypeError, ValueError):
            pass
    if as_str(p.get("deal_badge")):
        out["badge"] = escape(as_str(p.get("deal_badge")))
    return out


def home_price_chip(p: Dict[str, Any]) -> str:
    """Small price chip for photo tiles: live Amazon price only, timestamp + disclaimer on hover."""
    b = sale_price_bits(p)
    if not b:
        return ""
    tip = escape(f"Price {b['asof']}. {AMAZON_PRICE_DISCLAIMER}", quote=True)
    was = f'<s>{b["was"]}</s> ' if b.get("was") else ""
    deal = f'<span class="pc-deal">{b["badge"] if b.get("badge") else "Save " + b["pct"]}</span>' if (b.get("badge") or b.get("pct")) else ""
    return f'<span class="price-chip" title="{tip}">{deal}<span class="pc-amt">{was}{b["now"]}</span></span>'


def price_display_html(price: Any, as_of: Any = None) -> str:
    if price is None or as_str(price) == "":
        return '<span class="price-note">See Amazon for current price</span>'
    return (f'<span class="price-amount">${escape(str(price))}</span>'
            f'<span class="price-note" title="{escape(AMAZON_PRICE_DISCLAIMER, quote=True)}">{escape(price_as_of_text(as_of))} &middot; Details</span>')


def product_price_html(p: Dict[str, Any]) -> str:
    b = sale_price_bits(p)
    if not b:
        return price_display_html(None)
    was = f'<s class="price-was">{b["was"]}</s>' if b.get("was") else ""
    save = f'<span class="deal-badge">{b.get("badge") or ("Save " + b["pct"])}</span>' if (b.get("badge") or b.get("pct")) else ""
    return (f'{save}<span class="price-amount">{was} {b["now"]}</span>'
            f'<span class="price-note" title="{escape(AMAZON_PRICE_DISCLAIMER, quote=True)}">{b["asof"]} &middot; Details</span>')


def price_inline_text(price: Any, as_of: Any = None) -> str:
    if price is None or as_str(price) == "":
        return ""
    return f'${escape(str(price))} {escape(price_as_of_text(as_of))}'


def key_specs_grid(specs: Any) -> str:
    specs = require_dict(specs, "specs")
    items = [(k, v) for k, v in specs.items() if as_str(v)]
    if not items:
        return '<p class="specs-empty">Full specifications below.</p>'
    cells = []
    for k, v in items[:6]:
        label = escape(str(k).replace("_", " ").title())
        cells.append(f'<div class="spec-cell"><span class="spec-label">{label}</span><span class="spec-value">{escape(str(v))}</span></div>')
    return '<div class="spec-grid">' + "".join(cells) + "</div>"


def product_gallery_html(p: Dict[str, Any]) -> str:
    """Large main photo plus clickable thumbnails (Amazon API images only)."""
    gal = [u for u in (p.get("gallery") or []) if u]
    main = as_str(p.get("image_hi")) or (gal[0] if gal else as_str(p.get("image_url")))
    if not main:
        return f'<div class="pg-main pg-empty">{cover_tile_html(p, large=True)}</div>'
    name = escape(as_str(p.get("name")))
    out = [f'<div class="pg-main"><img id="pg-main-img" src="{escape(main, quote=True)}" alt="{name}" /></div>']
    if len(gal) > 1:
        thumbs = "".join(
            f'<button type="button" class="pg-thumb{" is-active" if i == 0 else ""}" data-src="{escape(u, quote=True)}" aria-label="Photo {i + 1}">'
            f'<img src="{escape(u, quote=True)}" alt="" loading="lazy" /></button>' for i, u in enumerate(gal))
        out.append(f'<div class="pg-thumbs">{thumbs}</div>')
        out.append('<script>document.addEventListener("click",function(e){var b=e.target.closest(".pg-thumb");if(!b)return;'
                   'document.getElementById("pg-main-img").src=b.getAttribute("data-src");'
                   'document.querySelectorAll(".pg-thumb").forEach(function(t){t.classList.toggle("is-active",t===b)});});</script>')
    return "".join(out)


def review_figure_html(p: Dict[str, Any]) -> str:
    """A second Amazon photo beside the review text (a different angle from the hero)."""
    gal = [u for u in (p.get("gallery") or []) if real_image(u)]
    if len(gal) < 2:
        return ""
    return (f'<figure class="review-fig"><img src="{escape(gal[1], quote=True)}" '
            f'alt="{escape(as_str(p.get("name")), quote=True)}" loading="lazy" /></figure>')


def buy_label_html(p: Dict[str, Any]) -> str:
    b = sale_price_bits(p)
    if not b:
        return "Check Price on Amazon &rarr;"
    was = f'<s class="btn-was">{b["was"]}</s> ' if b.get("was") else ""
    return f'{was}{b["now"]} on Amazon &rarr;'


def alternatives_html(p: Dict[str, Any], pool: List[Dict[str, Any]], limit: int = 4) -> str:
    """Photo cards for other picks in the same department."""
    me = as_str(p.get("slug"))
    others = [q for q in pool if as_str(q.get("slug")) != me]
    others.sort(key=lambda q: -(float(q.get("rating")) if isinstance(q.get("rating"), (int, float)) else 0))
    cards = []
    for q in others[:limit]:
        href = escape(f"/generated/products/{as_str(q.get('slug'))}/", quote=True)
        b = sale_price_bits(q)
        price = (f'<p class="alt-price">{("<s>" + b["was"] + "</s> ") if b.get("was") else ""}{b["now"]}</p>' if b else "")
        cards.append(f'<a class="alt-card" href="{href}"><div class="alt-media">{cover_tile_html(q)}</div>'
                     f'<p class="alt-brand">{escape(as_str(q.get("brand")))}</p><h3>{escape(as_str(q.get("name")))}</h3>{price}</a>')
    return '<div class="alt-grid">' + "".join(cards) + '</div>' if cards else ""


def render_product_page(template: str, p: Dict[str, Any]) -> str:
    name = as_str(p.get("name"))
    brand = as_str(p.get("brand"))
    description = as_str(p.get("description"))
    price = p.get("price_usd")
    rating = p.get("rating")
    verdict = as_str(p.get("verdict"))
    affiliate_url = as_str(p.get("affiliate_url"))
    img_url = as_str(p.get("image_url"))
    best_for = p.get("best_for") or []
    specs = p.get("specs") or {}
    features = p.get("features") or []
    pros = p.get("pros") or []
    cons = p.get("cons") or []
    primary_category_url = as_str(p.get("primary_category_url"))
    primary_category_name = as_str(p.get("primary_category_name"))
    sidebar_links = as_str(p.get("sidebar_links"))
    reviewer_name = as_str(p.get("reviewer_name"))
    reviewer_role = as_str(p.get("reviewer_role"))
    reviewer_bio = as_str(p.get("reviewer_bio"))
    site_name = as_str(p.get("site_name"))
    site_tagline = as_str(p.get("site_tagline"))
    price_txt = "" if price is None else str(price)
    rating_txt = "" if rating is None else str(rating)
    return (template
        .replace("{{PRODUCT_NAME}}", escape(name))
        .replace("{{PRODUCT_BRAND}}", escape(brand))
        .replace("{{PRODUCT_DESCRIPTION}}", escape(description))
        .replace("{{PRODUCT_PRICE}}", escape(price_txt))
        .replace("{{PRODUCT_RATING}}", escape(rating_txt))
        .replace("{{PRODUCT_VERDICT}}", escape(verdict))
        .replace("{{AFFILIATE_URL}}", safe_url(affiliate_url))
        .replace("{{IMAGE_URL}}", safe_url(img_url))
        .replace("{{BEST_FOR}}", best_for_html(best_for))
        .replace("{{SPECS_TABLE}}", specs_table_html(specs))
        .replace("{{FEATURES}}", features_html(features))
        .replace("{{PROS_CONS}}", pros_cons_html(pros, cons))
        .replace("{{PRIMARY_CATEGORY_URL}}", escape(primary_category_url))
        .replace("{{PRIMARY_CATEGORY_NAME}}", escape(primary_category_name))
        .replace("{{SIDEBAR_LINKS}}", sidebar_links)
        .replace("{{REVIEWER_NAME}}", escape(reviewer_name))
        .replace("{{REVIEWER_ROLE}}", escape(reviewer_role))
        .replace("{{REVIEWER_BIO}}", escape(reviewer_bio))
        .replace("{{SITE_NAME}}", escape(site_name))
        .replace("{{SITE_TAGLINE}}", escape(site_tagline))
        .replace("{{HEAD_EXTRA}}", as_str(p.get("head_extra")))
        .replace("{{COVER_TILE}}", product_gallery_html(p))
        .replace("{{BUY_LABEL}}", buy_label_html(p))
        .replace("{{REVIEW_FIGURE}}", review_figure_html(p))
        .replace("{{ALTERNATIVES}}", as_str(p.get("alternatives_html")))
        .replace("{{STARS}}", stars_html(p.get("rating")))
        .replace("{{BADGE}}", badge_html(p.get("badge")))
        .replace("{{KEY_SPECS_GRID}}", key_specs_grid(p.get("specs") or {}))
        .replace("{{PRICE_DISPLAY}}", product_price_html(p))
        .replace("{{PRICE_INLINE}}", price_inline_text(p.get("price_usd"), p.get("price_as_of")))
        .replace("{{UPDATED}}", UPDATED_LABEL))


DEFAULT_PRODUCT_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8" /><title>{{PRODUCT_NAME}} | {{SITE_NAME}}</title></head>
<body><h1>{{PRODUCT_NAME}}</h1>
<p><strong>Brand:</strong> {{PRODUCT_BRAND}}</p>
<p><strong>Price (USD):</strong> {{PRODUCT_PRICE}}</p>
<p><strong>Rating:</strong> {{PRODUCT_RATING}}/5</p>
<p><em>{{PRODUCT_VERDICT}}</em></p>
<p><a class="cta" href="{{AFFILIATE_URL}}" rel="nofollow sponsored noopener" target="_blank">Buy / Check Price</a></p>
<p><a class="secondary" href="{{PRIMARY_CATEGORY_URL}}">Browse {{PRIMARY_CATEGORY_NAME}}</a></p>
<div class="product-description">{{PRODUCT_DESCRIPTION}}</div>
<h2>Key Features</h2><div>{{FEATURES}}</div>
{{PROS_CONS}}
<h2>Best For</h2><div>{{BEST_FOR}}</div>
<h2>Specs</h2><div>{{SPECS_TABLE}}</div>
<div class="reviewer-byline"><p>Reviewed by <strong>{{REVIEWER_NAME}}</strong>, {{REVIEWER_ROLE}}</p></div>
<h2>Related Categories</h2><ul>{{SIDEBAR_LINKS}}</ul>
</body></html>"""

DEFAULT_INDEX_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8" /><title>{{SITE_NAME}}</title></head>
<body><h1>{{SITE_NAME}}</h1><p>{{SITE_TAGLINE}}</p>
<h2>Meet Our Team</h2>{{TEAM_SECTION}}
{{NICHE_SECTIONS}}<h2>All Categories</h2>{{ALL_CATEGORIES_LIST}}</body></html>"""


def product_matches(product: Dict[str, Any], match_tags: List[str]) -> bool:
    best_for = product.get("best_for") or []
    if not isinstance(best_for, list):
        return False
    best_for_norm = [normalize_slug(t) for t in best_for if as_str(t)]
    return any(t in best_for_norm for t in match_tags)


def build_category_product_list(matched: List[Dict[str, Any]]) -> str:
    if not matched:
        return "<p>No products yet.</p>"
    cards = []
    for p in matched:
        slug = as_str(p.get("slug"))
        name = as_str(p.get("name")) or slug
        brand = as_str(p.get("brand"))
        price = p.get("price_usd")
        rating = p.get("rating")
        verdict = as_str(p.get("verdict"))
        affiliate_url = as_str(p.get("affiliate_url"))
        specs = p.get("specs") or {}
        meta_parts = []
        if brand:
            meta_parts.append(f"<strong>Brand:</strong> {escape(brand)}")
        if price is not None:
            meta_parts.append(f"<strong>Price:</strong> " + (f"<s>{sale_price_bits(p).get('was')}</s> " if sale_price_bits(p).get('was') else "") + f"{sale_price_bits(p).get('now')} <span class=\"price-asof\" title=\"{escape(AMAZON_PRICE_DISCLAIMER, quote=True)}\">({escape(price_as_of_text(p.get('price_as_of')))})</span>")
        meta_html = " &nbsp;•&nbsp; ".join(meta_parts) if meta_parts else ""
        spec_bits = []
        for sk, sv in specs.items():
            if sv and sk != "warranty_years":
                spec_bits.append(f"<span>{escape(str(sk).replace('_', ' ').title())}: {escape(str(sv))}</span>")
            if len(spec_bits) >= 4:
                break
        specs_html = " ".join(spec_bits)
        aff_href = safe_url(affiliate_url)
        detail_href = escape(f"/generated/products/{slug}/", quote=True)
        name_href = escape(f"/generated/products/{slug}/", quote=True)
        card = f'<div class="product-card" id="p-{escape(slug, quote=True)}">'
        card += cover_tile_html(p)
        card += f'<h3><a href="{name_href}">{escape(name)}</a></h3>'
        if rating is not None:
            card += f'<div class="card-rating">{stars_html(rating)}<span class="card-rating-num">{escape(str(rating))}/5</span></div>' 
        if verdict:
            card += f'<p class="card-verdict"><em>{escape(verdict)}</em></p>'
        if meta_html:
            card += f'<p class="card-meta">{meta_html}</p>'
        if specs_html:
            card += f'<div class="card-specs">{specs_html}</div>'
        card += f'<div class="card-actions">'
        card += f'<a class="cta" href="{aff_href}" target="_blank" rel="nofollow sponsored noopener">Buy / Check Price</a>'
        card += f'<a class="secondary" href="{detail_href}">Read Full Review</a>'
        card += f'</div></div>'
        cards.append(card)
    html = "\n".join(cards)
    if matched and "product-card" not in html:
        raise RuntimeError("Category product list rendered without product cards.")
    return html


def _best_pick(matched: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    best = None
    best_rating = None
    for p in matched:
        try:
            rv = float(p.get("rating"))
        except (TypeError, ValueError):
            continue
        if best_rating is None or rv > best_rating:
            best_rating, best = rv, p
    return best


def build_top_pick_html(matched: List[Dict[str, Any]]) -> str:
    p = _best_pick(matched)
    if not p:
        return ""
    slug = as_str(p.get("slug"))
    name = as_str(p.get("name")) or slug
    brand = as_str(p.get("brand"))
    verdict = as_str(p.get("verdict"))
    rating = p.get("rating")
    price = p.get("price_usd")
    aff_href = safe_url(as_str(p.get("affiliate_url")))
    detail_href = escape(f"/generated/products/{slug}/", quote=True)
    meta = []
    if brand:
        meta.append(f"<strong>Brand:</strong> {escape(brand)}")
    if price is not None:
        meta.append(f"<strong>Price:</strong> " + (f"<s>{sale_price_bits(p).get('was')}</s> " if sale_price_bits(p).get('was') else "") + f"{sale_price_bits(p).get('now')} <span class=\"price-asof\" title=\"{escape(AMAZON_PRICE_DISCLAIMER, quote=True)}\">({escape(price_as_of_text(p.get('price_as_of')))})</span>")
    meta_html = " &nbsp;\u2022&nbsp; ".join(meta)
    parts = ['<aside class="top-pick" id="top-pick" aria-label="Top pick">']
    parts.append('<div class="top-pick-badge">\u2605 Our Top Pick</div>')
    parts.append('<div class="top-pick-body">')
    parts.append(cover_tile_html(p))
    parts.append('<div class="top-pick-info">')
    parts.append(f'<h2 class="top-pick-name"><a href="{detail_href}">{escape(name)}</a></h2>')
    if rating is not None:
        parts.append(f'<div class="top-pick-rating">{stars_html(rating)}<span class="card-rating-num">{escape(str(rating))}/5</span></div>')
    if verdict:
        parts.append(f'<p class="top-pick-verdict">{escape(verdict)}</p>')
    if meta_html:
        parts.append(f'<p class="top-pick-meta">{meta_html}</p>')
    parts.append('<div class="top-pick-actions">')
    parts.append(f'<a class="cta" href="{aff_href}" target="_blank" rel="nofollow sponsored noopener">Check Price</a>')
    parts.append(f'<a class="secondary" href="{detail_href}">Read Full Review</a>')
    parts.append('</div></div></div></aside>')
    return "".join(parts)


def build_category_faqs(name: str, matched: List[Dict[str, Any]], reviewer_name: str, reviewer_role: str) -> List[tuple]:
    n = len(matched)
    if not n:
        return []
    year = date.today().year
    low = name.lower()
    best = _best_pick(matched)
    top_name = as_str(best.get("name")) if best else ""
    faqs = []
    a1 = f"Our editors compared {n} {low} on real-world performance, specifications, build quality, and value to choose the models featured in this guide"
    a1 += f", with the {top_name} earning our top pick." if top_name else "."
    faqs.append((f"How did we choose the best {low}?", a1))
    faqs.append((f"How many {low} are compared in this guide?",
                 f"This guide compares {n} {low} side by side, updated for {year}."))
    if reviewer_name:
        role = f", {reviewer_role}," if reviewer_role else ""
        faqs.append((f"Who reviews the {low} featured here?",
                     f"{reviewer_name}{role} leads our coverage in this category."))
    faqs.append(("Do you earn a commission from these picks?",
                 "As an Amazon Associate we earn from qualifying purchases, and we may earn from "
                 "other affiliate programs. This never influences our recommendations \u2014 our "
                 "editorial team evaluates every product independently."))
    return faqs


def build_faq_section_html(faqs: List[tuple]) -> str:
    if not faqs:
        return ""
    parts = ['<section class="faq-section" id="faq" aria-label="Frequently asked questions">']
    parts.append("<h2>Frequently Asked Questions</h2>")
    for q, a in faqs:
        parts.append('<div class="faq-item">')
        parts.append(f'<h3 class="faq-q">{escape(q)}</h3>')
        parts.append(f'<p class="faq-a">{escape(a)}</p>')
        parts.append("</div>")
    parts.append("</section>")
    return "".join(parts)


def build_faq_ld(faqs: List[tuple]) -> Dict[str, Any]:
    return {"@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": q,
                            "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faqs]}


GUIDE_URL_BASE = "/generated/guides"


def load_guides() -> List[Dict[str, Any]]:
    if not GUIDES_DIR.exists():
        return []
    out = []
    for f in sorted(GUIDES_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        slug = normalize_slug(d.get("slug"))
        if not slug:
            continue
        faqs = []
        for x in (d.get("faqs") or []):
            if isinstance(x, (list, tuple)) and len(x) >= 2:
                faqs.append([as_str(x[0]), as_str(x[1])])
        out.append({"slug": slug, "title": as_str(d.get("title")) or slug,
                    "description": as_str(d.get("description")), "niche": normalize_slug(d.get("niche")),
                    "intro": as_str(d.get("intro")), "sections": d.get("sections") or [], "faqs": faqs})
    return out


def guide_section_anchor(heading: str, idx: int) -> str:
    base = normalize_slug(heading) or f"section-{idx + 1}"
    return f"s-{base}"


def guide_body_html(sections: Any) -> str:
    parts = []
    for i, s in enumerate(sections or []):
        if not isinstance(s, dict):
            continue
        h = as_str(s.get("heading"))
        b = as_str(s.get("body"))
        paras = "".join(f"<p>{escape(p.strip())}</p>" for p in b.split("\n\n") if p.strip())
        anchor = guide_section_anchor(h, i)
        parts.append(f'<section class="guide-section" id="{escape(anchor, quote=True)}"><h2>{escape(h)}</h2>{paras}</section>')
    return "".join(parts)


def build_toc_nav(items: List[tuple]) -> str:
    """Render a 'Contents' dropdown for the header nav. items = [(anchor, label), ...]."""
    if not items:
        return ""
    lis = "".join(
        f'<li><a href="#{escape(anchor, quote=True)}">{escape(label)}</a></li>'
        for anchor, label in items
    )
    return ('<div class="nav-contents">'
            '<button type="button" class="nav-contents-btn" aria-haspopup="true" aria-label="Jump to a section on this page">'
            'Contents <span class="nav-caret" aria-hidden="true">&#9662;</span></button>'
            f'<ul class="nav-dropdown">{lis}</ul></div>')


def build_guide_toc_items(guide: Dict[str, Any], has_faq: bool) -> List[tuple]:
    items: List[tuple] = []
    for i, s in enumerate(guide.get("sections") or []):
        if not isinstance(s, dict):
            continue
        h = as_str(s.get("heading"))
        if not h:
            continue
        items.append((guide_section_anchor(h, i), h))
    if has_faq:
        items.append(("faq", "Frequently Asked Questions"))
    return items


def build_category_toc_items(matched: List[Dict[str, Any]], has_faq: bool) -> List[tuple]:
    items: List[tuple] = []
    if _best_pick(matched):
        items.append(("top-pick", "★ Our Top Pick"))
    for p in matched:
        pslug = as_str(p.get("slug"))
        if not pslug:
            continue
        pname = as_str(p.get("name")) or pslug
        items.append((f"p-{pslug}", pname))
    if has_faq:
        items.append(("faq", "Frequently Asked Questions"))
    return items


def build_guide_head_extra(guide: Dict[str, Any], site_url: str, site_name: str, faqs=None) -> str:
    slug = as_str(guide.get("slug"))
    title = as_str(guide.get("title"))
    desc = as_str(guide.get("description"))
    canonical = f"{site_url}{GUIDE_URL_BASE}/{slug}/" if slug else f"{site_url}{GUIDE_URL_BASE}/"
    article_ld = {"@context": "https://schema.org", "@type": "Article", "headline": title,
                  "description": desc, "author": {"@type": "Organization", "name": site_name},
                  "publisher": {"@type": "Organization", "name": site_name}, "mainEntityOfPage": canonical}
    breadcrumb_ld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": site_url + "/"},
        {"@type": "ListItem", "position": 2, "name": "Buying Guides", "item": f"{site_url}{GUIDE_URL_BASE}/"},
        {"@type": "ListItem", "position": 3, "name": title, "item": canonical}]}
    parts = list(FAVICON_LINKS) + _og_meta(title, desc, canonical, site_name, og_type="article")
    parts.append(_jsonld_script(article_ld))
    parts.append(_jsonld_script(breadcrumb_ld))
    if faqs:
        parts.append(_jsonld_script(build_faq_ld(faqs)))
    return "\n    ".join(parts)


def render_guide_page(template: str, guide: Dict[str, Any], site_name: str, site_tagline: str,
                      related_cta: str, faq_html: str, head_extra: str, body_override=None,
                      toc_nav: str = "", section_bar: str = "", share_row: str = "", hero_media: str = "") -> str:
    body = body_override if body_override is not None else guide_body_html(guide.get("sections"))
    return (template
        .replace("{{SECTION_BAR}}", section_bar)
        .replace("{{GUIDE_HERO_MEDIA}}", hero_media)
        .replace("{{GUIDE_HERO_CLASS}}", " has-media" if hero_media else "")
        .replace("{{SHARE_ROW}}", share_row)
        .replace("{{TOC_NAV}}", toc_nav)
        .replace("{{GUIDE_TITLE}}", escape(as_str(guide.get("title"))))
        .replace("{{GUIDE_DESCRIPTION}}", escape(as_str(guide.get("description"))))
        .replace("{{GUIDE_INTRO}}", escape(as_str(guide.get("intro"))))
        .replace("{{GUIDE_BODY}}", body)
        .replace("{{RELATED_CTA}}", related_cta or "")
        .replace("{{FAQ_SECTION}}", faq_html or "")
        .replace("{{HEAD_EXTRA}}", head_extra or "")
        .replace("{{UPDATED}}", UPDATED_LABEL)
        .replace("{{SITE_NAME}}", escape(site_name))
        .replace("{{SITE_TAGLINE}}", escape(site_tagline)))


def get_site_url() -> str:
    return (os.environ.get("SITE_URL") or DEFAULT_SITE_URL).strip().rstrip("/")


def build_sitemap(urls: List[str]) -> str:
    today = date.today().isoformat()
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        parts.append("  <url>")
        parts.append(f"    <loc>{escape(u)}</loc>")
        parts.append(f"    <lastmod>{today}</lastmod>")
        parts.append("  </url>")
    parts.append("</urlset>")
    parts.append("")
    return "\n".join(parts)


def build_robots(site_url: str) -> str:
    return "\n".join(["User-agent: *", "Allow: /", f"Sitemap: {site_url}/sitemap.xml", ""])


def run_quality_gates():
    failures: List[str] = []
    product_pages = sorted(GENERATED_DIR.glob("products/*/index.html"))
    category_pages = sorted(GENERATED_DIR.glob("categories/*/index.html"))
    for page in product_pages:
        rel = page.relative_to(GENERATED_DIR)
        content = page.read_text(encoding="utf-8")
        if "<html" not in content.lower():
            failures.append(f"[MISSING <html>] {rel}")
        if "<title>" not in content.lower():
            failures.append(f"[MISSING <title>] {rel}")
        if "href=" not in content.lower():
            failures.append(f"[MISSING href=] {rel}")
        if "{{" in content and "}}" in content:
            failures.append(f"[UNREPLACED PLACEHOLDER] {rel}")
    for page in category_pages:
        rel = page.relative_to(GENERATED_DIR)
        content = page.read_text(encoding="utf-8")
        if "<html" not in content.lower():
            failures.append(f"[MISSING <html>] {rel}")
        if "<title>" not in content.lower():
            failures.append(f"[MISSING <title>] {rel}")
        if "<a href=" not in content.lower():
            failures.append(f"[MISSING <a href=] {rel}")
        if "{{" in content and "}}" in content:
            failures.append(f"[UNREPLACED PLACEHOLDER] {rel}")
    if INDEX_HTML.exists():
        hp = INDEX_HTML.read_text(encoding="utf-8")
        if "<html" not in hp.lower():
            failures.append("[MISSING <html>] index.html")
        if "{{" in hp and "}}" in hp:
            failures.append("[UNREPLACED PLACEHOLDER] index.html")
    if failures:
        print("\nQUALITY GATE FAILURES:\n")
        for f in failures:
            print(f"   - {f}")
        print(f"\n   {len(failures)} failure(s). Build halted.\n")
        sys.exit(1)
    total = len(product_pages) + len(category_pages)
    print(f"Quality gates passed ({len(product_pages)} product, {len(category_pages)} category pages, homepage checked).")


PRODUCT_FILENAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.json$")


def validate_product_filenames():
    """Halt the build if any file in data/products/ breaks the naming
    convention (lowercase, hyphen-separated, .json). Prevents a mis-cased or
    mis-extensioned file from being silently skipped by the *.json glob, which
    would drop an entire niche from the site with no error."""
    if not PRODUCTS_DIR.exists():
        return
    offenders = [f.name for f in sorted(PRODUCTS_DIR.iterdir())
                 if f.is_file() and f.name != ".gitkeep"
                 and not PRODUCT_FILENAME_RE.match(f.name)]
    if offenders:
        print("")
        print("FILENAME CONVENTION FAILURES in data/products/:")
        for name in offenders:
            print("   - " + name + "  (expected lowercase, hyphen-separated, .json)")
        print("")
        print(str(len(offenders)) + " file(s) violate the convention. Build halted.")
        sys.exit(1)
    print("Product filenames OK (" + str(len(list(PRODUCTS_DIR.glob("*.json")))) + " file(s)).")


FAVICON_LINKS = [
    '<link rel="icon" href="/favicon.svg" type="image/svg+xml" />',
    '<link rel="icon" href="/favicon.ico" sizes="any" />',
    '<link rel="apple-touch-icon" href="/apple-touch-icon.png" />',
]


def _jsonld_script(obj: Dict[str, Any]) -> str:
    payload = json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")
    return '<script type="application/ld+json">' + payload + "</script>"


def _og_meta(title: str, desc: str, url: str, site_name: str, og_type: str = "website", image: str = "") -> List[str]:
    parts = [
        f'<link rel="canonical" href="{escape(url, quote=True)}" />',
        f'<meta property="og:type" content="{escape(og_type, quote=True)}" />',
        f'<meta property="og:title" content="{escape(title, quote=True)}" />',
        f'<meta property="og:description" content="{escape(desc, quote=True)}" />',
        f'<meta property="og:url" content="{escape(url, quote=True)}" />',
        f'<meta property="og:site_name" content="{escape(site_name, quote=True)}" />',
        '<meta name="twitter:card" content="summary_large_image" />',
        f'<meta name="twitter:title" content="{escape(title, quote=True)}" />',
        f'<meta name="twitter:description" content="{escape(desc, quote=True)}" />',
    ]
    if image:
        parts.append(f'<meta property="og:image" content="{escape(image, quote=True)}" />')
        parts.append(f'<meta name="twitter:image" content="{escape(image, quote=True)}" />')
    return parts


def build_product_head_extra(p: Dict[str, Any], site_url: str, site_name: str) -> str:
    slug = as_str(p.get("slug"))
    name = as_str(p.get("name"))
    verdict = as_str(p.get("verdict"))
    desc = as_str(p.get("description")) or verdict
    brand = as_str(p.get("brand"))
    rating = p.get("rating")
    price = p.get("price_usd")
    image = as_str(p.get("image_url"))
    reviewer = as_str(p.get("reviewer_name")) or site_name
    canonical = f"{site_url}/generated/products/{slug}/"
    cat_name = as_str(p.get("primary_category_name"))
    cat_url = site_url + as_str(p.get("primary_category_url"))

    product_ld: Dict[str, Any] = {"@context": "https://schema.org", "@type": "Product", "name": name}
    if desc:
        product_ld["description"] = desc
    if brand:
        product_ld["brand"] = {"@type": "Brand", "name": brand}
    if image:
        product_ld["image"] = image
    if rating is not None:
        review = {
            "@type": "Review",
            "reviewRating": {"@type": "Rating", "ratingValue": rating, "bestRating": 5, "worstRating": 1},
            "author": {"@type": "Person", "name": reviewer},
            "publisher": {"@type": "Organization", "name": site_name},
        }
        if verdict:
            review["reviewBody"] = verdict
        product_ld["review"] = review
    if price is not None:
        product_ld["offers"] = {"@type": "Offer", "priceCurrency": "USD", "price": price,
                                "availability": "https://schema.org/InStock", "url": canonical}

    breadcrumb_ld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": site_url + "/"},
        {"@type": "ListItem", "position": 2, "name": cat_name, "item": cat_url},
        {"@type": "ListItem", "position": 3, "name": name, "item": canonical},
    ]}

    parts = list(FAVICON_LINKS) + _og_meta(f"{name} Review", verdict or desc, canonical, site_name, og_type="article", image=image)
    parts.append(_jsonld_script(product_ld))
    parts.append(_jsonld_script(breadcrumb_ld))
    return "\n    ".join(parts)


def build_category_head_extra(cat: Dict[str, Any], matched: List[Dict[str, Any]], site_url: str, site_name: str, faqs=None) -> str:
    cslug = as_str(cat.get("slug"))
    name = as_str(cat.get("name"))
    desc = as_str(cat.get("description"))
    canonical = f"{site_url}/generated/categories/{cslug}/"
    items = []
    for i, p in enumerate(matched, start=1):
        items.append({"@type": "ListItem", "position": i,
                      "url": f"{site_url}/generated/products/{as_str(p.get('slug'))}/",
                      "name": as_str(p.get("name"))})
    itemlist_ld = {"@context": "https://schema.org", "@type": "ItemList", "name": name, "itemListElement": items}
    breadcrumb_ld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": site_url + "/"},
        {"@type": "ListItem", "position": 2, "name": name, "item": canonical},
    ]}
    parts = list(FAVICON_LINKS) + _og_meta(name, desc, canonical, site_name, og_type="website")
    parts.append(_jsonld_script(itemlist_ld))
    parts.append(_jsonld_script(breadcrumb_ld))
    if faqs:
        parts.append(_jsonld_script(build_faq_ld(faqs)))
    return "\n    ".join(parts)


def build_home_head_extra(site_config: Dict[str, Any], site_url: str, site_name: str) -> str:
    desc = as_str(site_config.get("description"))
    org_ld = {"@context": "https://schema.org", "@type": "Organization", "name": site_name, "url": site_url + "/"}
    website_ld = {"@context": "https://schema.org", "@type": "WebSite", "name": site_name, "url": site_url + "/"}
    parts = list(FAVICON_LINKS) + _og_meta(site_name, desc, site_url + "/", site_name, og_type="website")
    parts.append(_jsonld_script(org_ld))
    parts.append(_jsonld_script(website_ld))
    return "\n    ".join(parts)


_PRICE_NUM=r"~?\$(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s?[kK])?\+?"
def strip_price_text(t: str) -> str:
    """Remove hard-coded dollar amounts from product prose (Amazon only allows API-sourced, current prices)."""
    if "$" not in t: return t
    o=t
    t=re.sub(r"\s*\((?:[^()]*\$\d[^()]*)\)","",t)
    t=re.sub(r"\bthe\s+"+_PRICE_NUM+r"\s+(price tag|price|premium)",r"the higher \1",t)
    t=re.sub(r"\bthe\s+"+_PRICE_NUM+r"\s*","the ",t)
    t=re.sub(r"\b(under|below|above|over|around)\s+the\s+"+_PRICE_NUM+r"\s+mark\b",r"\1 a key price point",t)
    t=re.sub(r"\s+cost\s+"+_PRICE_NUM+r"(?:\s*[-–]\s*\$?[\d.]+)?\s+each"," cost extra per cup",t)
    t=re.sub(r"\ban?\s+"+_PRICE_NUM,"a pricier",t)
    t=re.sub(r"^"+_PRICE_NUM+r"\s*[—–-]\s*","",t)
    t=re.sub(r"^"+_PRICE_NUM+r"\s+(starting price|price tag|price)\b",r"The \1",t)
    t=re.sub(r"^"+_PRICE_NUM+r"\s+(?:is|was)\s+","",t)
    t=re.sub(r"^"+_PRICE_NUM+r"\s+","The price ",t)
    t=re.sub(r"^(?:Under|Below|Less than)\s+"+_PRICE_NUM+r"\s+(?=for\b)","An affordable price ",t)
    t=re.sub(r"\b(spends?|spending|pay|paying)\s+(?:over\s+|more than\s+)?"+_PRICE_NUM,r"\1 more",t)
    t=re.sub(r"^(?:At|For|Under|Around|Just|Only)\s+(?:just\s+|only\s+|about\s+|around\s+|under\s+)?"+_PRICE_NUM+r"[^,—–:]*?[,—–:]\s*","",t)
    t=re.sub(r"(?:[,;]\s*|\s+)(?:all\s+)?(?:at|for|under|around|about|near|below|less than|just|only|roughly|approximately|priced at|costs?|starting at|from)\s+(?:just\s+|only\s+|about\s+|around\s+|under\s+|roughly\s+|a\s+)?(?:sub-)?"+_PRICE_NUM+r"(?:\s+(?:price|price point|price tag|range|mark|budget))?","",t,flags=re.I)
    t=re.sub(r"\bsub-"+_PRICE_NUM+r"\s*","budget-friendly ",t)
    t=re.sub(r"^"+_PRICE_NUM+r"\s*(?:[—–-]\s*)?","",t)
    t=re.sub(_PRICE_NUM,"the price",t)
    t=t.replace("budget-friendly budget", "budget")
    t=re.sub(r",?\s+(?:and|or|at|for|from)\s*([.!?])", r"\1", t)
    t=re.sub(r"([.!?])\s*,\s*([a-z])", lambda m: m.group(1) + " " + m.group(2).upper(), t)
    t=re.sub(r"\s{2,}"," ",t); t=re.sub(r"\s+([,.;:!?])",r"\1",t); t=re.sub(r"([,;])\1+",r"\1",t)
    t=re.sub(r"^[\s,—–:-]+","",t); t=t.strip()
    if t and t[0].islower(): t=t[0].upper()+t[1:]
    return t


def _enrichment_fresh(fetched_at: Any, max_hours: float = 24.0) -> bool:
    try:
        from datetime import datetime as _dt, timezone as _tz
        t = _dt.fromisoformat(as_str(fetched_at).replace("Z", "+00:00"))
        return (_dt.now(_tz.utc) - t).total_seconds() <= max_hours * 3600
    except Exception:
        return False


def apply_enrichment(products: List[Dict[str, Any]]) -> int:
    """Overlay Amazon Creators API enrichment (image, price, cleaned title) onto
    products. Fully gated: if data/enrichment/ or a product's file is absent,
    nothing changes, so the site renders exactly as before the API is live."""
    if not ENRICH_DIR.exists():
        return 0
    applied = 0
    for p in products:
        asin = ((p.get("network_ids") or {}).get("amazon") or "").strip()
        if not asin:
            continue
        f = ENRICH_DIR / f"{asin}.json"
        if not f.exists():
            continue
        try:
            e = load_json(f)
        except Exception:
            continue
        if not _enrichment_fresh(e.get("fetched_at")):
            continue  # older than 24h: Amazon data may not be shown
        if isinstance(e.get("gallery"), list):
            p["gallery"] = [as_str(u) for u in e["gallery"] if real_image(as_str(u))][:6]
        if real_image(as_str(e.get("image_hi"))):
            p["image_hi"] = as_str(e.get("image_hi"))
        img = as_str(e.get("image"))
        if real_image(img):
            p["image_url"] = img
        price = e.get("price_usd")
        if price not in (None, "", 0):
            p["price_usd"] = price
            p["_api_price"] = True
            p["price_as_of"] = as_str(e.get("fetched_at"))
            was = e.get("was_price_usd")
            try:
                if was not in (None, "") and float(was) > float(price):
                    p["was_price_usd"] = was
                    p["was_price_label"] = as_str(e.get("was_price_label"))
                    p["savings_percent"] = e.get("savings_percent")
            except (TypeError, ValueError):
                pass
            if as_str(e.get("deal_badge")):
                p["deal_badge"] = as_str(e.get("deal_badge"))
        # Keep our own editorial product names; Amazon's titles are long SEO strings.
        # Only fall back to Amazon's title when a product has no name of its own.
        title = as_str(e.get("title_clean"))
        if title and not as_str(p.get("name")):
            p["name"] = title
        p["amazon_title"] = title
        applied += 1
    return applied


def main():
    validate_product_filenames()
    # Rebuild generated/ from scratch each run so that renamed or removed
    # categories/products do not leave orphaned, unlinked "ghost" pages behind.
    if GENERATED_DIR.exists():
        shutil.rmtree(GENERATED_DIR)
        print("Cleared previous generated/ output (prevents orphaned/stale pages).")
    site_config = load_site_config()
    site_name = as_str(site_config.get("name")) or "Site Engine"
    site_tagline = as_str(site_config.get("tagline"))
    site_description = as_str(site_config.get("description"))
    site_url = get_site_url()
    niche_to_reviewer = build_niche_to_reviewer(site_config)
    print(f"Site: {site_name}")
    print(f"Team: {len(site_config.get('team', []))} reviewer(s) covering {len(niche_to_reviewer)} niche(s)")

    category_template = read_text_required(CATEGORY_TEMPLATE)
    product_template = read_text_optional(PRODUCT_TEMPLATE, DEFAULT_PRODUCT_TEMPLATE)
    index_template = read_text_optional(INDEX_TEMPLATE, DEFAULT_INDEX_TEMPLATE)
    aff_config = load_affiliates_config()

    raw_products = load_all_products()
    products = normalize_products(raw_products)
    # Only real, in-stock Amazon listings are shown. data/stock-status.json lists ASINs that were
    # out of stock or gone at the last check; those products are hidden until they return.
    _stock_path = DATA_DIR / "stock-status.json"
    _stock_cfg = {}
    if _stock_path.exists():
        try:
            _stock_cfg = json.loads(_stock_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"WARNING: bad stock-status.json: {e}")
    _exempt = set(_stock_cfg.get("exempt_niches") or [])
    _run_path = ENRICH_DIR / "_run.json"
    _unav = None
    if _run_path.exists():
        try:
            _run = json.loads(_run_path.read_text(encoding="utf-8"))
            if not _enrichment_fresh(_run.get("fetched_at")):
                raise ValueError("Amazon run data is older than 24 hours")
            _ret = _run.get("returned") or {}
            _unav = {a for a in (_run.get("requested") or [])
                     if a not in _ret or not _ret[a].get("has_offer")
                     or (as_str(_ret[a].get("availability_type")).upper() in ("OUT_OF_STOCK", "UNAVAILABLE"))}
            print(f"Stock source: Amazon API run ({as_str(_run.get('fetched_at'))[:16]}).")
        except Exception as e:
            print(f"WARNING: bad enrichment _run.json ({e}); falling back to stock-status.json")
            _unav = None
    if _unav is None:
        _unav = set((_stock_cfg.get("unavailable") or {}).keys())
        print("Stock source: data/stock-status.json (manual check).")
    _before = len(products)
    products = [p for p in products
                if as_str(p.get("category")) in _exempt
                or ((p.get("network_ids") or {}).get("amazon") or "").strip() not in _unav]
    print(f"Stock filter: hid {_before - len(products)} unavailable product(s).")
    _applied = apply_enrichment(products)
    # Amazon compliance: only show prices that came from the API (current, timestamped).
    # Stored prices and dollar amounts written into product prose are never displayed.
    for _p in products:
        if not _p.get("_api_price"):
            _p["price_usd"] = None
        for _k in ("verdict", "description"):
            if isinstance(_p.get(_k), str):
                _p[_k] = strip_price_text(_p[_k])
        for _k in ("pros", "cons", "features"):
            if isinstance(_p.get(_k), list):
                _p[_k] = [strip_price_text(x) if isinstance(x, str) else x for x in _p[_k]]
    print(f"Applied Amazon enrichment to {_applied} product(s).")
    file_count = len(list(PRODUCTS_DIR.glob("*.json"))) if PRODUCTS_DIR.exists() else 0
    print(f"Loaded {len(products)} product(s) from {file_count} file(s) in data/products/")

    products_by_niche: Dict[str, List[Dict[str, Any]]] = {}
    for p in products:
        niche = as_str(p.get("category"))
        if niche not in products_by_niche:
            products_by_niche[niche] = []
        products_by_niche[niche].append(p)

    for _niche, _plist in products_by_niche.items():
        _top = _best_pick(_plist)
        for _p in _plist:
            _bf = _p.get("best_for") or []
            if _top is not None and _p is _top:
                _p["badge"] = "Editor's Choice"
            elif "budget" in _bf:
                _p["badge"] = "Best Value"
            elif "premium" in _bf:
                _p["badge"] = "Premium Pick"
            else:
                _p["badge"] = ""

    auto_cats = auto_generate_categories(products_by_niche)
    manual_cats = load_manual_categories() + build_price_gift_guides(products)
    categories = merge_categories(auto_cats, manual_cats)
    print(f"Categories: {len(categories)} total ({len(manual_cats)} manual overrides from categories.json, remainder auto-generated)")
    assert_unique_slugs(categories, "category")
    category_by_slug = {as_str(c.get("slug")): c for c in categories}
    guides = load_guides()
    guide_by_niche = {g["niche"]: g for g in guides if g.get("niche")}

    for p in products:
        p["affiliate_url"] = build_affiliate_url(p.get("network_ids", {}), aff_config)
        cat_slug = as_str(p.get("category"))
        cat = category_by_slug.get(cat_slug)
        if cat:
            p["primary_category_url"] = f"/generated/categories/{cat_slug}/"
            p["primary_category_name"] = as_str(cat.get("name"))
            niche = as_str(cat.get("niche"))
        else:
            p["primary_category_url"] = "#"
            p["primary_category_name"] = cat_slug or "All Products"
            niche = cat_slug
        p["sidebar_links"] = build_sidebar_links(niche, categories, exclude_slug=cat_slug)
        reviewer = niche_to_reviewer.get(cat_slug, {})
        p["reviewer_name"] = reviewer.get("name", "")
        p["reviewer_role"] = reviewer.get("role", "")
        p["reviewer_bio"] = reviewer.get("bio", "")
        p["site_name"] = site_name
        p["site_tagline"] = site_tagline
        p["head_extra"] = build_product_head_extra(p, site_url, site_name)
        p["cover_tile"] = cover_tile_html(p, large=True)
    _by_cat = {}
    for p in products:
        _by_cat.setdefault(as_str(p.get("category")), []).append(p)
    for p in products:
        p["alternatives_html"] = alternatives_html(p, _by_cat.get(as_str(p.get("category")), []))

    product_count = 0
    for p in products:
        slug = as_str(p.get("slug"))
        if not slug:
            continue
        html = render_product_page(product_template, p).replace(
            "{{SHARE_ROW}}", share_row_html(f"{site_url}/generated/products/{as_str(p.get('slug'))}/", f"{as_str(p.get('name'))} | {site_name}"))
        out_path = GENERATED_DIR / "products" / slug / "index.html"
        write_text(out_path, html)
        product_count += 1

    category_count = 0
    thin_slugs = set()
    slug_to_product = {as_str(p.get("slug")): p for p in products if as_str(p.get("slug"))}
    for cat in categories:
        cslug = as_str(cat.get("slug"))
        name = as_str(cat.get("name")) or cslug
        description = as_str(cat.get("description"))
        niche = as_str(cat.get("niche"))
        tags = cat.get("tags") or []
        match_tags = [normalize_slug(t) for t in tags if as_str(t)] or [cslug]
        explicit_slugs = cat.get("products") if isinstance(cat.get("products"), list) else None
        if explicit_slugs:
            matched = [slug_to_product[as_str(s)] for s in explicit_slugs if as_str(s) in slug_to_product]
            real_count = len(matched)
        else:
            niche_products = products_by_niche.get(niche, [])
            matched = [p for p in niche_products if product_matches(p, match_tags)]
            if not matched and niche_products:
                matched = niche_products
                real_count = 0
            else:
                real_count = len(matched)
        if cat.get("_auto") and real_count:
            _dd = data_driven_description(slug_to_display(niche), matched)
            if _dd:
                description = _dd
                cat["description"] = _dd
        is_thin = real_count < THIN_CATEGORY_MIN
        # Sub-pages only earn a spot in Google if they're a real, narrower list (5+ picks, fewer than the hub).
        _is_hub = cslug == niche
        _hub_n = len(products_by_niche.get(niche, []))
        if not _is_hub and not explicit_slugs and (real_count < 5 or real_count >= _hub_n):
            is_thin = True
        if is_thin:
            thin_slugs.add(cslug)
        editorial_html = build_editorial_html(load_editorial(niche), slug_to_display(niche), len(matched)) if cslug == niche else ""
        product_list_html = build_category_product_list(matched)
        reviewer = niche_to_reviewer.get(niche, {})
        faq_name = name if explicit_slugs else slug_to_display(niche)
        faqs = build_category_faqs(faq_name, matched, reviewer.get("name", ""), reviewer.get("role", ""))
        html = render_category_page(category_template, name, description, product_list_html)
        sidebar = build_sidebar_links(niche, categories, exclude_slug=cslug)
        _g = guide_by_niche.get(niche)
        guide_link = ""
        if _g:
            guide_link = f'<p class="category-guide-link"><a href="{GUIDE_URL_BASE}/{_g["slug"]}/">Read our {escape(slug_to_display(niche))} buying guide &rarr;</a></p>'
        toc_nav = ""
        section_bar = build_section_bar(build_category_toc_items(matched, bool(faqs)))
        _bf = []
        if cslug == niche:
            for _c in categories:
                if as_str(_c.get("niche")) != niche or as_str(_c.get("slug")) in (niche, f"best-{niche}"):
                    continue
                _tags = [normalize_slug(t) for t in (_c.get("tags") or [])]
                if len(_tags) != 1 or _tags[0] in ("budget", "premium", "best-overall", "budget-premium"):
                    continue
                _cm = [p for p in matched if product_matches(p, _tags)]
                if len(_cm) < 2:
                    continue
                _bp = _top_rated(_cm, set())
                if _bp:
                    _bf.append((slug_to_display(_tags[0]), _bp, f"/generated/categories/{as_str(_c.get('slug'))}/"))
        # Gift guides are browsable lists, not ranked reviews, so they skip the Top/Budget/Upgrade cards.
        pick_cards = "" if niche == "gifts" else build_pick_cards_html(matched, display_noun(slug_to_display(niche)), _bf)
        html = (html
            .replace("{{TOC_NAV}}", toc_nav)
            .replace("{{GUIDE_LINK}}", guide_link)
            .replace("{{TOP_PICK}}", pick_cards)
            .replace("{{HERO_MEDIA}}", photo_trio_html(_rated(matched)))
            .replace("{{HERO_CLASS}}", " has-media" if photo_trio_html(_rated(matched)) else "")
            .replace("{{SECTION_BAR}}", section_bar)
            .replace("{{SHARE_ROW}}", share_row_html(f"{site_url}/generated/categories/{cslug}/", f"{name} | {site_name}"))
            .replace("{{PROMO_BOX}}", promo_box_html())
            .replace("{{FAQ_SECTION}}", build_faq_section_html(faqs))
            .replace("{{SITE_NAME}}", escape(site_name))
            .replace("{{SITE_TAGLINE}}", escape(site_tagline))
            .replace("{{REVIEWER_NAME}}", escape(reviewer.get("name", "")))
            .replace("{{REVIEWER_ROLE}}", escape(reviewer.get("role", "")))
            .replace("{{REVIEWER_BIO}}", escape(reviewer.get("bio", "")))
            .replace("{{SIDEBAR_LINKS}}", sidebar)
            .replace("{{EDITORIAL}}", editorial_html)
            .replace("{{SEO_TITLE}}", escape(category_seo_title(name, len(matched), _is_hub, bool(explicit_slugs), niche)))
            .replace("{{SEO_DESC}}", escape(category_seo_desc(name, matched, niche, description)))
            .replace("{{HEAD_EXTRA}}", ('<meta name="robots" content="noindex, follow" />\n' if is_thin else "") + build_category_head_extra(cat, matched, site_url, site_name, faqs)))
        out_path = GENERATED_DIR / "categories" / cslug / "index.html"
        write_text(out_path, html)
        category_count += 1

    hp_data = build_homepage(categories, products_by_niche, niche_to_reviewer, guides)
    homepage_html = index_template
    for key, val in hp_data.items():
        homepage_html = homepage_html.replace("{{" + key + "}}", val)
    homepage_html = homepage_html.replace("{{SITE_NAME}}", escape(site_name))
    homepage_html = homepage_html.replace("{{SITE_TAGLINE}}", escape(site_tagline))
    homepage_html = homepage_html.replace("{{SITE_DESCRIPTION}}", escape(site_description))
    homepage_html = homepage_html.replace("{{HEAD_EXTRA}}", build_home_head_extra(site_config, site_url, site_name))
    write_text(INDEX_HTML, homepage_html)
    print("Generated index.html (homepage).")

    search_index = []
    for _p in products:
        _s = as_str(_p.get("slug"))
        if _s:
            search_index.append({"t": as_str(_p.get("name")), "u": f"/generated/products/{_s}/", "k": "Review"})
    for _c in categories:
        _s = as_str(_c.get("slug"))
        if _s:
            search_index.append({"t": as_str(_c.get("name")), "u": f"/generated/categories/{_s}/", "k": "Category"})
    for _g in guides:
        _s = as_str(_g.get("slug"))
        if _s:
            search_index.append({"t": as_str(_g.get("title")), "u": f"/generated/guides/{_s}/", "k": "Guide"})
    write_text(GENERATED_DIR / "search-index.v1.js", "window.__HALCOTT_SEARCH__=" + json.dumps(search_index, ensure_ascii=False) + ";")
    print(f"Generated generated/search-index.v1.js ({len(search_index)} entries).")

    guide_urls: List[str] = []
    guide_template = read_text_optional(GUIDE_TEMPLATE, "")
    if guides and guide_template:
        hub_by_niche = {}
        for c in categories:
            if as_str(c.get("slug")) == as_str(c.get("niche")):
                hub_by_niche[as_str(c.get("niche"))] = (as_str(c.get("slug")), as_str(c.get("name")))
        for g in guides:
            gslug = g["slug"]
            related_cta = ""
            hub = hub_by_niche.get(g.get("niche"))
            gprods = products_by_niche.get(as_str(g.get("niche")), [])
            if hub:
                hub_href = escape(f"/generated/categories/{hub[0]}/", quote=True)
                related_cta = (f'<div class="guide-cta guide-cta-photo">{photo_trio_html(_rated(gprods))}'
                               f'<div class="gcp-text"><strong>Ready to compare the top {escape(hub[1].lower())}?</strong>'
                               f'<p>See every pick we recommend, with live Amazon prices.</p>'
                               f'<a class="cta cta-buy" href="{hub_href}">See all {len(gprods)} {escape(hub[1])} picks &rarr;</a></div></div>')
            picks_html = ""
            if gprods:
                picks_html = ('<section class="guide-picks" id="our-picks"><p class="gp-kicker">Our picks</p>'
                              + build_pick_cards_html(gprods, display_noun(slug_to_display(as_str(g.get("niche"))))) + '</section>')
            _secs = guide_body_html(g.get("sections"))
            _cut = [m.end() for m in re.finditer(r'</section>', _secs)]
            gbody = (_secs[:_cut[0]] + picks_html + _secs[_cut[0]:]) if _cut else (picks_html + _secs)
            faqs = g.get("faqs") or []
            faq_html = build_faq_section_html([(q, a) for q, a in faqs])
            head_extra = build_guide_head_extra(g, site_url, site_name, faqs)
            html = render_guide_page(guide_template, g, site_name, site_tagline, related_cta, faq_html, head_extra,
                                     body_override=gbody, hero_media=photo_trio_html(_rated(gprods)),
                                     section_bar=build_section_bar(build_guide_toc_items(g, bool(faqs))),
                                     share_row=share_row_html(f"{site_url}{GUIDE_URL_BASE}/{gslug}/", f"{as_str(g.get('title'))} | {site_name}"))
            write_text(GENERATED_DIR / "guides" / gslug / "index.html", html)
            guide_urls.append(f"{site_url}{GUIDE_URL_BASE}/{gslug}/")
        items = []
        for i, g in enumerate(guides):
            gd = as_str(g.get("description"))
            if len(gd) > 150:
                gd = gd[:147].rsplit(" ", 1)[0] + "..."
            items.append(photo_card_html(f"{GUIDE_URL_BASE}/{g['slug']}/", as_str(g.get("title")), gd,
                                         products_by_niche.get(as_str(g.get("niche")), []), i,
                                         "Read the guide &rarr;", slug_to_display(as_str(g.get("niche")))))
        idx_body = '<div class="photo-grid photo-grid-3 guides-index-grid">' + "".join(items) + "</div>"
        idx_desc = "In-depth buying guides from the " + site_name + " editorial team to help you choose the right product before you buy."
        idx_guide = {"slug": "", "title": "Buying Guides", "description": idx_desc,
                     "intro": "Practical, jargon-free guides to help you pick the right product with confidence."}
        idx_head = "\n    ".join(list(FAVICON_LINKS) + _og_meta("Buying Guides", idx_desc, f"{site_url}{GUIDE_URL_BASE}/", site_name, og_type="website"))
        idx_html = render_guide_page(guide_template, idx_guide, site_name, site_tagline, "", "", idx_head, body_override=idx_body)
        idx_html = re.sub(r'<a href="/generated/guides/">Buying Guides</a>\s*<span class="breadcrumb-sep">&rsaquo;</span>\s*', "", idx_html, count=1)
        write_text(GENERATED_DIR / "guides" / "index.html", idx_html)
        guide_urls.append(f"{site_url}{GUIDE_URL_BASE}/")
        print(f"Generated {len(guides)} buying guide(s) + guides index.")


    # ---- All Categories page (replaces the long list that used to sit on the homepage) ----
    def _strip_price(t):
        t = re.sub(r"\s*\((?:[^()]*\$\d[^()]*)\)", "", t)
        return re.sub(r"\s*(?:under|below|less than|for|at)?\s*\$\d[\d,]*(?:\.\d+)?\+?", "", t, flags=re.I).strip()
    by_niche_cats: Dict[str, List[Dict[str, Any]]] = OrderedDict()
    for c in categories:
        by_niche_cats.setdefault(as_str(c.get("niche")), []).append(c)
    dept_boxes = []
    for _niche in sorted(k for k in by_niche_cats if k and k != "gifts"):
        _cs = by_niche_cats[_niche]
        _hub = next((c for c in _cs if as_str(c.get("slug")) == _niche), None)
        _hub_href = escape(f"/generated/categories/{as_str(_hub.get('slug')) if _hub else _niche}/", quote=True)
        _links = []
        _seen_labels = set()
        for c in sorted(_cs, key=lambda c: as_str(c.get("name"))):
            cs = as_str(c.get("slug"))
            if cs == _niche or cs in thin_slugs:
                continue
            tags = c.get("tags") or []
            label = slug_to_display(tags[0]) if len(tags) == 1 else _strip_price(as_str(c.get("name")))
            if label.lower() in ("best overall",):
                label = "Top Picks"
            if label.lower() in _seen_labels:
                continue
            _seen_labels.add(label.lower())
            _links.append(f'<li><a href="{escape("/generated/categories/" + cs + "/", quote=True)}">{escape(label)}</a></li>')
        _trio = photo_trio_html(_rated(products_by_niche.get(_niche, [])))
        dept_boxes.append(f'<div class="allcat-box" style="--tint:{PASTELS[len(dept_boxes) % len(PASTELS)]}">'
                          + (f'<a class="allcat-media" href="{_hub_href}">{_trio}</a>' if _trio else '')
                          + f'<h2><a href="{_hub_href}">{escape(slug_to_display(_niche))}</a></h2>'
                          + (f'<ul>{"".join(_links)}</ul>' if _links else '')
                          + f'<a class="allcat-more" href="{_hub_href}">See all {escape(display_noun(slug_to_display(_niche)))} &rarr;</a></div>')
    ac_desc = f"Every product category reviewed by {site_name}, from 4K monitors to running shoes."
    ac_page = {"slug": "", "title": "All Categories", "description": ac_desc,
               "intro": "Pick a department to see our top picks, budget options, and buying advice."}
    ac_head = "\n    ".join(list(FAVICON_LINKS) + _og_meta("All Categories", ac_desc, f"{site_url}/generated/categories/", site_name, og_type="website"))
    ac_html = render_guide_page(guide_template, ac_page, site_name, site_tagline, "", "", ac_head,
                                body_override='<div class="allcat-grid">' + "".join(dept_boxes) + '</div>')
    ac_html = ac_html.replace('<main class="container guide-page', '<main class="container guide-page wide-page', 1)
    ac_html = re.sub(r'<a href="/generated/guides/">Buying Guides</a>\s*<span class="breadcrumb-sep">&rsaquo;</span>\s*', "", ac_html, count=1)
    write_text(GENERATED_DIR / "categories" / "index.html", ac_html)

    urls = [f"{site_url}/"]
    urls.append(f"{site_url}/generated/categories/")
    for sp in ("/about/", "/editorial-policy/", "/contact", "/privacy-policy", "/terms"):
        urls.append(f"{site_url}{sp}")
    for cat in categories:
        cslug = as_str(cat.get("slug"))
        if cslug in thin_slugs:
            continue
        urls.append(f"{site_url}/generated/categories/{cslug}/")
    for p in products:
        pslug = as_str(p.get("slug"))
        urls.append(f"{site_url}/generated/products/{pslug}/")
    urls.extend(guide_urls)
    urls = sorted(set(urls))
    write_text(SITEMAP_XML, build_sitemap(urls))
    write_text(ROBOTS_TXT, build_robots(site_url))

    run_quality_gates()
    print(f"Generated {category_count} category page(s); {len(thin_slugs)} thin page(s) set to noindex and left out of the sitemap.")
    print(f"Generated {product_count} product page(s).")
    print("Generated sitemap.xml and robots.txt.")


if __name__ == "__main__":
    main()
