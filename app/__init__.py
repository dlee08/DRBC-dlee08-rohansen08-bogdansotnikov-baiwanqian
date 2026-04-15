from pathlib import Path

import sqlite3
import requests
import pandas as pd
from flask import Flask, jsonify, render_template, request, session, redirect
import json
import urllib.request as urllib

app = Flask(__name__)
DATA_PATH = Path(__file__).resolve().parent / "data" / "IKEA_product_catalog.csv"
MAX_PRODUCT_TYPES = 30

#DB
DB_FILE = "data.db"

db = sqlite3.connect(DB_FILE) #open if file exists, otherwise create
c = db.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS user_base(username TEXT, password TEXT);""")
db.commit()
db.close()

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


@app.route("/", methods=["GET", "POST"])
def homepage():
  if not 'u_rowid' in session:
  	return redirect("/login")
  return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
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

def get_url(curr):
  return f'https://v6.exchangerate-api.com/v6/c608df1404c6c6b0bf2cd5bb/latest/{curr}'

@app.route("/")
def home_page():
  #return "the big leagues are calling for us twin"
  return redirect("https://palantir.com")

@app.route("/api_testing")
def api_testing():
  with urllib.urlopen(get_url("AED")) as response:
    json_data = response.read()

  apod_data = json.loads(json_data)

  json_string = json.dumps(apod_data, indent=2)
  print(json_string)
  return json_string

if __name__ == "__main__":
  app.debug = True
  app.run()
