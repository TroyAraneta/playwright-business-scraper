import time, csv, json, sys
from pathlib import Path
from app import scrape_company

csv_path = Path(__file__).resolve().parent.parent / 'logs' / 'maps_only.csv'
rows = []
with open(csv_path, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(r)

# --- Scrape stage timing ---
scrape_start = time.time()
results = []
for row in rows:
    try:
        res = scrape_company(row['website'])
        out = {
            **row,
            'company_name': res.get('company_name') or '',
            'email': ', '.join(res.get('email') or []),
            'phone': ', '.join(res.get('phone') or []),
            'location': res.get('location') or '',
            'services': ', '.join(res.get('services') or []),
            'error': ''
        }
    except Exception as e:
        out = {**row, 'company_name': '', 'email': '', 'phone': '', 'location': '', 'services': '', 'error': str(e)}
    results.append(out)
scrape_sec = time.time() - scrape_start
print('scrape_seconds', scrape_sec)

# --- Export stage timing ---
export_start = time.time()
out_path = Path(__file__).resolve().parent.parent / 'logs' / 'scrape_output.csv'
with open(out_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=results[0].keys())
    writer.writeheader()
    writer.writerows(results)
export_sec = time.time() - export_start
print('export_seconds', export_sec)
