import pandas as pd
import random
import pathlib
import sys
from coolattin.scripts.data_cleaning import clean_unified
BASE_DIR = pathlib.Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / 'coolattin' / 'static' / 'data'

tenancies_path = DATA_DIR / 'tenancies.csv'
emigrations_path = DATA_DIR / 'emigrations_records.csv'
evictions_path = DATA_DIR / 'evictions_records.csv'
fuzzy_matches_path = DATA_DIR / 'fuzzy_matches.csv'
unified_excel_path = DATA_DIR / 'Coolattin unified database.xlsx'

tenancies = pd.read_csv(tenancies_path)
emigrations = pd.read_csv(emigrations_path)
evictions = pd.read_csv(evictions_path)
fuzzy_matches = pd.read_csv(fuzzy_matches_path)

try:
    unified = pd.read_excel(unified_excel_path, engine='openpyxl')
    unified = clean_unified(unified)
except Exception as e:
    print('Warning: could not read unified Excel file:', e, file=sys.stderr)
    unified = None

random_ids = random.sample(list(fuzzy_matches['left_row_id'].unique()), 5)

report_lines = []
report_lines.append('# Consistency Check of Fuzzy Matches')
report_lines.append('')
report_lines.append('This table shows whether each randomly selected fuzzy‑matched ID appears in the tenancies, emigrations, evictions and unified database (if available).')
report_lines.append('')
header = ['ID', 'Tenancies', 'Emigrations', 'Evictions', 'Unified']
report_lines.append('| ' + ' | '.join(header) + ' |')
report_lines.append('|' + '---|' * len(header))

for rid in random_ids:
    def present(df, col='id'):
        return '✓' if not df[df[col] == rid].empty else '✗'
    t = present(tenancies)
    e = present(emigrations)
    v = present(evictions)
    u = present(unified) if unified is not None else 'N/A'
    report_lines.append(f'| {rid} | {t} | {e} | {v} | {u} |')

output_path = BASE_DIR / 'consistency_check.md'
with open(output_path, 'w') as f:
    f.write('\n'.join(report_lines))
print(f'Report written to {output_path}')
