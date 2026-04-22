# IKEAlyzer by Da Real Big Cool
## Roster: 
David Lee - Project Manager (PM) </br>
Rohan Sen - F Student (innovator) </br>
Bogdan Sotnikov - Fullstack Devo 2 </br>
Christine Chen - Fullstack Devo 3 </br>

## Description
A Flask application backed by SQLite3 which allows users to dynamically compare IKEA items on an international scale. Points of comparison include statistics such as price & currency, or average review rating & # of reviews. The site will use D3.js for data visualization and Bootstrap for styling.

#### Visit our live site at [http://104.236.89.211/](http://104.236.89.211/)

### FEATURE SPOTLIGHT
* Play around with the graphs and adjustable features of the D3 graphs for each item!
* Try counting how many total items we have by pressing the next page infinite times! (Joke... after parsing and removing incomplete entries, we believe there are around 4000 product groups! You can try checking every item, though, to see if we missed any incomplete entries and they bypassed our testing...)

### KNOWN BUGS/ISSUES
* Some images on the server-side host will not load because IKEA sometimes presumably flags the VM trying to scrape the IKEA product image (since every image has a unique path for the product group) as a bot (which is kind of true?) and so no image URL is returned.
* Everything else is in working order, maybe be slow the first time around when you serve on localhost for the first time and populate the db.

## Install guide
1) Clone the repo into a local directory:
```
git clone git@github.com:dlee08/DRBC-dlee08-rohansen08-bogdansotnikov-baiwanqian.git DRBC
```
2) Enter the app directory:
```
cd DRBC
```
3) Open a virtual environment:
```
python3 -m venv venv
```
4) Activate virtual env for Linux, Windows, or Mac:

i. Linux
```
. venv/bin/activate
```
ii. Windows
```
venv\Scripts\activate
```
iii. Mac
```
source venv/bin/activate
```
5) Install necessary modules:
```
pip install -r requirements.txt
```  
6) After running the launch codes and utilizing the app, exit the virtual environment:
```
deactivate
```

## Launch codes
1) Run the app through Flask:
```
python app/__init__.py

