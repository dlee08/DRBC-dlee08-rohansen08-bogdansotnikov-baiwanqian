# InfiniteIKEA by Da Real Big Cool
## Roster: 
David Lee - Project Manager (PM) </br>
Rohan Sen - F Student (innovator) </br>
Bogdan Sotnikov - Fullstack Devo 2 </br>
Christine Chen - Fullstack Devo 3 </br>

## Description
A Flask application backed by SQLite3 which allows users to dynamically compare IKEA items on an international scale. Points of comparison include statistics such as price & currency, or average review rating & # of reviews. The site will use D3.js for data visualization and Bootstrap for styling; React may be used for interactive components in data visualization.

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
