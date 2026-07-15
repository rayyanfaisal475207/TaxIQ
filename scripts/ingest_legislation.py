import asyncio
import os
import sys
from pathlib import Path

# Add root to pythonpath
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.ingestion.service import ingest_directory

async def main():
    print("Starting ingestion of data/raw/fbr/legislation...")
    dir_path = Path("data/raw/fbr/legislation")
    result = await ingest_directory(dir_path)
    print("Ingestion result:", result)

if __name__ == "__main__":
    asyncio.run(main())
