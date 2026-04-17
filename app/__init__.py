from functools import lru_cache
from pathlib import Path

import sqlite3
import requests
import pandas as pd
from flask import Flask, jsonify, render_template, request, session, redirect
import json
import urllib.request as urllib

app = Flask(__name__)
DATA_PATH = Path(__file__).resolve().parent / "data" / "IKEA_product_catalog.csv"
KEY_PATH = Path(__file__).resolve().parent / "keys" / "key_exchangerate-api.txt"
MAX_PRODUCT_TYPES = 30
DEFAULT_TARGET_CURRENCY = "USD"
app.secret_key = "6767"

csv = pd.read_csv(
    DATA_PATH,
    usecols=[
    "product_id",
    "product_name",
    "product_type",
    "main_category",
    "country",
    "price",
    "currency",
    "product_rating",
    "product_rating_count",
    "url"
    ]
)

#DB
DB_FILE = "data.db"

db = sqlite3.connect(DB_FILE) #open if file exists, otherwise create
c = db.cursor()

c.execute("CREATE TABLE IF NOT EXISTS user_base(username TEXT, password TEXT, path TEXT, saved TEXT);")
db.commit()
db.close()

def load_catalog():
  return pd.read_csv(
      DATA_PATH,
      usecols=[
      "product_id",
      "product_name",
      "product_type",
      "main_category",
      "country",
      "price",
      "currency",
      "product_rating",
      "product_rating_count",
      "url"
      ]
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
  return sorted(csv["country"].dropna().astype(str).unique().tolist())

def get_categories():
    return sorted(csv["display_category"].dropna().unique())

def get_catalog_items(limit=24, country=None, category=None, search=None):
    catalog = csv
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
    grouped = grouped.sort_values("product_name").head(limit)
    return grouped.to_dict(orient="records")

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
csv["display_category"] = csv["main_category"].apply(display_category)

def convert_price(amount, source_currency, target_currency):
  if source_currency == target_currency:
    return amount
  rates = get_conversion_rates(source_currency)
  return amount * rates[target_currency]

def get_product_types():
    catalog = load_catalog()
    return sorted(catalog["product_type"].dropna().astype(str).unique().tolist())

def build_demo_data(country, target_currency):
  catalog = csv
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
  catalog = csv
  filtered = catalog[catalog["product_type"] == product_type].dropna(subset=["country", "price"])
  grouped = (
    filtered.groupby("country", as_index=False)["price"]
    .mean()
    .sort_values("price", ascending=False)
  )
  grouped["price"] = grouped["price"].round(2)
  return grouped.to_dict(orient="records")


@app.route("/", methods=["GET", "POST"])
def homepage():
  if not 'u_rowid' in session:
  	return redirect("/login")
  return render_template("index.html")

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
        username=u_data[0])


@app.route("/catalog")
def catalog():
    countries = get_countries()
    categories = get_categories()
    selected_country = request.args.get("country", "")
    selected_category = request.args.get("category", "")
    search = request.args.get("search", "")

    items = get_catalog_items(
        limit=24,
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
    )

@app.route("/product/<product_id>")
def product_detail(product_id):
    product_rows = csv[csv["product_id"].astype(str) == product_id]
    product_rows = product_rows.sort_values("country")
    product_name = product_rows["product_name"].values[0]

    return render_template(
        "product.html",
        items=product_rows.to_dict(orient="records"),
        product_id=product_id,
        product_name=product_name
    )

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
    db = sqlite3.connect(DB_FILE)
    c = db.cursor()
    query = f"SELECT {data} FROM {table} WHERE {criteria}"
    c.execute(query, params)
    data = c.fetchall()
    db.commit()
    db.close()
    return data

def create_user(username, password):
    db = sqlite3.connect(DB_FILE)
    c = db.cursor()
    c.execute("SELECT username FROM user_base")
    list = [username[0] for username in c.fetchall()]
    if not username in list:
        # creates user in table
        c.execute("INSERT INTO user_base VALUES (?, ?, ?, ?)",(username, password, "", ""))

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
