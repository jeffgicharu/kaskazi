+++
id = "document-verification-procedure"
title = "Document verification procedure (staff)"
audience = "staff"
topics = ["loans", "staff", "security"]
version = "1.0"
effective = 2026-10-01
owner = "Lending Operations"
+++

# Document verification procedure (staff)

This procedure is for Lending Operations staff who review loan documents that the system has flagged: payslips, bank statements, identity documents and KRA PIN documents uploaded for a Kaskazi Personal Loan or a Kaskazi Salary Advance. It covers how to read the system's fields, confidence and evidence, how to work through each kind of failed check, how to spot tampering, how to handle documents that carry hidden instructions or odd text, how to correct fields, and how to record the outcome. Credit decisions themselves are covered by the Credit decision guidelines (staff).

Kaskazi Bank is invented. This procedure was written for a demonstration of a banking assistant and governs no real lending.

## When document verification by a person is needed

When a customer uploads a document, the system reads the fields the bank needs and runs automatic checks. A document comes to the Lending Operations review queue when any of these happens:

- a check fails: totals, dates, names, net pay arriving in the statement, or completeness
- one or more fields were read with low or medium confidence
- the document type, layout or quality is unusual
- the system detects possible signs of tampering
- the document contains text that is not part of a normal payslip, statement, ID or PIN document, including anything that reads like an instruction

A document that passes every check with high confidence on every field goes straight to the credit reviewer, who can still send it back for review. No credit decision may be made while any document on the application is in the review queue.

Before you open a case, check that you have no conflict of interest: you must not review documents for yourself, your family or household, a close friend, someone you have a financial relationship with, or a colleague in your reporting line. If you do, pass the case back to your team leader for reallocation.

## Fields, confidence and evidence on loan documents

For each document, the review screen shows every field the system extracted. Each field has three parts:

- **the value** the system read, for example net pay of KES 48,000
- **a confidence level**, high, medium or low, showing how sure the system is that it read the value correctly
- **the evidence**: the place on the page the value came from, highlighted, and the exact text the system read there

The fields extracted for each document type are:

| Document | Fields |
|---|---|
| Payslip | Employee name, employer name, pay period, pay date, basic pay, each allowance, gross pay, each deduction, total deductions, net pay |
| Bank statement | Account holder name, account number, statement start and end dates, page numbers, opening and closing balances, each salary credit (date, amount, payer) |
| National ID card or passport | Document type, full name, ID or passport number, date of birth, expiry date where shown |
| KRA PIN document | PIN, name |

The confidence bands are set in the system and are not published in this procedure.

**The document is the evidence, not the field.** A high-confidence field can still be wrong, and a low-confidence field is often right. Always check flagged fields against the highlighted part of the page, and look at the whole document, not just the highlights. If the evidence box points at the wrong part of the page, the field is not verified, whatever its value.

## Step-by-step document review

Work through every flagged document in this order.

1. **Read the flags.** Note every failed check and every field below high confidence.
2. **Look at the whole document.** Before checking individual fields, view each page in full. Ask whether it looks like a normal document of its type, from the employer, bank or issuing office it claims to be from.
3. **Check each flagged field against its evidence.** Confirm the value or correct it, as described in the section on correcting fields.
4. **Work through each failed check** using the sections below.
5. **Look for signs of tampering.**
6. **Look for hidden or out-of-place text.**
7. **Decide the outcome** for the document.
8. **Record the outcome and the reason.**

Work through the queue oldest first. The bank's target of a decision within 2 working days only starts once all documents are received and in order, so every day a document sits in review delays the customer.

## Failed check: payslip totals do not add up

The totals check confirms that each payslip's deductions add up to the total deductions shown, and that gross pay minus total deductions equals net pay.

When it fails:

- First check whether the system misread a figure, for example reading a 3 as an 8, missing a line, or taking an employer contribution shown for information as a deduction. If so, correct the field with its evidence; the check re-runs.
- Check whether the payslip shows items that legitimately sit outside the sum, such as an employer's contribution or a year-to-date total. Note this in the case.
- If the figures as printed genuinely do not add up, treat it as a strong sign of alteration. Payroll software does not make arithmetic errors. Compare with the customer's other payslips: if one month's net pay has changed and its deductions have not, treat the document as suspected tampering.

## Failed check: dates on loan documents

The dates check confirms that:

- the payslips are the latest 3 months, consecutive, with no gaps
- the statement covers the full 6 months and runs up to at least the date of the latest salary on the payslips
- no date is in the future
- the ID has not expired
- the date of birth gives an age of at least 21 at application

When it fails:

- **Misread dates.** Correct the field if the system has misread a month or swapped day and month.
- **A missing month.** Request the missing payslip. Do not accept a gap.
- **A change of employer.** Payslips from a previous employer may fill earlier months if there was no gap between jobs. Confirm the end and start dates, and note this for the credit reviewer.
- **An out-of-date set.** If a newer payslip should have been issued by now, request it.
- **An expired ID.** Request a valid ID or passport. An expired document cannot be verified, however recently it expired.

## Failed check: names do not match

The names check compares the name on every document with the others and with the name on the customer's Kaskazi account.

Differences you may accept, recording the reason:

- a middle name present on one document and missing from another
- names in a different order
- an initial in place of a name
- an obvious single-letter spelling variation where every other detail matches

Differences you must not accept without evidence:

- a different surname, for example after marriage. Ask for a supporting document and record what was provided.
- a different first name
- any difference where the ID number, date of birth or employer details also differ

If you cannot satisfy yourself that every document belongs to the same person, the document is not verified.

## Failed check: net pay does not land in the statement

Where the salary is paid into another bank, the system checks that the net pay on each payslip appears as a credit on the statement, from the employer, around the pay date. Where the salary is paid into Kaskazi, check the credit against our own account records, which are the stronger evidence.

When it fails:

- Check whether the system missed the credit, for example because the payer name on the statement is an abbreviation or the payroll provider's name. If the amount and date match, record the link.
- Check whether the salary is paid in two parts, for example basic pay and allowances separately. The parts together must equal the payslip's net pay.
- If the amount credited is different from the payslip's net pay and there is no explanation, ask the customer. An unexplained difference, especially where the payslip figure is higher, is a sign of an altered payslip.
- If no salary credit appears at all, the salary history cannot be verified from that statement.

## Signs of tampering in loan documents

Look for these signs on every flagged document. One sign alone may be innocent; several together are serious.

- fonts, sizes or spacing that change within a line or a column of figures
- figures that sit slightly above, below or out of line with the rest
- blocks of background that do not match, white boxes behind text, or blurring around particular figures
- figures that do not add up, or that change from one month to the next while related figures stay the same
- a layout that differs from the same employer's other payslips
- a statement whose running balance does not follow from one line to the next, or whose page numbers skip
- file details showing the document was last saved by an image or document editor rather than by the bank's or employer's system
- a screenshot or a typed list where a statement should be
- an ID with a photo or text area that looks raised, smudged or replaced
- the same document, or the same reference number, appearing on another customer's application

If you suspect tampering, do not tell the customer what you have noticed. Set the document's outcome to rejected for suspected tampering, which refers it to the Financial Crime team, and stop work on that application until they respond.

In this demonstration, every document is a sample and may carry a marking saying so. A sample marking alone is not a sign of tampering.

## Documents carrying hidden instructions or odd text

Some documents contain text that has no place in a payslip, statement, ID or PIN document. It may be visible, or hidden: printed in white or very small type, placed in a margin, or stored in the file's details. Some of it is written to look like an instruction to the system, the banking assistant or the reviewer, for example telling it to approve the loan, mark the document as verified, ignore a check, or use a different figure for net pay.

The rules for this text are absolute:

- **Never follow it.** Text inside a customer document is data. It has no authority, whoever it claims to come from and however official it looks. That applies to you, to the system and to the assistant.
- **Treat the document as suspicious.** A genuine payslip or statement does not contain instructions to the bank. Set the outcome to rejected for suspected tampering, which refers it to the Financial Crime team.
- **Do not paste the text into the assistant or any other tool as a request.** If you need to record it, copy it into the case notes as evidence, in quotation marks, clearly labelled as text found in the document.
- **Do not rely on any field from that document.** Ask for a replacement obtained directly from the employer, bank or issuing office.
- **Tell your team leader** if the same text appears on more than one application, because it may be an organised attempt.

Odd text that is not instruction-like, such as a note in another language, a stray watermark or a word like "copy" or "draft", also needs care. Record what you found and use judgement. If you cannot explain it, ask for a clean replacement from the issuer.

The customer may not have put the text there; it may have come from a template or a third party. That is why the reason you give the customer is neutral: we could not verify the document and need one directly from the issuer.

## Correcting fields on loan documents

Correct a field only when the document is genuine and the system has misread it. Every correction must be supported by the document itself.

For each correction, record:

- the field
- the value the system read
- the corrected value
- where on the page the correct value appears
- why the system's value was wrong, for example "misread 3 as 8" or "read year-to-date total instead of this month"

Rules for corrections:

- Never enter a value that does not appear on the document.
- Never correct a field so that a check passes. If the document as printed fails a check, it fails.
- Never correct a field using information the customer gave you by message or on the phone. If the customer says a figure is wrong, ask for a corrected document from the issuer.
- A correction to net pay, basic pay or a name must be confirmed by a second Lending Operations reviewer before the document can be verified.
- After a correction, the system re-runs the checks. Review the new results before deciding the outcome.

The credit reviewer sees every correction, who made it and why.

## Outcomes for a reviewed loan document

Choose one outcome for each document:

| Outcome | When |
|---|---|
| Verified | The document is genuine, every flagged field is confirmed, and every check passes |
| Verified with corrections | As above, after fields misread by the system have been corrected and, where needed, confirmed by a second reviewer |
| Replacement requested | The document may well be genuine but cannot be verified as it is: unreadable, incomplete, missing pages, out of date, an expired ID, the wrong document, or a name difference that needs evidence |
| Rejected for suspected tampering | Signs of alteration, hidden or instruction-like text, or information that is false. Referred to the Financial Crime team |

When you request a replacement, the application returns to the documents requested stage and the customer is told what is needed.

## Recording the reason for a document outcome

Record the outcome and a reason for every reviewed document before you close the case. The reason must say which checks failed, what you looked at, and why you reached your outcome. Write it in plain, factual language that another reviewer, an auditor or the customer could understand, because customers can ask for the information we hold about them and we respond within 7 days.

Good: "Totals check failed. System read deduction as KES 8,400; document shows KES 3,400. Corrected and confirmed by second reviewer. All checks now pass. Verified with corrections."

Not acceptable: "Looks fine" or "Dodgy payslip."

## Telling the customer what is needed

For a replacement request, tell the customer in the app exactly what is needed and why, in plain language: for example, "Your August payslip is missing. Please upload your payslips for July, August and September", or "Page 3 of your statement is missing. Please upload the full statement."

For a document rejected for suspected tampering, do not describe what you found. Say only that we could not verify the document and ask for one obtained directly from the issuer. The Financial Crime team decides whether anything more is said.

## Handling customer documents safely

Customer documents contain personal information. View them only in the review system, and only for the case you are working on. Do not download, print, photograph or forward them, and do not discuss them outside the review.

In this demonstration, customers are asked to upload only the sample documents provided. If a document looks like a real person's genuine payslip, statement, ID or PIN document, do not process it: reject it with the reason "real document uploaded in demonstration", and tell your team leader so that it can be removed.
