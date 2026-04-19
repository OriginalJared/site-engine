import json
import os
import re
from datetime import date
from pathlib import Path
from html import escape
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"
GENERATED_DIR = ROOT / "generated"

CATEGORIES_JSON = DATA_DIR / "categories.json"
PRODUCTS_JSON = DATA_DIR / "products.json"

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
    """
    Stable, URL-safe slug normalization:
      - lowercase
      - spaces/underscores -> hyphen
      - remove invalid chars
      - collapse repeated hyphens
    """
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
        affiliate_url = as_str(p.get("affiliate_url"))
        image_url = as_str(p.get("image_url"))
        price_usd = p.get("price_usd")

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
            "price_usd": price_usd,
            "affiliate_url": affiliate_url,
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
            "description": description,
            "tags": tags,
        })

    out.sort(key=lambda x: x["slug"])
    assert_unique_slugs(out, "category")
    return out


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
        return "<p>—</p>"
    return "<ul>" + "".join(f"<li>{escape(t)}</li>" for t in tags) + "</ul>"


def safe_url(url: str) -> str:
    """
    Return a safe, escaped URL string for use in href= or src= attributes.
    Returns '#' if the URL is empty/missing.
    """
    url = as_str(url)
    if not url:
        return "#"
    return escape(url, quote=True)


def render_product_page(template: str, p: Dict[str, Any]) -> str:
    """
    CONTRACT: The product template uses plain-URL placeholders.
      - {{AFFILIATE_URL}} goes inside href="..."   -> we supply a URL string
      - {{IMAGE_URL}}     goes inside src="..."     -> we supply a URL string
      - {{PRODUCT_NAME}}, {{PRODUCT_BRAND}}, {{PRODUCT_PRICE}} -> escaped text
      - {{BEST_FOR}}      -> rendered HTML (<ul> with <li> items)
      - {{SPECS_TABLE}}   -> rendered HTML (<table>)
    """
    name = as_str(p.get("name"))
    brand = as_str(p.get("brand"))
    price = p.get("price_usd")
    affiliate_url = as_str(p.get("affiliate_url"))
    img_url = as_str(p.get("image_url"))
    best_for = p.get("best_for") or []
    specs = p.get("specs") or {}

    price_txt = "" if price is None else str(price)

    return (
        template
        .replace("{{PRODUCT_NAME}}", escape(name))
        .replace("{{PRODUCT_BRAND}}", escape(brand))
        .replace("{{PRODUCT_PRICE}}", escape(price_txt))
        .replace("{{AFFILIATE_URL}}", safe_url(affiliate_url))
        .replace("{{IMAGE_URL}}", safe_url(img_url))
        .replace("{{BEST_FOR}}", best_for_html(best_for))
        .replace("{{SPECS_TABLE}}", specs_table_html(specs))
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
    <h2>Best For</h2>
    <div>{{BEST_FOR}}</div>
    <h2>Specs</h2>
    <div>{{SPECS_TABLE}}</div>
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
        return "<ul><li>No products yet.</li></ul>"

    items = []
    for p in matched:
        slug = as_str(p.get("slug"))
        name = as_str(p.get("name")) or slug
        brand = as_str(p.get("brand"))
        price = p.get("price_usd")

        meta_bits = []
        if brand:
            meta_bits.append(brand)
        if price is not None:
            meta_bits.append(f"${price}")
        meta = " — " + " • ".join(escape(x) for x in meta_bits) if meta_bits else ""

        href = f"/generated/products/{slug}/"
        href_attr = escape(href, quote=True)
        items.append(f'<li><a href="{href_attr}">{escape(name)}</a>{meta}</li>')

    html = "<ul>" + "".join(items) + "</ul>"

    # Guardrail: prevent silent broken links
    if matched and '<a href="' not in html:
        raise RuntimeError("Category product list rendered without anchor tags.")
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


def main():
    categories = normalize_categories(load_json(CATEGORIES_JSON))
    category_template = read_text_required(CATEGORY_TEMPLATE)
    product_template = read_text_optional(PRODUCT_TEMPLATE, DEFAULT_PRODUCT_TEMPLATE)

    products: List[Dict[str, Any]] = []
    if PRODUCTS_JSON.exists():
        products = normalize_products(load_json(PRODUCTS_JSON))

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

    # Generate category pages (filter by tags; fallback to all products early on)
    category_count = 0
    for cat in categories:
        cslug = as_str(cat.get("slug"))
        name = as_str(cat.get("name")) or cslug
        description = as_str(cat.get("description"))

        tags = cat.get("tags") or []
        match_tags = [normalize_slug(t) for t in tags if as_str(t)] or [cslug]

        matched = [p for p in products if product_matches(p, match_tags)]
        if not matched and products:
            matched = products  # early-stage safety fallback

        product_list_html = build_category_product_list(matched)

        html = render_category_page(category_template, name, description, product_list_html)
        out_path = GENERATED_DIR / "categories" / cslug / "index.html"
        write_text(out_path, html)
        category_count += 1

    # Generate sitemap.xml + robots.txt at repo root
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

    print(f"Generated {category_count} category page(s).")
    print(f"Generated {product_count} product page(s).")
    print("Generated sitemap.xml and robots.txt.")


if __name__ == "__main__":
    main()
