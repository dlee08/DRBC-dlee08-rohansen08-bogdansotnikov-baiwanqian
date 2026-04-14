from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
DATA_PATH = Path(__file__).resolve().parent / "data" / "IKEA_product_catalog.csv"
MAX_PRODUCT_TYPES = 30


def load_catalog():
  return pd.read_csv(DATA_PATH, usecols=["country", "product_type", "price"])


def get_countries():
  catalog = load_catalog()
  return sorted(catalog["country"].dropna().astype(str).unique().tolist())


def build_demo_data(country):
  catalog = load_catalog()
  filtered = catalog[catalog["country"] == country].dropna(subset=["product_type", "price"])
  top_product_types = (
    filtered["product_type"]
    .value_counts()
    .head(MAX_PRODUCT_TYPES)
    .index
  )
  filtered = filtered[filtered["product_type"].isin(top_product_types)]
  grouped = (
    filtered.groupby("product_type", as_index=False)["price"]
    .mean()
    .sort_values("price", ascending=False)
  )
  grouped["price"] = grouped["price"].round(2)
  return grouped.to_dict(orient="records")


@app.route("/")
def hello_world():
  return render_template("index.html")


@app.route("/demo_graph")
def demo_graph():
  countries = get_countries()
  default_country = "USA" if "USA" in countries else countries[0]
  return render_template(
    "demo_graph.html",
    countries=countries,
    default_country=default_country
  )


@app.route("/api/demo_graph_data")
def demo_graph_data():
  countries = get_countries()
  default_country = "USA" if "USA" in countries else countries[0]
  country = request.args.get("country", default_country)
  if country not in countries:
    return jsonify({"error": "Unknown country"}), 400

  return jsonify({
    "country": country,
    "data": build_demo_data(country)
  })


if __name__ == "__main__":
  app.debug = True
  app.run()
