import asyncio
import os
import sys
import json

# Add root directory to PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.pipeline.router import route_query

# Test Dataset
# Format: (query, expected_route, expected_format)
TEST_CASES = [
    # DIRECT
    ("Hello there!", "DIRECT", "chat"),
    ("How are you doing today?", "DIRECT", "chat"),
    ("What are your capabilities?", "DIRECT", "chat"),
    ("Thanks for your help.", "DIRECT", "chat"),
    ("How do I fix a flat tire on my Honda Civic?", "DIRECT", "chat"),
    ("What is the recipe for chocolate cake?", "DIRECT", "chat"),
    ("Generate a document on how to fix a flat tire.", "DIRECT", "file_docx"),
    
    # RAG
    ("Explain Section 148 of the Income Tax Ordinance.", "RAG", "chat"),
    ("What is the penalty for late filing of tax returns?", "RAG", "chat"),
    ("How is super tax calculated?", "RAG", "chat"),
    ("What was the sales tax rate back in 2023?", "RAG", "chat"),
    ("Generate a document about Section 148.", "RAG", "file_docx"),
    ("Can you generate a PDF explaining the definition of a resident person?", "RAG", "file_pdf"),
    
    # SQL
    ("What is the WHT rate on dividends for filers?", "SQL", "chat"),
    ("What is the sales tax rate on services?", "SQL", "chat"),
    ("Export the WHT rates for services as an excel file.", "SQL", "file_xlsx"),
    ("Can you give me the withholding tax rate for non-filers on prizes?", "SQL", "chat"),
    
    # WEB
    ("What did the FBR announce today?", "WEB", "chat"),
    ("What is the Sindh Sales Tax on software services?", "WEB", "chat"),
    ("Is there PRA tax on IT services in Punjab?", "WEB", "chat"),
    ("What is the latest news regarding the new federal budget?", "WEB", "chat"),
]

async def run_evaluation():
    print("=" * 60)
    print("Starting Router Evaluation Suite...")
    print("=" * 60)
    
    total = len(TEST_CASES)
    passed_route = 0
    passed_format = 0
    fully_passed = 0
    failures = []

    for i, (query, expected_route, expected_format) in enumerate(TEST_CASES, 1):
        print(f"\n[{i}/{total}] Query: '{query}'")
        try:
            # Route the query
            res = await route_query(query)
            actual_route = res.get("route")
            actual_format = res.get("output_format")
            
            # Check route accuracy
            route_match = actual_route == expected_route
            
            # Check format accuracy
            format_match = False
            if expected_format in ["file_docx", "file_pdf", "file_xlsx"]:
                if "pdf" in query.lower() and actual_format == "file_pdf":
                    format_match = True
                elif "excel" in query.lower() and actual_format == "file_xlsx":
                    format_match = True
                elif actual_format in ["file_docx", "file_pdf"]:
                    format_match = True
            else:
                format_match = actual_format == expected_format
                
            if route_match:
                passed_route += 1
            if format_match:
                passed_format += 1
                
            if route_match and format_match:
                fully_passed += 1
                print(f"  [PASS] Route: {actual_route} | Format: {actual_format}")
            else:
                print(f"  [FAIL] Expected: ({expected_route}, {expected_format}) -> Got: ({actual_route}, {actual_format})")
                print(f"         Reason given: {res.get('reason')}")
                failures.append({
                    "query": query,
                    "expected_route": expected_route,
                    "expected_format": expected_format,
                    "actual_route": actual_route,
                    "actual_format": actual_format,
                    "reason": res.get("reason")
                })
        except Exception as e:
            print(f"  [ERROR] {e}")
            failures.append({
                "query": query,
                "expected_route": expected_route,
                "expected_format": expected_format,
                "error": str(e)
            })

    # Summary
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    
    route_acc = (passed_route / total) * 100
    format_acc = (passed_format / total) * 100
    overall_acc = (fully_passed / total) * 100
    
    print(f"Total Test Cases : {total}")
    print(f"Route Accuracy   : {route_acc:.1f}% ({passed_route}/{total})")
    print(f"Format Accuracy  : {format_acc:.1f}% ({passed_format}/{total})")
    print(f"Overall Accuracy : {overall_acc:.1f}% ({fully_passed}/{total})")
    
    if failures:
        print("\nFAILED CASES:")
        for fail in failures:
            print(f"\n- Query: '{fail['query']}'")
            if "error" in fail:
                print(f"  Error: {fail['error']}")
            else:
                print(f"  Expected : Route={fail['expected_route']}, Format={fail['expected_format']}")
                print(f"  Actual   : Route={fail['actual_route']}, Format={fail['actual_format']}")
                print(f"  Reason   : {fail['reason']}")
    else:
        print("\nALL TESTS PASSED SUCCESSFULLY! 🚀")
        
    # Write to a JSON for artifact reporting
    with open("eval_results_router.json", "w") as f:
        json.dump({
            "total": total,
            "route_acc": route_acc,
            "format_acc": format_acc,
            "overall_acc": overall_acc,
            "failures": failures
        }, f, indent=2)

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    asyncio.run(run_evaluation())
