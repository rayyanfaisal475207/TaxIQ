import os
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

url=os.environ['SUPABASE_URL']
key=os.environ['SUPABASE_SERVICE_ROLE_KEY']
c=create_client(url, key)

filenames = set()
offset = 0
limit = 1000

while True:
    res = c.table('documents').select('filename').range(offset, offset+limit-1).execute()
    data = res.data
    if not data:
        break
    for r in data:
        filenames.add(r['filename'])
    offset += limit
    print(f"Fetched {offset} rows...", end="\r")

print(f"\nTotal unique filenames: {len(filenames)}")
print("Sample files:", list(filenames)[:5])
