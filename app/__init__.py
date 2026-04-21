from functools import lru_cache
from pathlib import Path
import hashlib
import re
import unicodedata
from datetime import date

import sqlite3
import requests
import pandas as pd
from flask import Flask, jsonify, render_template, request, session, redirect
import json
import urllib.request as urllib
from bs4 import BeautifulSoup

app = Flask(__name__)
DATA_PATH = Path(__file__).resolve().parent / "data" / "IKEA_product_catalog.csv"
KEY_PATH = Path(__file__).resolve().parent / "keys" / "key_exchangerate-api.txt"
DB_PATH = Path(__file__).resolve().parent / "data.db"
MAX_PRODUCT_TYPES = 30
DEFAULT_TARGET_CURRENCY = "USD"
CATALOG_SCHEMA_VERSION = 7
CATALOG_USECOLS = [
    "product_id",
    "product_name",
    "product_type",
    "product_description",
    "main_category",
    "badge",
    "discount",
    "sale_tag",
    "country",
    "price",
    "currency",
    "product_rating",
    "product_rating_count",
    "url",
]
app.secret_key = "6767"

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def db_value(value):
    if pd.isna(value):
        return None
    return value

def slugify_product_name(product_name):
    ascii_value = unicodedata.normalize("NFKD", str(product_name)).encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower()
    ascii_value = re.sub(r"[^a-z0-9]+", "-", ascii_value)
    return ascii_value.strip("-")

def make_group_id(product_name, anchor_key):
    if anchor_key == "name":
        return slugify_product_name(product_name)
    return hashlib.md5(f"{product_name}|{anchor_key}".encode("utf-8")).hexdigest()

def display_category(raw):
    if not raw:
        return "Other"
    category = str(raw).strip().lower()
    if "bathroom" in category:
        return "Bathroom"
    if "kitchen" in category or "cookware" in category or "tableware" in category or "dishwash" in category:
        return "Kitchen & Dining"
    if "storage" in category or "organis" in category or "organizer" in category or "garage" in category or "closet" in category:
        return "Storage"
    if "outdoor" in category or "picnic" in category:
        return "Outdoor"
    if "sofa" in category or "armchair" in category or "living" in category:
        return "Living Room"
    if "bed" in category or "mattress" in category or "wardrobe" in category or "bedroom" in category:
        return "Bedroom"
    if "light" in category or "lamp" in category:
        return "Lighting"
    if "decor" in category or "mirror" in category:
        return "Decor"
    return "Other"

def load_catalog_source_csv():
    catalog = pd.read_csv(DATA_PATH, usecols=CATALOG_USECOLS)
    catalog["product_id"] = catalog["product_id"].astype(str)
    catalog["display_category"] = catalog["main_category"].apply(display_category)
    return catalog

def catalog_query_df(query, params=()):
    with get_db_connection() as db:
        return pd.read_sql_query(query, db, params=params)

def load_catalog():
    return get_catalog_df().copy()

@lru_cache(maxsize=1)
def get_catalog_df():
    return catalog_query_df(
        "SELECT product_id, product_name, product_type, product_description, main_category, badge, discount, sale_tag, country, price, currency, "
        "product_rating, product_rating_count, url, display_category "
        "FROM catalog_items"
    )

def format_catalog_tag(value):
    if value is None:
        return None
    tag = str(value).strip()
    if not tag or tag.lower() == "none":
        return None
    return tag.replace("_", " ").title()

def clean_dropdown_description(product_name, product_description):
    if product_description is None:
        return ""
    description = str(product_description).strip()
    if not description:
        return ""
    product_name_text = str(product_name).strip()
    if description.lower().startswith(product_name_text.lower()):
        description = description[len(product_name_text):].lstrip(" ,.-:")
    return description.strip()

def choose_group_description(product_name, product_rows):
    english_rows = product_rows[product_rows["country"].isin(["USA", "UK", "Canada", "Australia", "New_Zealand", "Ireland"])]
    candidate_rows = english_rows if not english_rows.empty else product_rows
    descriptions = []
    for value in candidate_rows["product_description"].dropna().tolist():
        cleaned = clean_dropdown_description(product_name, value)
        if cleaned:
            descriptions.append(cleaned)
    if not descriptions:
        return ""
    descriptions = sorted(set(descriptions), key=lambda description: (len(description), description.lower()))
    return descriptions[0]

def extract_image_url(product_url):
    if not product_url:
        return None
    try:
        response = requests.get(product_url, timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        return og_image["content"]

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        script_text = script.string or script.get_text(strip=True)
        if not script_text:
            continue
        try:
            payload = json.loads(script_text)
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = payload if isinstance(payload, list) else [payload]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            image_value = item.get("image")
            if isinstance(image_value, str) and image_value:
                return image_value
            if isinstance(image_value, list) and image_value:
                first_image = image_value[0]
                if isinstance(first_image, str) and first_image:
                    return first_image

    return None


def load_exchange_rate_api_key():
  if KEY_PATH.exists():
    return KEY_PATH.read_text(encoding="utf-8").strip()
  raise FileNotFoundError("Missing ExchangeRate API key file.")


def get_exchange_rate_url(path):
  api_key = load_exchange_rate_api_key()
  return f"https://v6.exchangerate-api.com/v6/{api_key}/{path}"


@lru_cache(maxsize=1)
def get_supported_currencies():
  response = requests.get(get_exchange_rate_url("codes"), timeout=20)
  response.raise_for_status()
  payload = response.json()
  if payload.get("result") != "success":
    raise ValueError("ExchangeRate API did not return success for supported codes.")
  return [code for code, _name in payload["supported_codes"]]


@lru_cache(maxsize=64)
def get_conversion_rates(base_currency):
  response = requests.get(get_exchange_rate_url(f"latest/{base_currency}"), timeout=20)
  response.raise_for_status()
  payload = response.json()
  if payload.get("result") != "success":
    raise ValueError(f"ExchangeRate API did not return success for base currency {base_currency}.")
  return payload["conversion_rates"]


def get_countries():
  catalog = get_catalog_df()
  return sorted(catalog["country"].dropna().astype(str).unique().tolist())

def convert_price(amount, source_currency, target_currency):
  if source_currency == target_currency:
    return amount
  rates = get_conversion_rates(source_currency)
  return amount * rates[target_currency]

def get_product_types():
    catalog = load_catalog()
    return sorted(catalog["product_type"].dropna().astype(str).unique().tolist())

def build_product_groups(catalog):
    catalog["product_id"] = catalog["product_id"].astype(str)
    catalog["product_group_id"] = catalog["product_name"].apply(lambda product_name: make_group_id(product_name, "name"))
    group_rows = []
    for product_name, product_rows in catalog.groupby("product_name", sort=False):
        group_id = make_group_id(product_name, "name")
        description = choose_group_description(product_name, product_rows)
        label = f"{product_name} - {description}" if description else product_name
        representative_url = next(
            (str(url).strip() for url in product_rows["url"].tolist() if pd.notna(url) and str(url).strip()),
            None
        )
        group_rows.append({
            "group_id": group_id,
            "product_name": product_name,
            "label": label,
            "product_description": description,
            "product_page_url": representative_url,
            "image_url": None,
            "countries_count": int(product_rows["country"].nunique()),
        })

    product_groups = pd.DataFrame(group_rows).sort_values("product_name")
    group_lookup = {
        row["group_id"]: {
            "group_id": row["group_id"],
            "product_name": row["product_name"],
            "label": row["label"],
            "product_description": row["product_description"],
            "product_page_url": row["product_page_url"],
            "image_url": row["image_url"],
            "countries_count": int(row["countries_count"]),
        }
        for row in product_groups.to_dict(orient="records")
    }
    return catalog, group_lookup

def init_user_db():
    with get_db_connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS user_base(username TEXT, password TEXT, path TEXT, saved TEXT);")
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(user_base)").fetchall()
        }
        if "creation_date" not in columns:
            db.execute("ALTER TABLE user_base ADD COLUMN creation_date TEXT")
            db.execute(
                "UPDATE user_base SET creation_date = ? WHERE creation_date IS NULL",
                (date.today().isoformat(),)
            )
        if "bio" not in columns:
            db.execute("ALTER TABLE user_base ADD COLUMN bio TEXT")
            db.execute("UPDATE user_base SET bio = '' WHERE bio IS NULL")

def catalog_db_is_fresh():
    if not DB_PATH.exists():
        return False
    with get_db_connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS catalog_meta(schema_version INTEGER, data_mtime_ns INTEGER);")
        meta = db.execute(
            "SELECT schema_version, data_mtime_ns FROM catalog_meta ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if meta is None:
            return False
        table_names = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    required_tables = {"catalog_meta", "catalog_items", "product_groups", "catalog_grouped_rows", "user_base"}
    return (
        meta[0] == CATALOG_SCHEMA_VERSION
        and meta[1] == DATA_PATH.stat().st_mtime_ns
        and required_tables.issubset(table_names)
    )

def rebuild_catalog_db():
    catalog = load_catalog_source_csv()
    grouped_catalog, group_lookup = build_product_groups(catalog.copy())

    product_group_rows = pd.DataFrame(sorted(group_lookup.values(), key=lambda product: product["label"]))
    product_group_rows["countries_count"] = (
        grouped_catalog.groupby("product_group_id")["country"]
        .nunique()
        .reindex(product_group_rows["group_id"])
        .fillna(0)
        .astype(int)
        .values
    )

    grouped_rows = grouped_catalog[
        ["product_group_id", "product_name", "country", "price", "currency"]
    ].dropna(subset=["product_group_id", "country", "price", "currency", "product_name"])

    with get_db_connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS catalog_meta(schema_version INTEGER, data_mtime_ns INTEGER)")
        db.execute("DROP TABLE IF EXISTS catalog_items")
        db.execute("DROP TABLE IF EXISTS product_groups")
        db.execute("DROP TABLE IF EXISTS catalog_grouped_rows")
        db.execute(
            "CREATE TABLE catalog_items("
            "product_id TEXT NOT NULL, "
            "product_name TEXT, "
            "product_type TEXT, "
            "product_description TEXT, "
            "main_category TEXT, "
            "badge TEXT, "
            "discount TEXT, "
            "sale_tag TEXT, "
            "country TEXT, "
            "price REAL, "
            "currency TEXT, "
            "product_rating TEXT, "
            "product_rating_count TEXT, "
            "url TEXT, "
            "display_category TEXT)"
        )
        db.execute(
            "CREATE TABLE product_groups("
            "group_id TEXT PRIMARY KEY, "
            "product_name TEXT NOT NULL, "
            "label TEXT NOT NULL, "
            "product_description TEXT, "
            "product_page_url TEXT, "
            "image_url TEXT, "
            "countries_count INTEGER NOT NULL)"
        )
        db.execute(
            "CREATE TABLE catalog_grouped_rows("
            "product_group_id TEXT NOT NULL, "
            "product_name TEXT NOT NULL, "
            "country TEXT NOT NULL, "
            "price REAL NOT NULL, "
            "currency TEXT NOT NULL)"
        )
        db.executemany(
            "INSERT INTO catalog_items(product_id, product_name, product_type, product_description, main_category, badge, discount, sale_tag, country, price, currency, "
            "product_rating, product_rating_count, url, display_category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["product_id"],
                    db_value(row["product_name"]),
                    db_value(row["product_type"]),
                    db_value(row["product_description"]),
                    db_value(row["main_category"]),
                    db_value(row["badge"]),
                    db_value(row["discount"]),
                    db_value(row["sale_tag"]),
                    db_value(row["country"]),
                    db_value(row["price"]),
                    db_value(row["currency"]),
                    db_value(row["product_rating"]),
                    db_value(row["product_rating_count"]),
                    db_value(row["url"]),
                    db_value(row["display_category"]),
                )
                for row in catalog.to_dict(orient="records")
            ]
        )
        db.executemany(
            "INSERT INTO product_groups(group_id, product_name, label, product_description, product_page_url, image_url, countries_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["group_id"],
                    row["product_name"],
                    row["label"],
                    db_value(row["product_description"]),
                    db_value(row["product_page_url"]),
                    db_value(row["image_url"]),
                    int(row["countries_count"]),
                )
                for row in product_group_rows.to_dict(orient="records")
            ]
        )
        db.executemany(
            "INSERT INTO catalog_grouped_rows(product_group_id, product_name, country, price, currency) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    row["product_group_id"],
                    row["product_name"],
                    row["country"],
                    float(row["price"]),
                    row["currency"],
                )
                for row in grouped_rows.to_dict(orient="records")
            ]
        )
        db.execute("DELETE FROM catalog_meta")
        db.execute(
            "INSERT INTO catalog_meta(schema_version, data_mtime_ns) VALUES (?, ?)",
            (CATALOG_SCHEMA_VERSION, DATA_PATH.stat().st_mtime_ns)
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_catalog_items_country ON catalog_items(country)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_catalog_items_product_id ON catalog_items(product_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_catalog_items_product_name ON catalog_items(product_name)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_catalog_items_product_type ON catalog_items(product_type)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_catalog_items_display_category ON catalog_items(display_category)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_product_groups_label ON product_groups(label)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_product_groups_group_id ON product_groups(group_id)")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalog_grouped_rows_group_country "
            "ON catalog_grouped_rows(product_group_id, country)"
        )
    get_catalog_df.cache_clear()

def ensure_catalog_db():
    init_user_db()
    if not catalog_db_is_fresh():
        rebuild_catalog_db()

ensure_catalog_db()

def get_random():
    catalog_s = get_catalog_df()
    return catalog_s.sample()

def fetch_product_group_media(group_ids):
    if not group_ids:
        return {}
    placeholders = ", ".join(["?"] * len(group_ids))
    with get_db_connection() as db:
        rows = db.execute(
            f"SELECT group_id, product_page_url, image_url FROM product_groups WHERE group_id IN ({placeholders})",
            tuple(group_ids)
        ).fetchall()
    return {
        row[0]: {
            "product_page_url": row[1],
            "image_url": row[2],
        }
        for row in rows
    }

def update_product_group_image(group_id, image_url):
    with get_db_connection() as db:
        db.execute(
            "UPDATE product_groups SET image_url = ? WHERE group_id = ?",
            (image_url, group_id)
        )

def get_or_fetch_product_image(product_group_id):
    media_map = fetch_product_group_media([product_group_id])
    media = media_map.get(product_group_id)
    if not media:
        return None
    if media["image_url"]:
        return media["image_url"]
    if not media["product_page_url"]:
        return None

    image_url = extract_image_url(media["product_page_url"])
    if image_url:
        update_product_group_image(product_group_id, image_url)
    return image_url

def populate_missing_group_images(group_ids):
    media_map = fetch_product_group_media(group_ids)
    updates = []
    for group_id in group_ids:
        media = media_map.get(group_id)
        if not media or media["image_url"] or not media["product_page_url"]:
            continue
        image_url = extract_image_url(media["product_page_url"])
        if image_url:
            updates.append((image_url, group_id))

    if updates:
        with get_db_connection() as db:
            db.executemany(
                "UPDATE product_groups SET image_url = ? WHERE group_id = ?",
                updates
            )

    return fetch_product_group_media(group_ids)


def get_product_options():
    with get_db_connection() as db:
        rows = db.execute(
            "SELECT group_id, product_name, label, product_description, product_page_url, image_url, countries_count "
            "FROM product_groups ORDER BY label"
        ).fetchall()
    return [
        {
            "group_id": row[0],
            "product_name": row[1],
            "label": row[2],
            "product_description": row[3],
            "product_page_url": row[4],
            "image_url": row[5],
            "countries_count": row[6],
        }
        for row in rows
    ]

def get_multi_country_group_ids():
    with get_db_connection() as db:
        rows = db.execute(
            "SELECT group_id FROM product_groups WHERE countries_count > 1"
        ).fetchall()
    return {row[0] for row in rows}

def get_product_label(product_group_id):
    with get_db_connection() as db:
        row = db.execute(
            "SELECT label FROM product_groups WHERE group_id = ?",
            (product_group_id,)
        ).fetchone()
    if row is None:
        return None
    return row[0]

def get_product_name(product_group_id):
    with get_db_connection() as db:
        row = db.execute(
            "SELECT product_name FROM product_groups WHERE group_id = ?",
            (product_group_id,)
        ).fetchone()
    if row is None:
        return None
    return row[0]

def get_product_summary(product_group_id):
    product_name = get_product_name(product_group_id)
    if not product_name:
        return None

    with get_db_connection() as db:
        row = db.execute(
            "SELECT product_name, product_description, product_type, display_category, url "
            "FROM catalog_items WHERE product_name = ? "
            "ORDER BY CASE WHEN product_description IS NULL OR TRIM(product_description) = '' THEN 1 ELSE 0 END, country "
            "LIMIT 1",
            (product_name,)
        ).fetchone()
    if row is None:
        return None

    return {
        "product_name": row[0],
        "product_description": row[1] or "N/A",
        "product_type": row[2] or "N/A",
        "display_category": row[3] or "N/A",
        "url": row[4] or "N/A",
    }

def parse_saved_items(saved_value):
    if not saved_value:
        return []
    return [item for item in str(saved_value).split(", ") if item and item != " "]

def get_saved_product_entries(saved_value):
    saved_ids = parse_saved_items(saved_value)
    if not saved_ids:
        return []
    entries = []
    for product_group_id in saved_ids:
        label = get_product_label(product_group_id)
        if label:
            entries.append({
                "group_id": product_group_id,
                "label": label,
            })
    return entries

def build_demo_data(country, target_currency):
  catalog = get_catalog_df()
  filtered = catalog[catalog["country"] == country].dropna(subset=["product_type", "price", "currency"]).copy()
  top_product_types = (
    filtered["product_type"]
    .value_counts()
    .head(MAX_PRODUCT_TYPES)
    .index
  )
  filtered = filtered[filtered["product_type"].isin(top_product_types)]
  filtered["price"] = filtered.apply(
    lambda row: convert_price(row["price"], row["currency"], target_currency),
    axis=1
  )
  grouped = (
    filtered.groupby("product_type", as_index=False)["price"]
    .mean()
    .sort_values("price", ascending=False)
  )
  grouped["price"] = grouped["price"].round(2)
  return grouped.to_dict(orient="records")

def build_choropleth_data(product_type, target_currency):
  catalog = get_catalog_df()
  filtered = catalog[catalog["product_type"] == product_type].dropna(subset=["country", "price", "currency"]).copy()
  filtered["price"] = filtered.apply(
      lambda row: convert_price(row["price"], row["currency"], target_currency),
      axis=1
  )
  grouped = (
    filtered.groupby("country", as_index=False)["price"]
    .mean()
    .sort_values("price", ascending=False)
  )
  grouped["price"] = grouped["price"].round(2)
  return grouped.to_dict(orient="records")

def build_product_country_price_data(product_group_id, target_currency):
    with get_db_connection() as db:
        rows = db.execute(
            "SELECT product_group_id, product_name, country, price, currency "
            "FROM catalog_grouped_rows WHERE product_group_id = ?",
            (str(product_group_id),)
        ).fetchall()
    if not rows:
        return []

    country_totals = {}
    product_name = rows[0][1]
    for row in rows:
        converted_price = round(convert_price(row[3], row[4], target_currency), 2)
        country_entry = country_totals.setdefault(row[2], {"sum": 0.0, "count": 0})
        country_entry["sum"] += converted_price
        country_entry["count"] += 1

    return [
        {
            "product_group_id": str(product_group_id),
            "product_name": product_name,
            "country": country,
            "price": round(values["sum"] / values["count"], 2),
        }
        for country, values in sorted(country_totals.items())
    ]

def build_product_country_rating_data(product_group_id):
    product_name = get_product_name(product_group_id)
    if not product_name:
        return []

    with get_db_connection() as db:
        rows = db.execute(
            "SELECT country, product_rating "
            "FROM catalog_items WHERE product_name = ? AND product_rating IS NOT NULL AND product_rating != 'none'",
            (product_name,)
        ).fetchall()
    if not rows:
        return []

    country_totals = {}
    for country, rating in rows:
        try:
            numeric_rating = float(rating)
        except (TypeError, ValueError):
            continue
        country_entry = country_totals.setdefault(country, {"sum": 0.0, "count": 0})
        country_entry["sum"] += numeric_rating
        country_entry["count"] += 1

    return [
        {
            "product_group_id": str(product_group_id),
            "product_name": product_name,
            "country": country,
            "rating": round(values["sum"] / values["count"], 2),
        }
        for country, values in sorted(country_totals.items())
    ]

# ================= Catalog Helpers ===================
def get_categories():
    catalog = get_catalog_df()
    return sorted(catalog["display_category"].dropna().unique())

def get_catalog_items(page=1, limit=51, country=None, category=None, search=None):
    catalog = get_catalog_df()
    if category:
        catalog = catalog[catalog["display_category"] == category]
    if search:
        catalog = catalog[catalog["product_name"].str.contains(search, case=False, na=False)]
    if country:
        catalog = catalog[catalog["country"] == country]
    else:
        eng = ["USA", "UK", "Canada", "Australia", "New_Zealand"]
        catalog = pd.concat([
            catalog[catalog["country"].isin(eng)],
            catalog[~catalog["country"].isin(eng)]
        ])

    grouped = catalog.drop_duplicates(subset="product_name", keep="first").copy()
    grouped["product_group_id"] = grouped["product_name"].apply(lambda product_name: make_group_id(product_name, "name"))
    grouped = grouped[grouped["product_group_id"].isin(get_multi_country_group_ids())]
    grouped = grouped.sort_values("product_name").reset_index(drop=True)
    grouped["catalog_tags"] = grouped.apply(
        lambda row: [
            tag for tag in [
                format_catalog_tag(row.get("badge")),
                format_catalog_tag(row.get("discount")),
                format_catalog_tag(row.get("sale_tag")),
            ] if tag
        ],
        axis=1
    )
    total = len(grouped)
    start = (page-1) * limit
    grouped = grouped[start:(start + limit)]
    group_ids = grouped["product_group_id"].tolist()
    media_map = fetch_product_group_media(group_ids)
    grouped["image_url"] = grouped["product_group_id"].map(
        lambda group_id: media_map.get(group_id, {}).get("image_url")
    )
    return grouped.to_dict(orient="records"), total
# ===================================================


@app.route("/", methods=["GET", "POST"])
def homepage():
  if not 'u_rowid' in session:
  	return redirect("/login")
  if get_session_user_rowid() is None or get_current_user_saved_value() is None:
    return redirect("/login")
  rand = get_random()
  return render_template("index.html", products=get_product_options(), rand=rand)

@app.route("/product_graph", methods=["POST"])
def product_graph_redirect():
  if not 'u_rowid' in session:
    return redirect("/login")
  product_group_id = request.form.get("product_group_id", "").strip()
  if not product_group_id:
    return redirect("/")
  return redirect(f"/product_graph/{product_group_id}")

@app.route("/product_graph/<product_group_id>")
def product_graph(product_group_id):
    if not 'u_rowid' in session:
        return redirect("/login")
    saved_value = get_current_user_saved_value()
    if saved_value is None:
        return redirect("/login")
    supported_currencies = get_supported_currencies()
    price_chart_data = build_product_country_price_data(product_group_id, DEFAULT_TARGET_CURRENCY)
    if not price_chart_data:
        return redirect("/")
    rating_chart_data = build_product_country_rating_data(product_group_id)

    product_label = get_product_label(product_group_id)
    if not product_label:
        return redirect("/")
    product_summary = get_product_summary(product_group_id)
    product_image_url = get_or_fetch_product_image(product_group_id)
    is_saved = product_group_id in parse_saved_items(saved_value)

    return render_template(
        "product_country_graph.html",
        product_group_id=product_group_id,
        product_label=product_label,
        product_summary=product_summary,
        product_image_url=product_image_url,
        is_saved=is_saved,
        price_chart_data=price_chart_data,
        rating_chart_data=rating_chart_data,
        currencies=supported_currencies,
        default_currency=DEFAULT_TARGET_CURRENCY
    )

@app.route("/save_product/<product_group_id>", methods=["GET"])
def save_product(product_group_id):
    if 'u_rowid' not in session:
        return redirect("/login")

    user_rowid = get_session_user_rowid()
    saved_value = get_current_user_saved_value()
    if user_rowid is None or saved_value is None:
        return redirect("/login")
    saved_items = parse_saved_items(saved_value)
    if product_group_id not in saved_items and get_product_label(product_group_id):
        updated_saved = ", ".join(saved_items + [product_group_id]) if saved_items else product_group_id
        db = get_db_connection()
        c = db.cursor()
        c.execute("UPDATE user_base SET saved = ? WHERE ROWID = ?", (updated_saved, user_rowid))
        db.commit()
        db.close()

    return redirect(f"/product_graph/{product_group_id}")

@app.route("/login", methods=["GET", "POST"])
def login():
  if request.method == 'POST':
    usernames = [row[0] for row in fetch("user_base", "TRUE", "username")]
    if not request.form['username'] in usernames:
      return render_template("login.html",
                             error="Wrong &nbsp username &nbsp or &nbsp password!<br><br>")
    elif request.form['password'] != fetch("user_base", "username = ?", "password", (request.form['username'],))[0][0]:
      return render_template("login.html",
                             error="Wrong &nbsp username &nbsp or &nbsp password!<br><br>")
    else:
      session["u_rowid"] = fetch("user_base", "username = ?", "rowid", (request.form['username'],))[0][0]
    if 'u_rowid' in session:
      return redirect("/")
    session.clear()
  return render_template("login.html")

@app.route('/register', methods=["GET", "POST"])
def register():
    if 'u_rowid' in session:
        return redirect("/")
    if request.method == "POST":
        if not request.form['password'] == request.form['confirm']:
            return render_template("register.html",
                                   error="Passwords do not match, please try again! <br><br>")
        if not create_user(request.form['username'], request.form['password']):
            return render_template("register.html",
                                   error="Username already taken, please try again! <br><br>")
        else:
            return redirect("/login")
    return render_template("register.html")

@app.route('/logout', methods=["GET", "POST"])
def logout():
    session.pop("u_rowid", None)
    return redirect("/login")


@app.route('/profile', methods=["GET", "POST"])
def profileDefault():
    if not 'u_rowid' in session:
        return redirect("/login")
    user_rowid = get_session_user_rowid()
    if user_rowid is None:
        return redirect("/login")
    return redirect(f"/profile/{user_rowid}")

@app.route('/profile/<u_rowid>', methods=["GET", "POST"]) # makes u_rowid a variable that is passed to the function
def profile(u_rowid):
    if not 'u_rowid' in session:
        return redirect("/login")
    current_user_rowid = get_session_user_rowid()
    if current_user_rowid is None:
        return redirect("/login")
    if str(current_user_rowid) != str(u_rowid):
        return redirect(f"/profile/{current_user_rowid}")

    error = ""
    success = ""
    if request.method == "POST":
        old_password = request.form.get("old_password", "")
        new_password = request.form.get("new_password", "")
        password_rows = fetch('user_base', "ROWID=?", 'password', (u_rowid,))
        if not password_rows:
            session.pop("u_rowid", None)
            return redirect("/login")
        u_password = password_rows[0][0]

        if old_password != u_password:
            error = "Old password is incorrect."
        elif not new_password:
            error = "New password cannot be empty."
        else:
            db = get_db_connection()
            c = db.cursor()
            c.execute("UPDATE user_base SET password = ? WHERE ROWID = ?", (new_password, u_rowid))
            db.commit()
            db.close()
            success = "Password updated successfully."

    user_rows = fetch('user_base', "ROWID=?", 'username, password, creation_date, bio, saved', (u_rowid,))
    if not user_rows:
        session.pop("u_rowid", None)
        return redirect("/login")
    u_data = user_rows[0]
    return render_template("profile.html",
        username=u_data[0],
        password=u_data[1],
        creation_date=u_data[2],
        bio=u_data[3] or "",
        saved_items=get_saved_product_entries(u_data[4]),
        error=error,
        success=success)


@app.route("/catalog")
def catalog():
    countries = get_countries()
    categories = get_categories()
    selected_country = request.args.get("country", "")
    selected_category = request.args.get("category", "")
    search = request.args.get("search", "")
    page = int(request.args.get("page", 1))
    limit = 51;

    items, total = get_catalog_items(
        page=page,
        limit=limit,
        country=selected_country or None,
        category=selected_category or None,
        search=search or None
    )
    return render_template(
        "catalog.html",
        items=items,
        countries=countries,
        categories=categories,
        selected_country=selected_country,
        selected_category=selected_category,
        search=search,
        page=page,
        has_prev=page > 1,
        has_next=page*limit < total,
        total=total
    )

@app.route("/save/<product_id>", methods=["GET"])
def cave(product_id):
    if 'u_rowid' in session:
        user_rowid = get_session_user_rowid()
        saved_value = get_current_user_saved_value()
        if user_rowid is None or saved_value is None:
            return redirect("/login")
        if product_id not in saved_value.split(", "):
            db = get_db_connection()
            c = db.cursor()
            c.execute("UPDATE user_base SET saved = ? WHERE ROWID=?",
                (fetch("user_base", f"ROWID={user_rowid}", "saved")[0][0] + ", " + product_id,
                    user_rowid))
            db.commit()
            db.close()
    return redirect(f"/product/{product_id}")

@app.route("/demo_graph")
def demo_graph():
  countries = get_countries()
  default_country = "USA" if "USA" in countries else countries[0]
  supported_currencies = get_supported_currencies()
  return render_template(
    "demo_graph.html",
    countries=countries,
    default_country=default_country,
    currencies=supported_currencies,
    default_currency=DEFAULT_TARGET_CURRENCY
  )


@app.route("/choropleth")
def choropleth():
  product_types = get_product_types()
  default_product_type = product_types[0] if product_types else ""
  supported_currencies = get_supported_currencies()
  return render_template(
    "choropleth.html",
    product_types=product_types,
    default_product_type=default_product_type,
    currencies=supported_currencies,
    default_currency=DEFAULT_TARGET_CURRENCY
  )

@app.route("/api/choropleth_data")
def choropleth_data():
  product_types = get_product_types()
  default_product_type = product_types[0] if product_types else ""
  product_type = request.args.get("product_type", default_product_type)
  target_currency = request.args.get("target_currency", DEFAULT_TARGET_CURRENCY)
  if product_type not in product_types:
    return jsonify({"error": "Unknown product type"}), 400
  if target_currency not in get_supported_currencies():
    return jsonify({"error": "Unknown target currency"}), 400

  return jsonify({
    "product_type": product_type,
    "target_currency": target_currency,
    "data": build_choropleth_data(product_type, target_currency)
  })

@app.route("/api/demo_graph_data")
def demo_graph_data():
  countries = get_countries()
  default_country = "USA" if "USA" in countries else countries[0]
  country = request.args.get("country", default_country)
  target_currency = request.args.get("target_currency", DEFAULT_TARGET_CURRENCY)
  if country not in countries:
    return jsonify({"error": "Unknown country"}), 400
  if target_currency not in get_supported_currencies():
    return jsonify({"error": "Unknown target currency"}), 400

  return jsonify({
    "country": country,
    "target_currency": target_currency,
    "data": build_demo_data(country, target_currency)
  })

@app.route("/api/product_graph_data/<product_group_id>")
def product_graph_data(product_group_id):
  target_currency = request.args.get("target_currency", DEFAULT_TARGET_CURRENCY)
  if target_currency not in get_supported_currencies():
    return jsonify({"error": "Unknown target currency"}), 400

  data = build_product_country_price_data(product_group_id, target_currency)
  if not data:
    return jsonify({"error": "No product data found"}), 404

  return jsonify({
    "product_group_id": product_group_id,
    "target_currency": target_currency,
    "data": data
  })

@app.route("/api/product_rating_graph_data/<product_group_id>")
def product_rating_graph_data(product_group_id):
  data = build_product_country_rating_data(product_group_id)
  if not data:
    return jsonify({"error": "No product rating data found"}), 404

  return jsonify({
    "product_group_id": product_group_id,
    "data": data
  })

def get_url(curr):
  return f'https://v6.exchangerate-api.com/v6/c608df1404c6c6b0bf2cd5bb/latest/{curr}'

@app.route("/api_testing")
def api_testing():
  with urllib.urlopen(get_url("AED")) as response:
    json_data = response.read()

  apod_data = json.loads(json_data)

  json_string = json.dumps(apod_data, indent=2)
  print(json_string)
  return json_string

def fetch(table, criteria, data, params = ()):
    db = get_db_connection()
    c = db.cursor()
    query = f"SELECT {data} FROM {table} WHERE {criteria}"
    c.execute(query, params)
    data = c.fetchall()
    db.commit()
    db.close()
    return data

def get_session_user_rowid():
    raw_user_id = session.get("u_rowid")
    if isinstance(raw_user_id, (list, tuple)):
        return raw_user_id[0] if raw_user_id else None
    return raw_user_id

def get_current_user_saved_value():
    user_rowid = get_session_user_rowid()
    if user_rowid is None:
        return None
    saved_rows = fetch('user_base', "ROWID=?", 'saved', (user_rowid,))
    if not saved_rows:
        session.pop("u_rowid", None)
        return None
    return saved_rows[0][0]

def create_user(username, password):
    db = get_db_connection()
    c = db.cursor()
    c.execute("SELECT username FROM user_base")
    list = [username[0] for username in c.fetchall()]
    if not username in list:
        # creates user in table
        c.execute(
            "INSERT INTO user_base(username, password, path, saved, creation_date, bio) VALUES (?, ?, ?, ?, ?, ?)",
            (username, password, "", " ", date.today().isoformat(), "")
        )

        # set path
        c.execute("SELECT rowid FROM user_base WHERE username=?", (username,))
        c.execute(f"UPDATE user_base SET path = '/profile/{c.fetchall()[0][0]}' WHERE username=?", (username,))
        db.commit()
        db.close()
        return True
    db.commit()
    db.close()
    return False


if __name__ == "__main__":
  app.debug = True
  app.run()
