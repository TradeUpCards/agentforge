"""Generator for the synthetic lab PDF fixture.

Creates agent/tests/fixtures/synthetic_lab_999100.pdf — a minimal but
realistic lab report for synthetic patient 999100 (W2 sentinel range).

Requirements:
- patient_id 999100 (in [999100, 999199] — W2 sentinel range per §8.2)
- No real-person PHI: all names, dates, values are fabricated
- Contains multiple distinct text blocks so smoke tests can verify
  Docling produces non-zero, distinct bboxes per block
- At least 2 pages to test page-number tracking

Run from repo root:
    python -m agent.tests.fixtures._generate_lab_pdf

Output: agent/tests/fixtures/synthetic_lab_999100.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

_OUTPUT = Path(__file__).parent / "synthetic_lab_999100.pdf"


def _generate() -> None:
    """Generate the synthetic lab PDF using reportlab.

    reportlab is a pure-Python PDF library with no native-library deps.
    It is added as a dev-only dependency (not in production requirements.txt).
    If unavailable, falls back to fpdf2.
    """
    try:
        _generate_with_reportlab()
    except ImportError:
        try:
            _generate_with_fpdf()
        except ImportError:
            print(
                "ERROR: Neither reportlab nor fpdf2 is installed.\n"
                "Install one of them to regenerate the fixture:\n"
                "  pip install reportlab\n"
                "  pip install fpdf2",
                file=sys.stderr,
            )
            sys.exit(1)


def _generate_with_reportlab() -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    width, height = letter  # 612 x 792 points

    c = canvas.Canvas(str(_OUTPUT), pagesize=letter)

    # ------- Page 1: Lab Report Header + Results -------
    # Header block
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 72, "REGIONAL HEALTH LABORATORY")
    c.setFont("Helvetica", 11)
    c.drawString(72, height - 95, "Laboratory Report")

    # Patient info block (synthetic — sentinel patient 999100)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, height - 130, "Patient Information")
    c.setFont("Helvetica", 10)
    c.drawString(72, height - 148, "Patient ID: 999100")
    c.drawString(72, height - 163, "Name: Synthetic, Test A")
    c.drawString(72, height - 178, "DOB: 1970-01-01")
    c.drawString(72, height - 193, "Sex: M")
    c.drawString(72, height - 208, "Collection Date: 2026-04-15")
    c.drawString(72, height - 223, "Ordering Provider: Dr. Fixture")

    # Results table header
    c.setFont("Helvetica-Bold", 10)
    c.drawString(72, height - 260, "Test Name")
    c.drawString(250, height - 260, "Result")
    c.drawString(330, height - 260, "Unit")
    c.drawString(400, height - 260, "Ref. Range")
    c.drawString(490, height - 260, "Flag")
    c.line(72, height - 265, 540, height - 265)

    # Lab results (synthetic values)
    results = [
        ("Hemoglobin A1c", "7.8", "%", "<5.7", "H"),
        ("Glucose, Fasting", "142", "mg/dL", "70-99", "H"),
        ("eGFR", "62", "mL/min/1.73m2", ">60", ""),
        ("Creatinine, Serum", "1.1", "mg/dL", "0.7-1.2", ""),
        ("Sodium", "138", "mEq/L", "136-145", ""),
        ("Potassium", "4.2", "mEq/L", "3.5-5.0", ""),
        ("LDL Cholesterol", "118", "mg/dL", "<100", "H"),
        ("HDL Cholesterol", "48", "mg/dL", ">40", ""),
        ("Triglycerides", "185", "mg/dL", "<150", "H"),
    ]

    c.setFont("Helvetica", 10)
    y = height - 280
    for test_name, value, unit, ref_range, flag in results:
        c.drawString(72, y, test_name)
        c.drawString(250, y, value)
        c.drawString(330, y, unit)
        c.drawString(400, y, ref_range)
        if flag:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(490, y, flag)
            c.setFont("Helvetica", 10)
        y -= 18

    # Footer block (page 1)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(
        72,
        72,
        "SYNTHETIC TEST DATA ONLY — NOT FOR CLINICAL USE — Patient ID 999100",
    )
    c.drawString(72, 58, "Page 1 of 2")

    c.showPage()

    # ------- Page 2: Additional Tests + Interpretation -------
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "Laboratory Report (continued)")

    # CBC block
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, height - 110, "Complete Blood Count (CBC)")
    c.setFont("Helvetica", 10)

    cbc_results = [
        ("WBC", "7.2", "x10^3/uL", "4.5-11.0", ""),
        ("RBC", "4.8", "x10^6/uL", "4.5-5.9", ""),
        ("Hemoglobin", "14.2", "g/dL", "13.5-17.5", ""),
        ("Hematocrit", "42.1", "%", "41.0-53.0", ""),
        ("Platelets", "245", "x10^3/uL", "150-400", ""),
    ]

    y = height - 130
    c.setFont("Helvetica-Bold", 10)
    c.drawString(72, y, "Test Name")
    c.drawString(250, y, "Result")
    c.drawString(330, y, "Unit")
    c.drawString(400, y, "Ref. Range")
    c.drawString(490, y, "Flag")
    c.line(72, y - 5, 540, y - 5)
    y -= 20
    c.setFont("Helvetica", 10)
    for test_name, value, unit, ref_range, flag in cbc_results:
        c.drawString(72, y, test_name)
        c.drawString(250, y, value)
        c.drawString(330, y, unit)
        c.drawString(400, y, ref_range)
        c.drawString(490, y, flag)
        y -= 18

    # Interpretation block
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y - 20, "Clinical Interpretation")
    c.setFont("Helvetica", 10)
    lines = [
        "HbA1c 7.8% indicates suboptimal glycemic control (target <7.0% for most adults).",
        "Fasting glucose 142 mg/dL is consistent with the elevated HbA1c.",
        "eGFR 62 mL/min/1.73m2 suggests mild chronic kidney disease (CKD stage 2).",
        "LDL 118 mg/dL exceeds the guideline target for high-risk patients (<70 mg/dL).",
        "CBC within normal limits.",
    ]
    y = y - 40
    for line in lines:
        c.drawString(72, y, line)
        y -= 16

    # Signing block
    c.setFont("Helvetica-Bold", 10)
    c.drawString(72, y - 20, "Verified by: Dr. Fixture, MD")
    c.drawString(72, y - 36, "Report Date: 2026-04-15")

    # Footer block (page 2)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(
        72,
        72,
        "SYNTHETIC TEST DATA ONLY — NOT FOR CLINICAL USE — Patient ID 999100",
    )
    c.drawString(72, 58, "Page 2 of 2")

    c.showPage()
    c.save()
    print(f"Generated: {_OUTPUT}")


def _generate_with_fpdf() -> None:
    """Fallback generator using fpdf2."""
    from fpdf import FPDF  # type: ignore[import-untyped]

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Page 1
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "REGIONAL HEALTH LABORATORY", ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, "Laboratory Report", ln=True)
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Patient Information", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, "Patient ID: 999100", ln=True)
    pdf.cell(0, 6, "Name: Synthetic, Test A", ln=True)
    pdf.cell(0, 6, "DOB: 1970-01-01", ln=True)
    pdf.cell(0, 6, "Collection Date: 2026-04-15", ln=True)
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(70, 8, "Test Name", border=1)
    pdf.cell(25, 8, "Result", border=1)
    pdf.cell(30, 8, "Unit", border=1)
    pdf.cell(35, 8, "Ref. Range", border=1)
    pdf.cell(15, 8, "Flag", border=1, ln=True)
    pdf.set_font("Helvetica", "", 10)

    results = [
        ("Hemoglobin A1c", "7.8", "%", "<5.7", "H"),
        ("Glucose, Fasting", "142", "mg/dL", "70-99", "H"),
        ("eGFR", "62", "mL/min/1.73m2", ">60", ""),
        ("Creatinine, Serum", "1.1", "mg/dL", "0.7-1.2", ""),
        ("LDL Cholesterol", "118", "mg/dL", "<100", "H"),
    ]
    for test_name, value, unit, ref_range, flag in results:
        pdf.cell(70, 7, test_name, border=1)
        pdf.cell(25, 7, value, border=1)
        pdf.cell(30, 7, unit, border=1)
        pdf.cell(35, 7, ref_range, border=1)
        pdf.cell(15, 7, flag, border=1, ln=True)

    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(
        0,
        6,
        "SYNTHETIC TEST DATA ONLY — NOT FOR CLINICAL USE — Patient ID 999100",
        ln=True,
    )

    # Page 2
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Laboratory Report (continued)", ln=True)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Complete Blood Count (CBC)", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, "WBC: 7.2 x10^3/uL (4.5-11.0)", ln=True)
    pdf.cell(0, 6, "Hemoglobin: 14.2 g/dL (13.5-17.5)", ln=True)
    pdf.cell(0, 6, "Platelets: 245 x10^3/uL (150-400)", ln=True)
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Clinical Interpretation", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(
        0,
        6,
        "HbA1c 7.8% indicates suboptimal glycemic control. "
        "eGFR 62 suggests mild CKD. LDL 118 exceeds target.",
    )
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(
        0,
        6,
        "SYNTHETIC TEST DATA ONLY — NOT FOR CLINICAL USE — Patient ID 999100",
        ln=True,
    )

    pdf.output(str(_OUTPUT))
    print(f"Generated: {_OUTPUT}")


if __name__ == "__main__":
    _generate()
