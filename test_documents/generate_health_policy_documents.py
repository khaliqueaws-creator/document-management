from generate_test_documents import (
    ensure_dirs,
    create_docx,
    create_xlsx,
    create_pdf,
    create_ocr_image,
    WORD_DIR,
    EXCEL_DIR,
    PDF_DIR,
    OCR_DIR,
)


RAG_HEALTH_DIR = WORD_DIR.parent / "rag_health_policy"


HEALTH_POLICY_DOCUMENTS = [
    (
        "company_health_policy",
        "HR",
        "Policy",
        "Company Health Policy",
        (
            "The company health policy provides eligible full-time employees "
            "and their dependents access to medical, dental, and vision "
            "coverage. Coverage begins on the first day of the month after "
            "thirty days of employment. The company pays seventy-five percent "
            "of the employee-only medical premium and fifty percent of eligible "
            "dependent medical premiums. Employees may choose either the "
            "Standard PPO plan or the High Deductible Health Plan with a Health "
            "Savings Account. Preventive care, annual physicals, vaccinations, "
            "telehealth visits, prescription drug coverage, maternity care, "
            "mental health counseling, and emergency services are included "
            "under the company health policy. Employees enroll during new hire "
            "orientation, annual open enrollment, or within thirty-one days of "
            "a qualifying life event."
        ),
    ),
    (
        "health_benefits_enrollment_guide",
        "HR",
        "Guide",
        "Health Benefits Enrollment Guide",
        (
            "Employees should use the health benefits enrollment guide to "
            "select medical, dental, and vision coverage under the company "
            "health policy. New employees must submit elections within "
            "thirty-one days of becoming eligible. Qualifying life events "
            "include marriage, birth or adoption of a child, divorce, loss of "
            "other coverage, or a spouse's employment change. Required "
            "enrollment information includes dependent names, birth dates, "
            "relationship details, Social Security numbers when applicable, "
            "and proof of dependent eligibility. Employees who miss the "
            "deadline must wait until the next open enrollment period unless "
            "they experience a qualifying life event."
        ),
    ),
    (
        "medical_plan_comparison",
        "HR",
        "Comparison",
        "Medical Plan Comparison",
        (
            "The company health policy offers two medical plan options. The "
            "Standard PPO has higher payroll deductions but lower deductibles "
            "and predictable copays for office visits. The High Deductible "
            "Health Plan has lower payroll deductions, higher deductibles, and "
            "company contributions to a Health Savings Account. Both plans "
            "cover preventive care at no cost when employees use in-network "
            "providers. Both plans include telehealth, prescription drugs, "
            "urgent care, emergency room services, inpatient hospital care, "
            "maternity services, and behavioral health counseling."
        ),
    ),
    (
        "wellness_and_preventive_care_policy",
        "HR",
        "Policy",
        "Wellness and Preventive Care Policy",
        (
            "The wellness and preventive care policy supports the company "
            "health policy by encouraging annual physical exams, recommended "
            "vaccinations, biometric screenings, cancer screenings, tobacco "
            "cessation, nutrition counseling, and mental health support. "
            "Eligible employees may receive a wellness incentive after "
            "completing the annual health assessment and one approved wellness "
            "activity. Participation is voluntary and individual medical "
            "information is confidential. Managers may not request employee "
            "diagnosis details or use health information in employment "
            "decisions."
        ),
    ),
    (
        "health_policy_faq",
        "HR",
        "FAQ",
        "Company Health Policy FAQ",
        (
            "Question: What is the company health policy? Answer: The company "
            "health policy is the HR benefits policy that defines eligibility, "
            "company premium contributions, available medical plan options, "
            "covered health services, enrollment deadlines, dependent coverage, "
            "and qualifying life event rules for employee health benefits. "
            "Question: Who is eligible? Answer: Regular full-time employees "
            "scheduled to work at least thirty hours per week are eligible. "
            "Question: When does coverage start? Answer: Coverage starts on "
            "the first day of the month after thirty days of employment."
        ),
    ),
]


HEALTH_OCR_DOCUMENTS = [
    (
        "ocr_health_policy_notice.png",
        "Company Health Policy Notice",
        (
            "The company health policy covers medical, dental, vision, "
            "preventive care, telehealth, prescriptions, and mental health "
            "counseling for eligible full-time employees."
        ),
    ),
    (
        "ocr_open_enrollment_reminder.jpg",
        "Open Enrollment Reminder",
        (
            "Open enrollment for company health policy benefits runs from "
            "November 1 through November 15. Employees must confirm medical "
            "plan elections and dependent coverage."
        ),
    ),
]


def main():
    ensure_dirs()
    RAG_HEALTH_DIR.mkdir(parents=True, exist_ok=True)

    for index, item in enumerate(HEALTH_POLICY_DOCUMENTS):
        slug, department, document_type, title, content = item
        if index < 2:
            create_docx(slug, department, document_type, title, content)
            source_path = WORD_DIR / f"{slug}.docx"
        elif index < 4:
            create_xlsx(slug, department, document_type, title, content)
            source_path = EXCEL_DIR / f"{slug}.xlsx"
        else:
            create_pdf(slug, department, document_type, title, content)
            source_path = PDF_DIR / f"{slug}.pdf"
        (RAG_HEALTH_DIR / source_path.name).write_bytes(source_path.read_bytes())

    for index, item in enumerate(HEALTH_OCR_DOCUMENTS):
        filename, title, body = item
        create_ocr_image(filename, title, body, rotation=[0, -1][index])
        source_path = OCR_DIR / filename
        (RAG_HEALTH_DIR / source_path.name).write_bytes(source_path.read_bytes())

    print("Generated health policy RAG test documents.")
    print(f"Health RAG bundle: {len(list(RAG_HEALTH_DIR.iterdir()))}")
    print(f"Word total: {len(list(WORD_DIR.glob('*.docx')))}")
    print(f"Excel total: {len(list(EXCEL_DIR.glob('*.xlsx')))}")
    print(f"PDF total: {len(list(PDF_DIR.glob('*.pdf')))}")
    print(f"OCR image total: {len(list(OCR_DIR.iterdir()))}")


if __name__ == "__main__":
    main()
