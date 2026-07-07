import time, csv, json, os, sys
from pathlib import Path
from maps_website_collector import scrape_websites

csv_path = Path(__file__).resolve().parent.parent / 'logs' / 'maps_1.csv'
rows = []
with open(csv_path, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(r)

print('Number of rows:', len(rows))
for workers in [1, 3, 5, 10]:
    start = time.time()
    results = scrape_websites(rows, max_workers=workers)
    elapsed = time.time() - start
    print(f'workers={workers} scrape_seconds={elapsed:.2f}')
