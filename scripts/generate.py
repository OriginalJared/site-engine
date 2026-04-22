import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from html import escape
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"
GENERATED_DIR = ROOT / "generated"

CATEGORIES_JSON = DATA_DIR / "categories.json"
PRODUCTS_DIR = DATA_DIR / "products"
AFFILIATES_JSON = DATA_DIR / "affiliates.json"

CATEGORY_TEMPLATE = TEMPLATES_DIR / "category.html"
PRODUCT_TEMPLATE = TEMPLATES_DIR / "product.html"

SITEMAP_XML = ROOT / "sitemap.xml"
ROBOTS_TXT = ROOT / "robots.txt"

DEFAULT_SITE_URL = "https://site-engine-9gr.pages.dev"

_slug_re = re.compile(r"[^a-z0-9-]+")


# ----------------------------
# IO helpers
# ----------------------------

def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_all_products() -> List[Any]:
    """Load and merge all product JSON files from data/products/."""
    if not PRODUCTS_DIR.exists():
        return []
    all_products = []
    for f in sorted(PRODUCTS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"\u26a0\ufe0f  Skipping {f.name}: {e}")
            continue
        if isinstance(data, list):
            all_products.extend(data)
        elif isinstance(data, dict):
            all_products.append(data)
        else:
            print(f"\u26a0\ufe0f  Skipping {f.name}: unexpected root type {type(data).__name__}")
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


# ----------------------------
# Normalization + validation
# ----------------------------

def as_str(v: Any) -> str:
    return ("" if v is None else str(v)).strip()


def normalize_slug(v: Any) -> str:
    s = as_str(v).lower()
    s = s.replace("_", "-")
    s = "-".join(s.split())
    s = _slug_re.sub("-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


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


# ----------------------------
# Affiliate URL builder (Phase 4)
# ----------------------------

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


# ----------------------------
# Product / category normalization
# ----------------------------

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
        network_ids = require_dict(p.get("network_ids"), f"products[{i}].network_ids")
        best_for = [
            normalize_slug(x)
            for x in require_list(p.get("best_for"), f"products[{i}].best_for")
            if as_str(x)
        ]
        specs = require_dict(p.get("specs"), f"products[{i}].specs")
        out.append({
            "slug": slug,
            "name": name,
            "brand": brand,
            "category": category,
            "description": description,
            "price_usd": price_usd,
            "network_ids": network_ids,
            "affiliate_url": "",
            "image_url": image_url,
            "best_for": best_for,
            "specs": specs,
        })
    out.sort(key=lambda x: x["slug"])
    assert_unique_slugs(out, "product")
    return out


def normalize_categories(raw_categories: Any) -> List[Dict[str, Any]]:
    cats = require_list(raw_categories, "categories")
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(cats):
        if not isinstance(c, dict):
            continue
        slug = normalize_slug(c.get("slug"))
        if not slug:
            continue
        name = as_str(c.get("name")) or slug
        niche = as_str(c.get("niche"))
        description = as_str(c.get("description"))
        tags_raw = c.get("tags", None)
        tags: List[str] = []
        if tags_raw is not None:
            tags = [
                normalize_slug(x)
                for x in require_list(tags_raw, f"categories[{i}].tags")
                if as_str(x)
            ]
        out.append({
            "slug": slug,
            "name": name,
            "niche": niche,
            "description": description,
            "tags": tags,
        })
    out.sort(key=lambda x: x["slug"])
    assert_unique_slugs(out, "category")
    return out


# ----------------------------
# Dynamic sidebar builder
# ----------------------------

def build_sidebar_links(niche: str, categories: List[Dict[str, Any]], exclude_slug: str = "") -> str:
    """Build HTML <li> items for all categories in the same niche."""
    niche_cats = [
        c for c in categories
        if as_str(c.get("niche")) == niche and as_str(c.get("slug")) != exclude_slug
    ]
    if not niche_cats:
        return '<li>No related categories yet.</li>'
    items = []
    for c in niche_cats:
        slug = as_str(c.get("slug"))
        name = as_str(c.get("name"))
        href = escape(f"/generated/categories/{slug}/", quote=True)
        items.append(f'<li><a href="{href}">{escape(name)}</a></li>')
    return "\n            ".join(items)


# ----------------------------
# HTML rendering helpers
# ----------------------------

def render_category_page(template: str, name: str, description: str, product_list_html: str) -> str:
    return (
        template
        .replace("{{CATEGORY_NAME}}", escape(name))
        .replace("{{CATEGORY_DESCRIPTION}}", escape(description))
        .replace("{{PRODUCT_LIST}}", product_list_html)
    )


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
        return '<p>\u2014</p>'
    return '<ul>' + ''.join(f'<li>{escape(t)}</li>' for t in tags) + '</ul>'


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
    affiliate_url = as_str(p.get("affiliate_url"))
    img_url = as_str(p.get("image_url"))
    best_for = p.get("best_for") or []
    specs = p.get("specs") or {}
    primary_category_url = as_str(p.get("primary_category_url"))
    primary_category_name = as_str(p.get("primary_category_name"))
    sidebar_links = as_str(p.get("sidebar_links"))

    price_txt = "" if price is None else str(price)

    return (
        template
        .replace("{{PRODUCT_NAME}}", escape(name))
        .replace("{{PRODUCT_BRAND}}", escape(brand))
        .replace("{{PRODUCT_DESCRIPTION}}", escape(description))
        .replace("{{PRODUCT_PRICE}}", escape(price_txt))
        .replace("{{AFFILIATE_URL}}", safe_url(affiliate_url))
        .replace("{{IMAGE_URL}}", safe_url(img_url))
        .replace("{{BEST_FOR}}", best_for_html(best_for))
        .replace("{{SPECS_TABLE}}", specs_table_html(specs))
        .replace("{{PRIMARY_CATEGORY_URL}}", escape(primary_category_url))
        .replace("{{PRIMARY_CATEGORY_NAME}}", escape(primary_category_name))
        .replace("{{SIDEBAR_LINKS}}", sidebar_links)
    )


DEFAULT_PRODUCT_TEMPLATE = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>{{PRODUCT_NAME}}</title>
  </head>
  <body>
    <h1>{{PRODUCT_NAME}}</h1>
    <p><strong>Brand:</strong> {{PRODUCT_BRAND}}</p>
    <p><strong>Price (USD):</strong> {{PRODUCT_PRICE}}</p>
    <p><a class="cta" href="{{AFFILIATE_URL}}" rel="nofollow sponsored noopener" target="_blank">Buy / Check Price</a></p>
    <p><a class="secondary" href="{{PRIMARY_CATEGORY_URL}}">Browse {{PRIMARY_CATEGORY_NAME}}</a></p>
    <div class="product-description">{{PRODUCT_DESCRIPTION}}</div>
    <h2>Best For</h2>
    <div>{{BEST_FOR}}</div>
    <h2>Specs</h2>
    <div>{{SPECS_TABLE}}</div>
    <h2>Related Categories</h2>
    <ul>{{SIDEBAR_LINKS}}</ul>
  </body>
</html>
"""


# ----------------------------
# Category filtering + listing
# ----------------------------

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
        affiliate_url = as_str(p.get("affiliate_url"))
        specs = p.get("specs") or {}

        meta_parts = []
        if brand:
            meta_parts.append(f"<strong>Brand:</strong> {escape(brand)}")
        if price is not None:
            meta_parts.append(f"<strong>Price:</strong> ${escape(str(price))}")
        meta_html = " &nbsp;\u2022&nbsp; ".join(meta_parts) if meta_parts else ""

        spec_bits = []
        w = specs.get("desktop_width_in")
        d = specs.get("desktop_depth_in")
        if w and d:
            spec_bits.append(f'<span>Desktop: {escape(str(w))}" \u00d7 {escape(str(d))}"</span>')
        h_min = specs.get("height_min_in")
        h_max = specs.get("height_max_in")
        if h_min and h_max:
            spec_bits.append(f'<span>Height: {escape(str(h_min))}" \u2013 {escape(str(h_max))}"</span>')
        cap = specs.get("weight_capacity_lbs")
        if cap:
            spec_bits.append(f"<span>Capacity: {escape(str(cap))} lbs</span>")
        motors = specs.get("motors")
        if motors:
            spec_bits.append(f"<span>Motors: {escape(str(motors))}</span>")
        warranty = specs.get("warranty_years")
        if warranty:
            spec_bits.append(f"<span>Warranty: {escape(str(warranty))} yr</span>")
        specs_html = " ".join(spec_bits) if spec_bits else ""

        aff_href = safe_url(affiliate_url)
        detail_href = escape(f"/generated/products/{slug}/", quote=True)
        name_href = escape(f"/generated/products/{slug}/", quote=True)

        card = f'<div class="product-card">'
        card += f'<h3><a href="{name_href}">{escape(name)}</a></h3>'
        if meta_html:
            card += f'<p class="card-meta">{meta_html}</p>'
        if specs_html:
            card += f'<div class="card-specs">{specs_html}</div>'
        card += f'<div class="card-actions">'
        card += f'<a class="cta" href="{aff_href}" target="_blank" rel="nofollow sponsored noopener">Buy / Check Price</a>'
        card += f'<a class="secondary" href="{detail_href}">View Details</a>'
        card += f'</div>'
        card += f'</div>'
        cards.append(card)

    html = "\n".join(cards)
    if matched and "product-card" not in html:
        raise RuntimeError("Category product list rendered without product cards.")
    return html


# ----------------------------
# SEO files: sitemap.xml + robots.txt
# ----------------------------

def get_site_url() -> str:
    site = (os.environ.get("SITE_URL") or DEFAULT_SITE_URL).strip().rstrip("/")
    return site


def build_sitemap(urls: List[str]) -> str:
    today = date.today().isoformat()
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    for u in urls:
        parts.append("  <url>")
        parts.append(f"    <loc>{escape(u)}</loc>")
        parts.append(f"    <lastmod>{today}</lastmod>")
        parts.append("  </url>")
    parts.append("</urlset>")
    parts.append("")
    return "\n".join(parts)


def build_robots(site_url: str) -> str:
    return "\n".join([
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {site_url}/sitemap.xml",
        ""
    ])


# ----------------------------
# Quality gates (Phase 1)
# ----------------------------

def run_quality_gates():
    failures: List[str] = []
    product_pages = sorted(GENERATED_DIR.glob("products/*/index.html"))
    category_pages = sorted(GENERATED_DIR.glob("categories/*/index.html"))

    for page in product_pages:
        rel = page.relative_to(GENERATED_DIR)
        content = page.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("import ") or line.startswith("from "):
                failures.append(f"[PYTHON LEAK] {rel}: line starts with '{line[:40]}...'")
                break
        if "<html" not in content.lower():
            failures.append(f"[MISSING <html>] {rel}")
        if "<title>" not in content.lower():
            failures.append(f"[MISSING <title>] {rel}")
        if "href=" not in content.lower():
            failures.append(f"[MISSING href=] {rel}: no links found (broken CTA?)")
        if "{{" in content and "}}" in content:
            failures.append(f"[UNREPLACED PLACEHOLDER] {rel}: raw " + "{{...}}" + " found in output")

    for page in category_pages:
        rel = page.relative_to(GENERATED_DIR)
        content = page.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("import ") or line.startswith("from "):
                failures.append(f"[PYTHON LEAK] {rel}: line starts with '{line[:40]}...'")
                break
        if "<html" not in content.lower():
            failures.append(f"[MISSING <html>] {rel}")
        if "<title>" not in content.lower():
            failures.append(f"[MISSING <title>] {rel}")
        if "<a href=" not in content.lower():
            failures.append(f"[MISSING <a href=] {rel}: no product links found")
        if "{{" in content and "}}" in content:
            failures.append(f"[UNREPLACED PLACEHOLDER] {rel}: raw " + "{{...}}" + " found in output")

    if failures:
        print("\n\u274c QUALITY GATE FAILURES:\n")
        for f in failures:
            print(f"   \u2022 {f}")
        print(f"\n   {len(failures)} failure(s) found. Build halted.\n")
        sys.exit(1)

    total = len(product_pages) + len(category_pages)
    print(f"\u2705 Quality gates passed ({len(product_pages)} product, {len(category_pages)} category pages checked).")


# ----------------------------
# Main
# ----------------------------

def main():
    categories = normalize_categories(load_json(CATEGORIES_JSON))
    category_template = read_text_required(CATEGORY_TEMPLATE)
    product_template = read_text_optional(PRODUCT_TEMPLATE, DEFAULT_PRODUCT_TEMPLATE)

    aff_config = load_affiliates_config()

    raw_products = load_all_products()
    products = normalize_products(raw_products)

    file_count = len(list(PRODUCTS_DIR.glob("*.json"))) if PRODUCTS_DIR.exists() else 0
    print(f"Loaded {len(products)} product(s) from {file_count} file(s) in data/products/")

    # Build category lookups for dynamic templates
    category_by_slug = {as_str(c.get("slug")): c for c in categories}

    # Set affiliate URLs and category context for each product
    for p in products:
        p["affiliate_url"] = build_affiliate_url(
            p.get("network_ids", {}), aff_config
        )

        # Resolve primary category and niche for dynamic sidebar
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

    # Generate category pages
    category_count = 0
    for cat in categories:
        cslug = as_str(cat.get("slug"))
        name = as_str(cat.get("name")) or cslug
        description = as_str(cat.get("description"))

        tags = cat.get("tags") or []
        match_tags = [normalize_slug(t) for t in tags if as_str(t)] or [cslug]

        matched = [p for p in products if product_matches(p, match_tags)]
        if not matched and products:
            matched = products

        product_list_html = build_category_product_list(matched)

        html = render_category_page(category_template, name, description, product_list_html)
        out_path = GENERATED_DIR / "categories" / cslug / "index.html"
        write_text(out_path, html)
        category_count += 1

    # SEO files
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

    run_quality_gates()

    print(f"Generated {category_count} category page(s).")
    print(f"Generated {product_count} product page(s).")
    print("Generated sitemap.xml and robots.txt.")


if __name__ == "__main__":
    main()
