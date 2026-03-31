# import pandas as pd
# import sqlite
# data = pd.read_csv("IKEA_product_catalog.csv")

from flask import Flask, render_template
app = Flask(__name__)

@app.route("/")
def hello_world():
  return "the big leagues are calling us twin"

if __name__ == "__main__":
  app.debug = True
  app.run()
