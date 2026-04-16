from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
DATA_PATH = Path(__file__).resolve().parent / "data" / "IKEA_product_catalog.csv"
KEY_PATH = Path(__file__).resolve().parent / "keys" / "key_exchangerate-api.txt"
MAX_PRODUCT_TYPES = 30
DEFAULT_TARGET_CURRENCY = "USD"


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


def build_demo_data(country, target_currency):
  catalog = load_catalog()
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


@app.route("/")
def hello_world():
  return "the big leagues are calling us twin"


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

if __name__ == "__main__":
  app.debug = True
  app.run()
