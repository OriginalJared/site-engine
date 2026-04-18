import json
from pathlib import Path
from html import escape

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"
GENERATED_DIR = ROOT / "generated"

CATEGORIES_JSON = DATA_DIR / "categories.json"
PRODUCTS_JSON = DATA_DIR / "products.json"

CATEGORY_TEMPLATE = TEMPLATES_DIR / "category.html"
PRODUCT_TEMPLATE = TEMPLATES_DIR / "product.html"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_template_optional(path: Path, fallback: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return fallback


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str):
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def render_category_page(template: str, name: str, description: str, product_list_html: str) -> str:
    return (
        template
        .replace("{{CATEGORY_NAME}}", name)
        .replace("{{CATEGORY_DESCRIPTION}}", description)
        .replace("{{PRODUCT_LIST}}", product_list_html)
    )


def specs_table_html(specs: dict) -> str:
    if not isinstance(specs, dict) or not specs:
        return "<p>No specs available.</p>"
    rows = []
    for k, v in specs.items():
        rows.append(f"<tr><th>{escape(str(k))}</th><td>{escape(str(v))}</td></tr>")
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def render_product_page(template: str, p: dict) -> str:
    name = (p.get("name") or "").strip()
    brand = (p.get("brand") or "").strip()
    price = p.get("price_usd")
    affiliate_url = (p.get("affiliate_url") or "").strip()
    image_url = (p.get("image_url") or "").strip()
    best_for = p.get("best_for") or []
    specs = p.get("specs") or {}

    best_for_html = ""
    if isinstance(best_for, list) and best_for:
        best_for_html = "<ul>" + "".join(f"<li>{escape(str(x))}</li>" for x in best_for) + "</ul>"

    return (
        template
        .replace("{{PRODUCT_NAME}}", escape(name))
        .replace("{{PRODUCT_BRAND}}", escape(brand))
        .replace("{{PRODUCT_PRICE}}", escape(str(price) if price is not None else ""))
        .replace("{{AFFILIATE_URL}}", escape(affiliate_url))
        .replace("{{IMAGE_URL}}", escape(image_url))
        .replace("{{BEST_FOR}}", best_for_html)
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
    <p><a href="{{AFFILIATE_URL}}" rel="nofollow sponsored">Buy / Check Price</a></p>
    <div>{{BEST_FOR}}</div>
    <h2>Specs</h2>
    <div>{{SPECS_TABLE}}</div>
  </body>
</html>
"""


def main():
    categories = load_json(CATEGORIES_JSON)

    # category.html is required for now
    category_template = (TEMPLATES_DIR / "category.html").read_text(encoding="utf-8")

    # product.html is optional; fallback keeps workflow green even if missing
    product_template = load_template_optional(PRODUCT_TEMPLATE, DEFAULT_PRODUCT_TEMPLATE)

    products = []
    if PRODUCTS_JSON.exists():
        products = load_json(PRODUCTS_JSON)

    # Generate product pages
    product_count = 0
    for p in products:
        slug = (p.get("slug") or "").strip()
        if not slug:
            continue
        html = render_product_page(product_template, p)
        out_path = GENERATED_DIR / "products" / slug / "index.html"
        write_text(out_path, html)
        product_count += 1

    # Generate category pages (lists products for now)
    category_count = 0
    for cat in categories:
        slug = (cat.get("slug") or "").strip()
        name = (cat.get("name") or "").strip() or slug
        description = (cat.get("description") or "").strip()
        if not slug:
            continue

        # For now: list ALL products in every category page (we will filter later)
        items = []
        for p in products:
            pslug = (p.get("slug") or "").strip()
            pname = (p.get("name") or "").strip() or pslug
            price = p.get("price_usd")
            price_txt = f" — ${price}" if price is not None else ""
            items.append(f'<li><a href="/generated/products/{escape(pslug)}/">{escape(pname)}</a>{escape(price_txt)}</li>')

        product_list_html = "<ul>" + "".join(items) + "</ul>" if items else "<ul><li>No products yet.</li></ul>"

        html = render_category_page(category_template, name, description, product_list_html)
        out_path = GENERATED_DIR / "categories" / slug / "index.html"
        write_text(out_path, html)
        category_count += 1

    print(f"Generated {category_count} category page(s).")
    print(f"Generated {product_count} product page(s).")


if __name__ == "__main__":
    main()
