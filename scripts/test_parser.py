import csv, re

def clean_num(s):
    if not s or s.strip() in ('', '--', 'N/A', '0%'):
        return 0.0
    return float(re.sub(r'[^0-9.\-]', '', s) or '0')

def roas_x(roas_pct):
    return round(roas_pct / 100, 2)

path = r'C:\Users\retai\Downloads\Chewy Ads - Campaigns.csv'
rows = []
with open(path, newline='', encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        rows.append({
            'name':     row.get('Campaign name', '').strip(),
            'status':   row.get('Status', '').strip(),
            'spend':    clean_num(row.get('Spend', '0')),
            'roas_raw': clean_num(row.get('Direct ROAS', '0')),
            'sales':    clean_num(row.get('Direct sales', '0')),
            'orders':   int(clean_num(row.get('Total orders', '0'))),
            'position': clean_num(row.get('Avg position', '0')),
        })

print(f'Parsed {len(rows)} campaigns:')
for r in rows:
    print(f'  {r["name"][:45]:<45} | {r["status"]:<8} | spend=${r["spend"]:.2f} | ROAS={roas_x(r["roas_raw"])}x')

active = [r for r in rows if r['status'].lower() == 'active']
total_spend = sum(r['spend'] for r in active)
total_sales = sum(r['sales'] for r in active)
roas = round(total_sales / total_spend, 2) if total_spend else 0
print(f'\nActive totals: spend=${total_spend:.2f}  sales=${total_sales:.2f}  ROAS={roas}x')
