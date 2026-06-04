# Quick one-shot run: process any Chewy CSVs already in Downloads, then exit.
# Use this to manually trigger an update any time, without the background watcher.
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from chewy_watcher import DOWNLOADS_DIR, is_chewy_report, process_file

found = 0
for csv_file in DOWNLOADS_DIR.glob("*.csv"):
    if is_chewy_report(csv_file):
        print(f"Processing: {csv_file.name}")
        process_file(csv_file)
        found += 1

if found == 0:
    print("No Chewy CSV reports found in Downloads. Save a report there and run again.")
else:
    print(f"\nDone — processed {found} file(s). Dashboard updated and pushed to GitHub.")
