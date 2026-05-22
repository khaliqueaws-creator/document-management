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


MORE_DOCUMENTS = [
    ("employee_promotion_request", "HR", "Request", "Employee Promotion Request", "Promotion justification, role change summary, compensation review, manager recommendation, performance history, and approval routing."),
    ("payroll_correction_form", "HR", "Form", "Payroll Correction Form", "Correction request for missed hours, incorrect deduction, pay period reference, employee acknowledgement, and payroll approval."),
    ("workplace_safety_notice", "HR", "Notice", "Workplace Safety Notice", "Safety reminder for emergency exits, incident reporting, ergonomic review, visitor procedures, and facility contact information."),
    ("software_license_renewal", "IT", "Renewal", "Software License Renewal", "License count, renewal deadline, subscription owner, procurement approval, support terms, and usage justification."),
    ("cybersecurity_audit_findings", "IT", "Audit", "Cybersecurity Audit Findings", "Audit observations, identity access gaps, patching exceptions, remediation owners, risk rating, and target completion dates."),
    ("it_asset_disposal_form", "IT", "Form", "IT Asset Disposal Form", "Retired hardware inventory, data wipe confirmation, disposal vendor, asset tag list, and security approval."),
    ("disaster_recovery_exercise", "IT", "Report", "Disaster Recovery Exercise", "Recovery time objective, failover test results, application validation, incident commander notes, and improvement actions."),
    ("customer_refund_approval", "Finance", "Approval", "Customer Refund Approval", "Refund amount, customer account, invoice reference, reason code, approval workflow, and processing notes."),
    ("procurement_exception_request", "Finance", "Request", "Procurement Exception Request", "Single-source justification, budget impact, vendor selection reason, compliance review, and executive approval."),
    ("tax_document_checklist", "Finance", "Checklist", "Tax Document Checklist", "Required forms, filing deadline, supporting schedules, reviewer signoff, and missing documentation follow-up."),
    ("vendor_termination_memo", "Legal", "Memo", "Vendor Termination Memo", "Termination reason, contract clause reference, notice delivery, transition obligations, and final payment review."),
    ("legal_settlement_summary", "Legal", "Summary", "Legal Settlement Summary", "Settlement terms, confidentiality requirement, payment schedule, release language, and responsible parties."),
    ("records_hold_notice", "Legal", "Notice", "Records Hold Notice", "Legal hold scope, affected custodians, retention instructions, preservation deadline, and compliance acknowledgement."),
    ("office_safety_inspection", "Operations", "Inspection", "Office Safety Inspection", "Inspection checklist, fire extinguisher status, trip hazards, corrective action owner, and completion date."),
    ("fleet_maintenance_log", "Operations", "Log", "Fleet Maintenance Log", "Vehicle service date, mileage, maintenance performed, next inspection, driver notes, and repair vendor."),
    ("shipping_delay_report", "Operations", "Report", "Shipping Delay Report", "Delayed shipment tracking, carrier issue, customer impact, revised delivery date, and escalation notes."),
    ("training_feedback_report", "Operations", "Report", "Training Feedback Report", "Participant survey results, instructor rating, content clarity, improvement ideas, and follow-up training needs."),
    ("customer_escalation_playbook", "Support", "Playbook", "Customer Escalation Playbook", "Escalation severity, response timeline, account owner, executive notification, and closure criteria."),
    ("support_ticket_trend_analysis", "Support", "Analysis", "Support Ticket Trend Analysis", "Ticket volume, repeated issues, root cause categories, service level status, and recommended fixes."),
    ("product_launch_readiness", "Marketing", "Checklist", "Product Launch Readiness", "Launch milestones, messaging approval, website update, sales enablement, customer announcement, and campaign tracking."),
    ("brand_guideline_update", "Marketing", "Guideline", "Brand Guideline Update", "Logo usage, color palette, typography rules, approval process, and asset management requirements."),
]


MORE_OCR_DOCUMENTS = [
    ("ocr_payroll_correction.png", "Payroll Correction Form", "Correct missed overtime hours for the previous pay period. Manager approval and payroll review are required."),
    ("ocr_software_license.jpg", "Software License Renewal", "Renew subscription for analytics software before expiration. Procurement approval is pending."),
    ("ocr_safety_inspection.tiff", "Office Safety Inspection", "Inspection found blocked exit signage and one damaged power strip requiring immediate replacement."),
    ("ocr_customer_refund.jpeg", "Customer Refund Approval", "Refund approval for duplicate invoice payment. Finance review completed and customer notification required."),
]


def main():
    ensure_dirs()

    for index, item in enumerate(MORE_DOCUMENTS):
        slug, department, document_type, title, content = item
        if index < 8:
            create_docx(slug, department, document_type, title, content)
        elif index < 15:
            create_xlsx(slug, department, document_type, title, content)
        else:
            create_pdf(slug, department, document_type, title, content)

    for index, item in enumerate(MORE_OCR_DOCUMENTS):
        filename, title, body = item
        create_ocr_image(filename, title, body, rotation=[-1, 1, 0, 2][index])

    print("Generated 25 additional files.")
    print(f"Word total: {len(list(WORD_DIR.glob('*.docx')))}")
    print(f"Excel total: {len(list(EXCEL_DIR.glob('*.xlsx')))}")
    print(f"PDF total: {len(list(PDF_DIR.glob('*.pdf')))}")
    print(f"OCR image total: {len(list(OCR_DIR.iterdir()))}")


if __name__ == "__main__":
    main()
