from functools import lru_cache
from pathlib import Path
import hashlib
import re
import unicodedata

import sqlite3
import requests
import pandas as pd
from flask import Flask, jsonify, render_template, request, session, redirect
import json
import urllib.request as urllib

app = Flask(__name__)
DATA_PATH = Path(__file__).resolve().parent / "data" / "IKEA_product_catalog.csv"
KEY_PATH = Path(__file__).resolve().parent / "keys" / "key_exchangerate-api.txt"
DB_PATH = Path(__file__).resolve().parent / "data.db"
MAX_PRODUCT_TYPES = 30
DEFAULT_TARGET_CURRENCY = "USD"
ENGLISH_PRIORITY_COUNTRIES = ["USA", "UK", "Canada", "Australia", "New_Zealand", "Ireland", "Singapore", "India"]
CATALOG_SCHEMA_VERSION = 1
CATALOG_USECOLS = [
    "product_id",
    "product_name",
    "product_type",
    "main_category",
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

def make_group_id(product_name, anchor_key):
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
        "SELECT product_id, product_name, product_type, main_category, country, price, currency, "
        "product_rating, product_rating_count, url, display_category "
        "FROM catalog_items"
    )


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

def normalize_slug_part(value):
    ascii_value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower()
    ascii_value = re.sub(r"[^a-z0-9]+", "-", ascii_value)
    return ascii_value.strip("-")

def get_url_slug(url):
    if not isinstance(url, str) or "/p/" not in url:
        return ""
    slug = url.split("/p/", 1)[1].rsplit("/", 1)[0]
    slug = re.sub(r"-s\d+$", "", slug)
    return normalize_slug_part(slug)

def get_descriptor_slug(product_name, url):
    url_slug = get_url_slug(url)
    if not url_slug:
        return ""
    name_slug = normalize_slug_part(product_name)
    if url_slug == name_slug:
        return ""
    if url_slug.startswith(f"{name_slug}-"):
        descriptor_slug = url_slug[len(name_slug) + 1:]
    else:
        descriptor_slug = url_slug
    descriptor_slug = re.sub(r"-\d+$", "", descriptor_slug)
    return descriptor_slug

def id_similarity_score(left_id, right_id):
    left = str(left_id)
    right = str(right_id)
    prefix = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        prefix += 1

    suffix = 0
    for left_char, right_char in zip(left[::-1], right[::-1]):
        if left_char != right_char:
            break
        suffix += 1

    shared_chars = len(set(left) & set(right))
    return max(prefix, suffix) * 3 + shared_chars

def descriptor_to_label(descriptor_slug):
    if not descriptor_slug:
        return ""
    return descriptor_slug.replace("-", " ").title()

def build_product_groups(catalog):
    catalog["product_id"] = catalog["product_id"].astype(str)
    catalog["descriptor_slug"] = [
        get_descriptor_slug(product_name, url)
        for product_name, url in zip(catalog["product_name"], catalog["url"])
    ]

    group_lookup = {}
    id_assignments = []

    for product_name, product_rows in catalog.groupby("product_name", sort=False):
        english_rows = product_rows[product_rows["country"].isin(ENGLISH_PRIORITY_COUNTRIES)]
        anchor_rows = english_rows if not english_rows.empty else product_rows

        anchor_groups = {}
        for row in anchor_rows[["product_id", "descriptor_slug"]].drop_duplicates().itertuples(index=False):
            anchor_key = row.descriptor_slug or row.product_id
            anchor_groups.setdefault(anchor_key, {
                "product_name": product_name,
                "descriptor_slug": row.descriptor_slug,
                "anchor_ids": set(),
            })
            anchor_groups[anchor_key]["anchor_ids"].add(row.product_id)

        id_to_anchor = {}
        for product_id in product_rows["product_id"].drop_duplicates():
            matching_anchor = next(
                (anchor_key for anchor_key, anchor_group in anchor_groups.items() if product_id in anchor_group["anchor_ids"]),
                None
            )
            if matching_anchor is None:
                best_anchor_key = None
                best_score = -1
                for anchor_key, anchor_group in anchor_groups.items():
                    score = max(
                        id_similarity_score(product_id, anchor_id)
                        for anchor_id in anchor_group["anchor_ids"]
                    )
                    if score > best_score:
                        best_score = score
                        best_anchor_key = anchor_key
                matching_anchor = best_anchor_key
            id_to_anchor[product_id] = matching_anchor

        for anchor_key, anchor_group in anchor_groups.items():
            descriptor_slug = anchor_group["descriptor_slug"]
            group_id = make_group_id(product_name, anchor_key)
            descriptor_label = descriptor_to_label(descriptor_slug)
            if descriptor_label:
                label = f"{product_name} ({descriptor_label})"
            else:
                label = product_name

            group_lookup[group_id] = {
                "group_id": group_id,
                "product_name": product_name,
                "label": label,
                "descriptor_slug": descriptor_slug,
            }
        for product_id, anchor_key in id_to_anchor.items():
            id_assignments.append({
                "product_name": product_name,
                "product_id": product_id,
                "product_group_id": make_group_id(product_name, anchor_key),
            })

    assignment_frame = pd.DataFrame(id_assignments)
    catalog = catalog.merge(assignment_frame, on=["product_name", "product_id"], how="left")
    return catalog, group_lookup

def init_user_db():
    with get_db_connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS user_base(username TEXT, password TEXT, path TEXT, saved TEXT);")

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
            "main_category TEXT, "
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
            "descriptor_slug TEXT, "
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
            "INSERT INTO catalog_items(product_id, product_name, product_type, main_category, country, price, currency, "
            "product_rating, product_rating_count, url, display_category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["product_id"],
                    db_value(row["product_name"]),
                    db_value(row["product_type"]),
                    db_value(row["main_category"]),
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
            "INSERT INTO product_groups(group_id, product_name, label, descriptor_slug, countries_count) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    row["group_id"],
                    row["product_name"],
                    row["label"],
                    db_value(row["descriptor_slug"]),
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

def get_product_options():
    with get_db_connection() as db:
        rows = db.execute(
            "SELECT group_id, product_name, label, descriptor_slug, countries_count "
            "FROM product_groups ORDER BY label"
        ).fetchall()
    return [
        {
            "group_id": row[0],
            "product_name": row[1],
            "label": row[2],
            "descriptor_slug": row[3],
            "countries_count": row[4],
        }
        for row in rows
    ]

def get_product_label(product_group_id):
    with get_db_connection() as db:
        row = db.execute(
            "SELECT label FROM product_groups WHERE group_id = ?",
            (product_group_id,)
        ).fetchone()
    if row is None:
        return None
    return row[0]

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

def build_choropleth_data(product_type):
  catalog = get_catalog_df()
  filtered = catalog[catalog["product_type"] == product_type].dropna(subset=["country", "price"])
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


# ================= Catalog Helpers ===================

def get_categories():
    catalog = get_catalog_df()
    return sorted(catalog["display_category"].dropna().unique())

def get_catalog_items(page=1, limit=50, country=None, category=None, search=None):
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

    grouped = catalog.drop_duplicates(subset="product_id", keep="first")
    grouped = grouped.sort_values("product_name").reset_index(drop=True)
    total = len(grouped)
    start = (page-1) * limit
    grouped = grouped[start:(start + limit)]
    return grouped.to_dict(orient="records"), total
# ===================================================


@app.route("/", methods=["GET", "POST"])
def homepage():
  if not 'u_rowid' in session:
  	return redirect("/login")
  return render_template("index.html", products=get_product_options())

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
    supported_currencies = get_supported_currencies()
    chart_data = build_product_country_price_data(product_group_id, DEFAULT_TARGET_CURRENCY)
    if not chart_data:
        return redirect("/")

    product_label = get_product_label(product_group_id)
    if not product_label:
        return redirect("/")

    return render_template(
        "product_country_graph.html",
        product_group_id=product_group_id,
        product_label=product_label,
        chart_data=chart_data,
        currencies=supported_currencies,
        default_currency=DEFAULT_TARGET_CURRENCY
    )

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
      session["u_rowid"] = fetch("user_base", "username = ?", "rowid", (request.form['username'],))[0]
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
    return redirect(f"/profile/{session['u_rowid'][0]}")

@app.route('/profile/<u_rowid>', methods=["GET", "POST"]) # makes u_rowid a variable that is passed to the function
def profile(u_rowid):
    if not 'u_rowid' in session:
        return redirect("/login")
    u_data = fetch('user_base', "ROWID=?", 'username, saved', (u_rowid,))[0]
    return render_template("profile.html",
        username=u_data[0],
        saved = u_data[1].split(", "))


@app.route("/catalog")
def catalog():
    countries = get_countries()
    categories = get_categories()
    selected_country = request.args.get("country", "")
    selected_category = request.args.get("category", "")
    search = request.args.get("search", "")
    page = int(request.args.get("page", 1))
    limit = 50;

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

@app.route("/product/<product_id>")
def product_detail(product_id):
    catalog = get_catalog_df()
    product_rows = catalog[catalog["product_id"].astype(str) == product_id]
    product_rows = product_rows.sort_values("country")
    product_name = product_rows["product_name"].values[0]

    return render_template(
        "product.html",
        items=product_rows.to_dict(orient="records"),
        product_id=product_id,
        product_name=product_name
    )

@app.route("/save/<product_id>", methods=["GET"])
def cave(product_id):
    if 'u_rowid' in session:
        if product_id not in fetch('user_base',
            "ROWID=?", 'saved', (session['u_rowid'][0],))[0][0].split(", "):
            db = get_db_connection()
            c = db.cursor()
            c.execute("UPDATE user_base SET saved = ? WHERE ROWID=?",
                (fetch("user_base", f"ROWID={session['u_rowid'][0]}", "saved")[0][0] + ", " + product_id,
                    session['u_rowid'][0]))
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
    return render_template (
    "choropleth.html",
    product_types = product_types,
    default_product_type = default_product_type
)


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

def create_user(username, password):
    db = get_db_connection()
    c = db.cursor()
    c.execute("SELECT username FROM user_base")
    list = [username[0] for username in c.fetchall()]
    if not username in list:
        # creates user in table
        c.execute("INSERT INTO user_base VALUES (?, ?, ?, ?)",(username, password, "", " "))

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
