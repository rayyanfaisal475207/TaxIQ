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

# filter for 2020 onwards
years = ['2020', '2021', '2022', '2023', '2024', '2025', '2026']
recent_docs = [f for f in filenames if any(y in f for y in years)]

print(f"Total unique documents: {len(filenames)}")
print(f"Total documents from 2020 onwards: {len(recent_docs)}")
with open("tally.txt", "w") as f:
    for doc in sorted(recent_docs):
        f.write(f"{doc}\n")
