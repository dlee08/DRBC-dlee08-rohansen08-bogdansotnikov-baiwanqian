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

csv = pd.read_csv(DATA_PATH, usecols=["country", "product_type", "price", "currency"])

#DB
DB_FILE = "data.db"

db = sqlite3.connect(DB_FILE) #open if file exists, otherwise create
c = db.cursor()

c.execute("CREATE TABLE IF NOT EXISTS user_base(username TEXT, password TEXT);")
db.commit()
db.close()

def load_catalog():
  return pd.read_csv(DATA_PATH, usecols=["country", "product_type", "price", "currency"])


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
  catalog = load_catalog()
  return sorted(catalog["country"].dropna().astype(str).unique().tolist())


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

if __name__ == "__main__":
  app.debug = True
  app.run()
