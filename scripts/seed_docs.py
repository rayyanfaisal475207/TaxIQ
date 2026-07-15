import os
import shutil
from pathlib import Path

DOCS_DIR = Path("d:/Rapids AI/Rag-Chatbot/data/documents")

# 1. Clear old medical files
for file in DOCS_DIR.glob("*"):
    if file.is_file() and file.name != "README.txt":
        print(f"Removing old file: {file.name}")
        file.unlink()

# 2. Create seed Tax Data
ito_content = """# Income Tax Ordinance 2001 (Excerpts)

## Section 12. Salary
(1) Any salary received by an employee in a tax year, other than salary that is exempt from tax under this Ordinance, shall be chargeable to tax in that year under the head "Salary".

## Section 113. Minimum tax on the income of certain persons
(1) This section shall apply to a resident company, permanent establishment of a non-resident company, an individual (having turnover of three hundred million rupees or above) and an association of persons (having turnover of three hundred million rupees or above).
(2) The minimum tax payable shall be 1.25% of the person's turnover for the year.

## Section 150. Dividends
Every person paying a dividend shall deduct tax from the gross amount of the dividend paid. The rate of tax to be deducted under section 150 shall be 15% for active taxpayers (filers) and 30% for non-active taxpayers.

## Section 152. Payments to non-residents
(1) Every person paying an amount of royalty or fee for technical services to a non-resident person shall deduct tax from the gross amount paid at the rate specified in Division IV of Part I of the First Schedule.
(2) The rate of tax to be deducted under section 152(1) for fee for technical services shall be 15% of the gross amount of the fee.

## Section 153. Payments for goods, services and contracts
(1) Every prescribed person making a payment in full or part including advance payment to a resident person for:
(a) the sale of goods; shall deduct tax at 4% for companies and 5.5% for others.
(b) the rendering of or providing of services; shall deduct tax at 8% for companies and 10% for others (filers).
(c) on the execution of a contract; shall deduct tax at 6.5% for companies and 7% for others.
"""

sales_tax_content = """# Sales Tax Act 1990 (Excerpts)

## Section 8. Tax credit not allowed
(1) Notwithstanding anything contained in this Act, a registered person shall not be entitled to reclaim or deduct input tax paid on:
(a) the goods or services used or to be used for any purpose other than for taxable supplies made or to be made by him;
(b) any other goods or services which the Federal Government may, by a notification in the official Gazette, specify.

## Section 14. Registration
(1) Every person engaged in making taxable supplies in Pakistan, including zero-rated supplies, in the course or furtherance of any taxable activity carried on by him, if his annual turnover from taxable supplies exceeds ten million rupees, shall be required to be registered under this Act.

## Section 26. Return
(1) Every registered person shall furnish not later than the 15th day of every month a true and correct return in the prescribed form to a designated bank or any other office specified by the Board.
"""

wht_rates_content = """# Withholding Tax Rate Card 2024-25

| Nature of Payment | Section | Rate for Active Taxpayer (Filer) | Rate for Non-Active Taxpayer (Non-Filer) |
|---|---|---|---|
| Dividend | 150 | 15% | 30% |
| Return on Investment / Profit on Debt | 151 | 15% | 30% |
| Payments to Non-Residents (Technical Services) | 152(1) | 15% | 15% |
| Goods (Companies) | 153(1)(a) | 4% | 8% |
| Goods (Non-Companies) | 153(1)(a) | 5.5% | 11% |
| Services (Companies) | 153(1)(b) | 8% | 16% |
| Services (Non-Companies) | 153(1)(b) | 10% | 20% |
| Rent of Property (Companies) | 155 | 15% | 15% |
| Prize on Prize Bonds | 156 | 15% | 30% |
"""

with open(DOCS_DIR / "ITO_2001_Excerpts.md", "w") as f:
    f.write(ito_content)

with open(DOCS_DIR / "Sales_Tax_Act_1990_Excerpts.md", "w") as f:
    f.write(sales_tax_content)

with open(DOCS_DIR / "WHT_Rate_Card_2024-25.md", "w") as f:
    f.write(wht_rates_content)

print("Created seed tax documents.")
