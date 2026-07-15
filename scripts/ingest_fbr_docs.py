"""
ingest_fbr_docs.py — Priority FBR Document Downloader & Ingester
=================================================================
Downloads and ingests the priority Pakistan tax documents that fill
the 11 dataset gaps identified by the Phase 8 eval suite.

Eval baseline: 5/20 pass (25%). Expected after ingestion: ~15/20 (75%).

Priority order from TaxIQ Master Plan §4:
  P1 — ITO 2001, Finance Act 2024-25, Sales Tax Act 1990 (full)
  P2 — WHT Rate Card, FBR SROs
  P3 — Provincial tax acts (PRA, SRB, KPKRA)

Usage:
  python ingest_fbr_docs.py               # Download + ingest all
  python ingest_fbr_docs.py --check-only  # Only check what's missing
  python ingest_fbr_docs.py --local <dir> # Ingest already-downloaded PDFs
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Priority document list ────────────────────────────────────────────────────
# Each entry: (filename, url, eval_questions_fixed, notes)
PRIORITY_DOCUMENTS = [
    {
        "filename": "ITO_2001_Full.pdf",
        "url": "https://download1.fbr.gov.pk/Docs/201421410341177261IncomeTaxOrdinance2001UpdateduptoJune30,2021.pdf",
        "fixes_questions": [3, 4, 9, 11, 15, 17, 19, 20],
        "sections": "Section 12, 29, 74, 113, 127, 148, 152, 153, 155, 182, 236M, Third Schedule, Section 2(41)",
        "priority": 1,
    },
    {
        "filename": "Finance_Act_2023_24.pdf",
        "url": "https://download1.fbr.gov.pk/Docs/202481414461827681FinanceAct2023-24.pdf",
        "fixes_questions": [18],
        "sections": "Super tax, Finance Act amendments",
        "priority": 1,
    },
    {
        "filename": "Federal_Excise_Act_2005.pdf",
        "url": "https://download1.fbr.gov.pk/Docs/20221117171215994FederalExciseAct2005-UpdatedJune2022.pdf",
        "fixes_questions": [16],
        "sections": "FED rates on goods including cement",
        "priority": 1,
    },
    {
        "filename": "Sales_Tax_Act_1990_Full.pdf",
        "url": "https://download1.fbr.gov.pk/Docs/2022111813122590SalesTaxAct1990-UpdatedJune2022.pdf",
        "fixes_questions": [2, 8],
        "sections": "Section 14, 26, 8 (full text)",
        "priority": 1,
    },
    {
        "filename": "WHT_Rate_Card_2024_25_Full.pdf",
        "url": "https://download1.fbr.gov.pk/Docs/202451492952987481WHTRateCard2024-25.pdf",
        "fixes_questions": [],
        "sections": "Full WHT rate card with all sections",
        "priority": 2,
    },
]

MANUAL_SOURCES = """
MANUAL DOWNLOAD SOURCES
========================
If the automated download URLs are outdated (FBR restructures their site occasionally),
download the PDFs manually from:

1. FBR Official Downloads:
   https://www.fbr.gov.pk/acts-and-ordinances/111218

2. ITO 2001 (most up-to-date):
   Search "Income Tax Ordinance 2001" on fbr.gov.pk
   -> Usually at: fbr.gov.pk > Tax Laws > Income Tax Laws > Income Tax Ordinance 2001

3. Finance Act 2023-24:
   fbr.gov.pk > Finance Acts > Finance Act 2023

4. Federal Excise Act 2005:
   fbr.gov.pk > Tax Laws > Federal Excise Laws

Save downloaded PDFs to: data/documents/
Then run: python ingest_fbr_docs.py --local data/documents/
"""


def check_existing_documents(docs_dir: Path) -> dict:
    """Check which priority documents are already downloaded."""
    status = {}
    for doc in PRIORITY_DOCUMENTS:
        path = docs_dir / doc["filename"]
        status[doc["filename"]] = path.exists()
    return status


def print_status(docs_dir: Path) -> None:
    """Print which documents are present and which are missing."""
    status = check_existing_documents(docs_dir)

    print("\n=== FBR Document Status ===")
    print(f"Documents directory: {docs_dir.resolve()}\n")

    missing = []
    for doc in PRIORITY_DOCUMENTS:
        fname = doc["filename"]
        present = status[fname]
        icon = "OK" if present else "MISSING"
        q_str = f"Q{doc['fixes_questions']}" if doc["fixes_questions"] else "supplementary"
        print(f"  [{icon}] P{doc['priority']} {fname}")
        print(f"         Fixes: {q_str} | Sections: {doc['sections'][:60]}")
        if not present:
            missing.append(fname)

    print(f"\nMissing: {len(missing)}/{len(PRIORITY_DOCUMENTS)} documents")
    if missing:
        print("\nRun with --download to attempt automatic download from FBR.")
        print("Or download manually — see printed instructions above.")


async def download_document(url: str, dest: Path) -> bool:
    """Download a single document via HTTP. Returns True on success."""
    try:
        import httpx
    except ImportError:
        logger.error("httpx not installed. Run: pip install httpx")
        return False

    try:
        logger.info("Downloading %s ...", dest.name)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()

            dest.write_bytes(response.content)
            size_mb = len(response.content) / (1024 * 1024)
            logger.info("  Saved %s (%.1f MB)", dest.name, size_mb)
            return True

    except Exception as exc:
        logger.error("  Failed to download %s: %s", dest.name, exc)
        logger.error("  URL: %s", url)
        return False


async def ingest_document(filepath: Path) -> bool:
    """Run the existing ingestion pipeline on a single file."""
    try:
        # Import ingestion components
        from src.ingestion.pipeline import ingest_file
        logger.info("Ingesting %s ...", filepath.name)
        result = await ingest_file(filepath)
        logger.info("  Ingested %d chunks from %s", result.get("chunks", 0), filepath.name)
        return True
    except ImportError:
        # Fall back to running the ingest CLI
        logger.info("Ingesting via ingest.py: %s ...", filepath.name)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "ingest.py", str(filepath),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            logger.info("  Done: %s", stdout.decode()[:100])
            return True
        else:
            logger.error("  Failed: %s", stderr.decode()[:200])
            return False
    except Exception as exc:
        logger.error("  Ingestion failed for %s: %s", filepath.name, exc)
        return False


async def ingest_local_directory(local_dir: Path) -> None:
    """Ingest all PDF/MD files in a directory."""
    supported = list(local_dir.glob("*.pdf")) + list(local_dir.glob("*.md"))
    if not supported:
        logger.warning("No PDF or MD files found in %s", local_dir)
        return

    logger.info("Found %d files to ingest in %s", len(supported), local_dir)
    success = 0
    for fp in sorted(supported):
        ok = await ingest_document(fp)
        if ok:
            success += 1

    logger.info("\nIngestion complete: %d/%d files processed", success, len(supported))


async def main(args: argparse.Namespace) -> None:
    docs_dir = Path("data/documents")
    docs_dir.mkdir(parents=True, exist_ok=True)

    if args.check_only:
        print_status(docs_dir)
        print(MANUAL_SOURCES)
        return

    if args.local:
        local_dir = Path(args.local)
        if not local_dir.exists():
            logger.error("Directory not found: %s", local_dir)
            sys.exit(1)
        await ingest_local_directory(local_dir)
        return

    if args.download:
        print_status(docs_dir)
        status = check_existing_documents(docs_dir)
        to_download = [d for d in PRIORITY_DOCUMENTS if not status[d["filename"]]]

        if not to_download:
            logger.info("All priority documents already downloaded.")
        else:
            logger.info("\nAttempting to download %d missing documents...", len(to_download))
            logger.info("Note: FBR URLs change periodically. If downloads fail, use --check-only for manual instructions.\n")

            for doc in to_download:
                dest = docs_dir / doc["filename"]
                ok = await download_document(doc["url"], dest)
                if not ok:
                    logger.warning("Skipping ingestion for %s (download failed)", doc["filename"])
                    continue
                await ingest_document(dest)

        # Also ingest any existing files not yet ingested
        logger.info("\nRe-ingesting all files in %s to ensure ChromaDB is current...", docs_dir)
        await ingest_local_directory(docs_dir)
        return

    # Default: show status + instructions
    print_status(docs_dir)
    print(MANUAL_SOURCES)
    print("\nOptions:")
    print("  python ingest_fbr_docs.py --check-only   # Show status only")
    print("  python ingest_fbr_docs.py --download     # Download from FBR + ingest")
    print("  python ingest_fbr_docs.py --local <dir>  # Ingest local PDFs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download and ingest priority FBR documents")
    parser.add_argument("--check-only", action="store_true", help="Only check status, don't download")
    parser.add_argument("--download", action="store_true", help="Download missing docs from FBR and ingest")
    parser.add_argument("--local", metavar="DIR", help="Ingest all PDFs from a local directory")
    args = parser.parse_args()

    asyncio.run(main(args))
