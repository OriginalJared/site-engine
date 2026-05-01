import json
import os
import re
import sys
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
PRODUCTS_DIR = DATA_DIR / "products"
AFFILIATES_JSON = DATA_DIR / "affiliates.json"
SITE_JSON = DATA_DIR / "site.json"

CATEGORY_TEMPLATE = TEMPLATES_DIR / "category.html"
PRODUCT_TEMPLATE = TEMPLATES_DIR / "product.html"
INDEX_TEMPLATE = TEMPLATES_DIR / "index.html"

SITEMAP_XML = ROOT / "sitemap.xml"
ROBOTS_TXT = ROOT / "robots.txt"
INDEX_HTML = ROOT / "index.html"

DEFAULT_SITE_URL = "https://site-engine-9gr.pages.dev"

_slug_re = re.compile(r"[^a-z0-9-]+")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

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


def slug_to_display(slug: str) -> str:
    return slug.replace("-", " ").title()


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


# ---------------------------------------------------------------------------
# Site config (site.json)
# ---------------------------------------------------------------------------

def load_site_config() -> Dict[str, Any]:
    if not SITE_JSON.exists():
        return {
            "name": "Site Engine",
            "tagline": "",
            "description": "",
            "url": DEFAULT_SITE_URL,
            "team": []
        }
    return load_json(SITE_JSON)


def build_niche_to_reviewer(site_config: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Build a mapping of niche-slug -> reviewer info dict."""
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


# ---------------------------------------------------------------------------
# Affiliates
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Product normalization
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Category auto-generation
# ---------------------------------------------------------------------------

TAG_CATEGORY_TEMPLATES = {
    "best-overall": {"slug_pattern": "best-{niche_slug}", "name": "Best {niche} {year} (Top Picks)", "description": "Our top-rated {niche} based on performance, features, and value. These are the products we recommend most often."},
    "budget": {"slug_pattern": "budget-{niche_slug}", "name": "Best Budget {niche}", "description": "Great {niche} that deliver solid performance without breaking the bank. Proof that you don't need to overspend to get quality."},
    "premium": {"slug_pattern": "premium-{niche_slug}", "name": "Premium {niche}", "description": "Top-of-the-line {niche} with the best features, build quality, and warranties available. For those who want the absolute best."},
}

DEFAULT_TAG_TEMPLATE = {"slug_pattern": "{niche_slug}-for-{tag_slug}", "name": "Best {niche} for {tag}", "description": "The best {niche} for {tag} \u2014 curated based on real specs, reviews, and performance data."}


def auto_generate_categories(products_by_niche: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    year = date.today().year
    auto_cats: List[Dict[str, Any]] = []
    for niche_slug in sorted(products_by_niche.keys()):
        if not niche_slug:
            continue
        niche_display = slug_to_display(niche_slug)
        niche_products = products_by_niche[niche_slug]
        all_tags = set()
        for p in niche_products:
            for t in (p.get("best_for") or []):
                tag = normalize_slug(t)
                if tag:
                    all_tags.add(tag)
        all_tags_sorted = sorted(all_tags)
        auto_cats.append({"slug": niche_slug, "name": niche_display, "niche": niche_slug, "description": f"Compare the best {niche_display.lower()} of {year} side by side. Browse all categories and find the right product for your needs and budget.", "tags": all_tags_sorted, "_auto": True})
        for tag_slug in all_tags_sorted:
            tag_display = slug_to_display(tag_slug)
            tmpl = TAG_CATEGORY_TEMPLATES.get(tag_slug, DEFAULT_TAG_TEMPLATE)
            cat_slug = tmpl["slug_pattern"].format(niche_slug=niche_slug, tag_slug=tag_slug)
            cat_name = tmpl["name"].format(niche=niche_display, tag=tag_display, year=str(year))
            cat_desc = tmpl["description"].format(niche=niche_display.lower(), tag=tag_display.lower(), year=str(year))
            auto_cats.append({"slug": cat_slug, "name": cat_name, "niche": niche_slug, "description": cat_desc, "tags": [tag_slug], "_auto": True})
    return auto_cats


def load_manual_categories() -> List[Dict[str, Any]]:
    if not CATEGORIES_JSON.exists():
        return []
    raw = load_json(CATEGORIES_JSON)
    cats = require_list(raw, "categories")
    out = []
    for i, c in enumerate(cats):
        if not isinstance(c, dict):
            continue
        slug = normalize_slug(c.get("slug"))
        if not slug:
            continue
        out.append({"slug": slug, "name": as_str(c.get("name")) or slug, "niche": as_str(c.get("niche")), "description": as_str(c.get("description")), "tags": [normalize_slug(t) for t in require_list(c.get("tags"), f"categories[{i}].tags") if as_str(t)], "_auto": False})
    return out


def merge_categories(auto_cats: List[Dict[str, Any]], manual_cats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for c in auto_cats:
        merged[c["slug"]] = c
    for c in manual_cats:
        merged[c["slug"]] = c
    out = sorted(merged.values(), key=lambda x: x["slug"])
    for c in out:
        c.pop("_auto", None)
    return out


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Homepage builder
# ---------------------------------------------------------------------------

def build_homepage(categories: List[Dict[str, Any]], products_by_niche: Dict[str, List[Dict[str, Any]]], niche_to_reviewer: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    niches: Dict[str, List[Dict[str, Any]]] = OrderedDict()
    for c in categories:
        niche = as_str(c.get("niche"))
        if niche not in niches:
            niches[niche] = []
        niches[niche].append(c)
    niche_sections = []
    for niche, cats in niches.items():
        display = slug_to_display(niche)
        product_count = len(products_by_niche.get(niche, []))
        reviewer = niche_to_reviewer.get(niche, {})
        reviewer_name = reviewer.get("name", "")
        reviewer_role = reviewer.get("role", "")
        reviewer_bio = reviewer.get("bio", "")
        hub_cat = None
        for c in cats:
            if as_str(c.get("slug")) == niche:
                hub_cat = c
                break
        hub_slug = as_str(hub_cat.get("slug")) if hub_cat else niche
        hub_href = escape(f"/generated/categories/{hub_slug}/", quote=True)
        sub_cats = [c for c in cats if as_str(c.get("slug")) != niche][:3]
        cards = []
        for sc in sub_cats:
            sc_slug = as_str(sc.get("slug"))
            sc_name = as_str(sc.get("name"))
            sc_desc = as_str(sc.get("description"))
            if len(sc_desc) > 120:
                sc_desc = sc_desc[:117].rsplit(" ", 1)[0] + "..."
            sc_href = escape(f"/generated/categories/{sc_slug}/", quote=True)
            card = '<div class="card" style="flex:1;min-width:220px;">'
            card += f'<h3>{escape(sc_name)}</h3>'
            card += f'<p>{escape(sc_desc)}</p>'
            card += f'<a class="cta" href="{sc_href}">Browse</a>'
            card += '</div>'
            cards.append(card)
        cards_html = "\n".join(cards)
        remaining_cats = [c for c in cats if as_str(c.get("slug")) != niche and c not in sub_cats]
        more_html = ""
        if remaining_cats:
            more_items = []
            for rc in remaining_cats:
                rc_slug = as_str(rc.get("slug"))
                rc_name = as_str(rc.get("name"))
                rc_href = escape(f"/generated/categories/{rc_slug}/", quote=True)
                more_items.append(f'<li><a href="{rc_href}">{escape(rc_name)}</a></li>')
            more_list = "\n".join(more_items)
            more_html = f'<div style="margin-top:12px;"><strong>More {escape(display)}:</strong><ul style="margin:6px 0 0 18px;">{more_list}</ul></div>'
        reviewer_html = ""
        if reviewer_name:
            reviewer_html = f'<p class="niche-reviewer"><strong>Reviewed by {escape(reviewer_name)}</strong>, {escape(reviewer_role)}</p>'
        section = f'<section class="niche-section">'
        section += f'<h2><a href="{hub_href}" style="text-decoration:none;color:inherit;">{escape(display)}</a> <span style="font-size:0.6em;color:#888;">({product_count} products)</span></h2>'
        section += reviewer_html
        section += f'<div style="display:flex;gap:16px;flex-wrap:wrap;">{cards_html}</div>'
        section += more_html
        section += '</section>'
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
            all_cats_parts.append(f'<li><a href="{c_href}">{escape(c_name)}</a></li>')
        all_cats_parts.append('</ul>')

    # Build team section
    team_parts = []
    team_parts.append('<div class="team-grid">')
    # We deduplicate by slug since multiple niches map to the same reviewer
    seen_reviewers = set()
    for niche in sorted(niche_to_reviewer.keys()):
        r = niche_to_reviewer[niche]
        rslug = r.get("slug", "")
        if rslug in seen_reviewers:
            continue
        seen_reviewers.add(rslug)
        rname = escape(r.get("name", ""))
        rrole = escape(r.get("role", ""))
        rbio = escape(r.get("bio", ""))
        team_parts.append(f'<div class="team-card">')
        team_parts.append(f'<div class="team-avatar">{escape(rname[:1])}</div>')
        team_parts.append(f'<h3>{rname}</h3>')
        team_parts.append(f'<p class="team-role">{rrole}</p>')
        team_parts.append(f'<p class="team-bio">{rbio}</p>')
        team_parts.append(f'</div>')
    team_parts.append('</div>')

    return {
        "NICHE_SECTIONS": "\n".join(niche_sections),
        "ALL_CATEGORIES_LIST": "\n".join(all_cats_parts),
        "TEAM_SECTION": "\n".join(team_parts),
    }


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

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
        return "<p>\u2014</p>"
    return "<ul>" + "".join(f"<li>{escape(t)}</li>" for t in tags) + "</ul>"


def features_html(features: Any) -> str:
    items = require_list(features, "features")
    items = [as_str(f) for f in items if as_str(f)]
    if not items:
        return ""
    return "<ul>" + "".join(f"<li>{escape(f)}</li>" for f in items) + "</ul>"


def pros_cons_html(pros: Any, cons: Any) -> str:
    pro_list = [as_str(p) for p in require_list(pros, "pros") if as_str(p)]
    con_list = [as_str(c) for c in require_list(cons, "cons") if as_str(c)]
    parts = []
    if pro_list:
        parts.append('<div class="pros"><h3>\u2705 What We Like</h3><ul>')
        parts.extend(f"<li>{escape(p)}</li>" for p in pro_list)
        parts.append('</ul></div>')
    if con_list:
        parts.append('<div class="cons"><h3>\u26a0\ufe0f Worth Noting</h3><ul>')
        parts.extend(f"<li>{escape(c)}</li>" for c in con_list)
        parts.append('</ul></div>')
    return "\n".join(parts)


def safe_url(url: str) -> str:
    url = as_str(url)
    if not url:
        return "#"
    return escape(url, quote=True)


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
        .replace("{{SITE_TAGLINE}}", escape(site_tagline)))


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
<div class="pros-cons">{{PROS_CONS}}</div>
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
            meta_parts.append(f"<strong>Price:</strong> ${escape(str(price))}")
        if rating is not None:
            meta_parts.append(f"<strong>Rating:</strong> {escape(str(rating))}/5")
        meta_html = " &nbsp;\u2022&nbsp; ".join(meta_parts) if meta_parts else ""
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
        card = f'<div class="product-card">'
        card += f'<h3><a href="{name_href}">{escape(name)}</a></h3>'
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


# ---------------------------------------------------------------------------
# SEO files
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Load site config
    site_config = load_site_config()
    site_name = as_str(site_config.get("name")) or "Site Engine"
    site_tagline = as_str(site_config.get("tagline"))
    site_description = as_str(site_config.get("description"))
    niche_to_reviewer = build_niche_to_reviewer(site_config)
    print(f"Site: {site_name}")
    print(f"Team: {len(site_config.get('team', []))} reviewer(s) covering {len(niche_to_reviewer)} niche(s)")

    # Load templates
    category_template = read_text_required(CATEGORY_TEMPLATE)
    product_template = read_text_optional(PRODUCT_TEMPLATE, DEFAULT_PRODUCT_TEMPLATE)
    index_template = read_text_optional(INDEX_TEMPLATE, DEFAULT_INDEX_TEMPLATE)
    aff_config = load_affiliates_config()

    # Load and normalize products
    raw_products = load_all_products()
    products = normalize_products(raw_products)
    file_count = len(list(PRODUCTS_DIR.glob("*.json"))) if PRODUCTS_DIR.exists() else 0
    print(f"Loaded {len(products)} product(s) from {file_count} file(s) in data/products/")

    # Group by niche
    products_by_niche: Dict[str, List[Dict[str, Any]]] = {}
    for p in products:
        niche = as_str(p.get("category"))
        if niche not in products_by_niche:
            products_by_niche[niche] = []
        products_by_niche[niche].append(p)

    # Build categories
    auto_cats = auto_generate_categories(products_by_niche)
    manual_cats = load_manual_categories()
    categories = merge_categories(auto_cats, manual_cats)
    print(f"Categories: {len(categories)} total ({len(manual_cats)} manual overrides from categories.json, remainder auto-generated)")
    assert_unique_slugs(categories, "category")
    category_by_slug = {as_str(c.get("slug")): c for c in categories}

    # Enrich products with affiliate URLs, categories, reviewer info, and site info
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
        # Attach reviewer info
        reviewer = niche_to_reviewer.get(cat_slug, {})
        p["reviewer_name"] = reviewer.get("name", "")
        p["reviewer_role"] = reviewer.get("role", "")
        p["reviewer_bio"] = reviewer.get("bio", "")
        # Attach site info
        p["site_name"] = site_name
        p["site_tagline"] = site_tagline

    # Generate product pages
    product_count = 0
    for p in products:
        slug = as_str(p.get("slug"))
        if not slug:
            continue
        html = render_product_page(product_template, p)
        out_path = GENERATED_DIR / "products" / slug / "index.html"
        write_text(out_path, html)
        product_count += 1

    # Generate category pages — inject site name and reviewer into template
    category_count = 0
    for cat in categories:
        cslug = as_str(cat.get("slug"))
        name = as_str(cat.get("name")) or cslug
        description = as_str(cat.get("description"))
        niche = as_str(cat.get("niche"))
        tags = cat.get("tags") or []
        match_tags = [normalize_slug(t) for t in tags if as_str(t)] or [cslug]
        niche_products = products_by_niche.get(niche, [])
        matched = [p for p in niche_products if product_matches(p, match_tags)]
        if not matched and niche_products:
            matched = niche_products
        product_list_html = build_category_product_list(matched)
        html = render_category_page(category_template, name, description, product_list_html)
        # Inject site-level and reviewer placeholders into category pages
        reviewer = niche_to_reviewer.get(niche, {})
        html = (html
            .replace("{{SITE_NAME}}", escape(site_name))
            .replace("{{SITE_TAGLINE}}", escape(site_tagline))
            .replace("{{REVIEWER_NAME}}", escape(reviewer.get("name", "")))
            .replace("{{REVIEWER_ROLE}}", escape(reviewer.get("role", "")))
            .replace("{{REVIEWER_BIO}}", escape(reviewer.get("bio", ""))))
        out_path = GENERATED_DIR / "categories" / cslug / "index.html"
        write_text(out_path, html)
        category_count += 1

    # Generate homepage
    hp_data = build_homepage(categories, products_by_niche, niche_to_reviewer)
    homepage_html = index_template
    for key, val in hp_data.items():
        homepage_html = homepage_html.replace("{{" + key + "}}", val)
    homepage_html = homepage_html.replace("{{SITE_NAME}}", escape(site_name))
    homepage_html = homepage_html.replace("{{SITE_TAGLINE}}", escape(site_tagline))
    homepage_html = homepage_html.replace("{{SITE_DESCRIPTION}}", escape(site_description))
    write_text(INDEX_HTML, homepage_html)
    print("Generated index.html (homepage).")

    # Generate SEO files
    site_url = get_site_url()
    urls = [f"{site_url}/"]
    for cat in categories:
        cslug = as_str(cat.get("slug"))
        urls.append(f"{site_url}/generated/categories/{cslug}/")
    for p in products:
        pslug = as_str(p.get("slug"))
        urls.append(f"{site_url}/generated/products/{pslug}/")
    urls = sorted(set(urls))
    write_text(SITEMAP_XML, build_sitemap(urls))
    write_text(ROBOTS_TXT, build_robots(site_url))

    # Quality gates
    run_quality_gates()
    print(f"Generated {category_count} category page(s).")
    print(f"Generated {product_count} product page(s).")
    print("Generated sitemap.xml and robots.txt.")


if __name__ == "__main__":
    main()
