# EBT Coordination Copilot Project Scope

## Focus

A narrow, buildable first product: a **Shadow-Mode EBT Review Packet Generator** that later expands into an Outlook-connected EBT Coordination Copilot.

## Table of Contents

1. [Project Summary](#1-project-summary)
2. [Why This Comes First](#2-why-this-comes-first)
3. [Problem](#3-problem)
4. [Product Goal](#4-product-goal)
5. [Core User](#5-core-user)
6. [MVP Definition](#6-mvp-definition)
7. [What the EBT Copilot Does](#7-what-the-ebt-copilot-does)
8. [What the EBT Copilot Does Not Do](#8-what-the-ebt-copilot-does-not-do)
9. [Workflow Pipeline](#9-workflow-pipeline)
10. [Input Sources](#10-input-sources)
11. [Output: EBT Review Packet](#11-output-ebt-review-packet)
12. [Example Review Tables](#12-example-review-tables)
13. [Mini-Agents](#13-mini-agents)
14. [Human Review Workflow](#14-human-review-workflow)
15. [Extraction Confidence](#15-extraction-confidence)
16. [Source-Backed Design](#16-source-backed-design)
17. [Calendar Logic](#17-calendar-logic)
18. [Deadline and Risk Logic](#18-deadline-and-risk-logic)
19. [Email Follow-Up Support](#19-email-follow-up-support)
20. [Security and Privacy](#20-security-and-privacy)
21. [Context Engineering](#21-context-engineering)
22. [Technical Architecture](#22-technical-architecture)
23. [Data Objects](#23-data-objects)
24. [MVP Phases](#24-mvp-phases)
25. [Success Metrics](#25-success-metrics)
26. [Final EBT Product Pitch](#26-final-ebt-product-pitch)

---

# 1. Project Summary

The **EBT Coordination Copilot** helps litigation staff coordinate depositions by converting messy emails, court orders, calendars, and availability responses into a structured, source-backed review packet.

The first version operates in **shadow mode**.

That means:

```text
The AI prepares the packet.
The human reviews and corrects it.
The system does not send emails.
The system does not write to calendars.
The system does not make legal decisions.
```

The first deliverable is:

```text
EBT Coordination Review Packet
```

---

# 2. Why This Comes First

The project should start with the EBT Coordinator because it is the easiest mini-tool to test quickly.

Reasons:

```text
- EBT coordination already lives heavily in Outlook
- Email threads contain most of the needed data
- Attorney calendars are directly relevant
- The output can be tested as an Excel-style table
- The workflow is repetitive and painful
- The product can prove value without legal automation
- It is easier to validate than full e-filing or legal research
```

The build plan:

```text
Build one working mini-tool first.
Test it on Outlook-style email/calendar workflows.
Then expand into other modules.
```

---

# 3. Problem

EBT coordination is operationally painful.

A paralegal may need to review:

```text
- Long Outlook email threads
- OPA responses
- Defense counsel responses
- Witness availability
- Internal attorney calendar conflicts
- Court reporter availability
- Court orders
- Compliance conference orders
- Deposition notices
- Prior stipulations
```

The manual workflow often looks like:

```text
Read 10-30 emails
Extract dates manually
Check internal attorney calendar
Find the latest controlling order
Build a spreadsheet
Track who responded
Track who did not respond
Draft follow-up emails
Ask attorney about risky dates
Repeat when someone changes availability
```

The problem is not just picking a date.

The problem is producing a reliable review packet.

---

# 4. Product Goal

The goal is to reduce EBT coordination review from hours to minutes.

The system should:

```text
Read the email thread.
Check attorney calendar availability.
Extract OPA / defense / witness availability.
Extract possible EBT deadline from court order.
Flag conflicts and missing responses.
Generate an Excel-style review packet.
Show sources.
Assign confidence scores.
Let the human correct everything.
```

---

# 5. Core User

## Primary User

```text
Paralegal / calendar clerk / litigation support staff
```

Needs:

```text
- Fast availability extraction
- Calendar conflict visibility
- Missing response tracker
- Deadline-risk warning
- Excel-style table
- Source-backed summary
- Easy correction workflow
```

## Secondary User

```text
Attorney
```

Needs:

```text
- Quick review
- Risk flags
- Source references
- Clear timeline
- Confidence that nothing was silently decided by AI
```

---

# 6. MVP Definition

The MVP is:

```text
A shadow-mode EBT review packet generator.
```

It takes:

```text
- Outlook email thread or uploaded email file
- Court order PDF
- Attorney calendar data
```

It produces:

```text
- Availability matrix
- Candidate date table
- Calendar conflict table
- Missing response tracker
- Possible deadline risk table
- Source-backed summary
- Communication timeline
- Audit log
- Excel/CSV/PDF export
```

It should not send emails or create calendar events in MVP-1.

---

# 7. What the EBT Copilot Does

The EBT Copilot helps answer:

```text
Who needs to be scheduled?
Who has responded?
Who has not responded?
What availability did each person provide?
What dates did OPA offer?
What dates did defense counsel offer?
What dates did the witness offer?
Is our attorney actually available?
Which dates are possible?
Which dates are risky?
Which dates are outside a possible court-ordered deadline?
What source supports each date?
What needs human correction?
What needs attorney review?
```

---

# 8. What the EBT Copilot Does Not Do

The system does not:

```text
- Send emails automatically
- File documents
- Make legal decisions
- Guarantee legal deadline correctness
- Decide whether an adjournment is allowed
- Replace attorney review
- Replace paralegal judgment
- Create calendar events without approval
```

---

# 9. Workflow Pipeline

## 9.1 High-Level Pipeline

```text
User starts EBT packet
        ↓
User selects or uploads email thread
        ↓
User uploads court order / compliance order
        ↓
User connects or uploads attorney calendar data
        ↓
AI extracts people, dates, availability, conditions, and possible deadlines
        ↓
AI checks calendar conflicts
        ↓
AI assigns confidence scores
        ↓
AI flags missing responses, contradictions, and ambiguity
        ↓
AI generates review tables
        ↓
Human reviews and corrects
        ↓
System exports Excel/PDF/CSV packet
        ↓
System logs all actions
```

---

## 9.2 Step-by-Step Pipeline

<details>
<summary>Step 1: Start EBT Task</summary>

User creates a task:

```text
Schedule EBT of Dr. Smith
```

System collects:

```text
- Case name
- Index number
- Court
- County
- Judge / Part
- Assigned attorney
- Assigned paralegal
- Deponent / witness
- Known deadline, if any
```

</details>

<details>
<summary>Step 2: Ingest Email Thread</summary>

System reads the email thread and extracts:

```text
- Senders
- Recipients
- Parties
- Proposed dates
- Available dates
- Unavailable dates
- Conditions
- Pending confirmations
- Missing responses
```

Example:

```text
Email:
Dr. Smith can do June 18 after 1 PM or June 24 in the morning.
He is unavailable June 20.
```

Extraction:

```text
Available:
- June 18 after 1 PM
- June 24 morning

Unavailable:
- June 20

Source:
OPA email dated June 5
```

</details>

<details>
<summary>Step 3: Ingest Court Order</summary>

System reads the court order and extracts possible procedural dates.

Examples:

```text
- Possible EBT deadline
- Compliance conference date
- Discovery deadline
- Final conference date
```

Safe output:

```text
Possible EBT deadline found: June 30.
Source: Compliance Conference Order, page 2.
Human confirmation required.
```

</details>

<details>
<summary>Step 4: Check Internal Attorney Calendar</summary>

System checks the assigned attorney calendar in read-only mode.

It identifies:

```text
- Free blocks
- Busy blocks
- Tentative holds
- Court appearances
- Trials
- Other depositions
- OOO
- Private events
```

Output:

```text
June 18 at 2 PM:
Attorney appears available.
Confidence: High.

June 24 at 10 AM:
Attorney has tentative hold.
Confidence: Medium.
Human review required.
```

</details>

<details>
<summary>Step 5: Generate Review Packet</summary>

System generates:

```text
- Candidate date table
- Participant availability table
- Calendar conflict table
- Missing response tracker
- Deadline risk table
- Communication timeline
- Audit log
```

</details>

<details>
<summary>Step 6: Human Correction</summary>

User confirms or corrects:

```text
- Dates
- Times
- Availability
- Conditions
- Source references
- Calendar conflicts
- Deadline type
- Risk level
```

Corrections are saved and logged.

</details>

---

# 10. Input Sources

## MVP-1 Inputs

```text
- Uploaded .eml or .msg email thread
- Pasted email thread
- PDF of email thread
- Court order PDF
- Calendar export or manual calendar data
```

## MVP-2 Inputs

```text
- Outlook read-only email thread selection
- Outlook read-only calendar access
- Uploaded court order
```

## Later Inputs

```text
- Outlook add-in
- Gmail integration
- Case management system
- Document management system
- NYSCEF/PACER import
```

---

# 11. Output: EBT Review Packet

The output is:

```text
EBT Coordination Review Packet
├── Case Summary
├── EBT Task Summary
├── Possible Controlling Deadline
├── Human Deadline Confirmation Status
├── Candidate Date Table
├── Participant Availability Table
├── Internal Attorney Calendar Conflict Table
├── Missing Response Tracker
├── Deadline Risk Table
├── Source-Backed Summary
├── Communication Timeline
├── Human Corrections
├── AI Audit Log
└── Excel / CSV / PDF Export
```

---

# 12. Example Review Tables

## 12.1 Candidate Date Table

<details>
<summary>Example: Candidate Date Table</summary>

| Date | Time | Proposed By | Format / Location | Internal Attorney | Defense Counsel | OPA | Witness | Court Reporter | Internal Conflict | Deadline Risk | Confidence | Status | Sources |
|---|---:|---|---|---|---|---|---|---|---|---|---|---|---|
| Jun 12 | 10:00 AM | Defense | Remote | Available | Unavailable | Unknown | Available | Unknown | None | Within possible deadline | Medium | Not viable | Email 4, Calendar |
| Jun 14 | 2:00 PM | OPA | In-person | Partial conflict | Available | Available | Pending | Available | Attorney has 3 PM conference | Within possible deadline | Medium | Risky | Email 7, Calendar |
| Jun 18 | 2:00 PM | Plaintiff | Remote | Available | Available | Available | Available after 1 PM | Available | Clear | Within possible deadline | High | Best candidate for review | Email 9, Order, Calendar |
| Jun 24 | 10:00 AM | Defense | Remote | Tentative hold | Available | Partial | Available | Unknown | Needs confirmation | Close to possible deadline | Medium | Needs review | Email 12, Order |
| Jul 2 | 10:00 AM | OPA | Remote | Available | Available | Available | Available | Available | Clear | Outside possible deadline | High | Attorney review required | Order |

</details>

---

## 12.2 Participant Availability Table

<details>
<summary>Example: Participant Availability Table</summary>

| Person / Entity | Role | Availability Provided | Unavailable Dates | Conditions | Last Response | Missing Info | Follow-Up Needed | Confidence | Source |
|---|---|---|---|---|---|---|---|---|---|
| Attorney A | Internal plaintiff attorney | Jun 12, 14, 18, 24 | Jun 20 | Tentative hold Jun 24 | Calendar checked | Confirm tentative hold | Yes | Medium | Internal calendar |
| Defense Counsel A | Defense attorney | Jun 18, Jun 24 | Jun 12 | Remote preferred | Jun 4 email | None | No | High | Email 004 |
| OPA | Opposing office contact | Jun 14, Jun 18 | — | Needs witness confirmation | Jun 5 email | Confirm witness | Yes | Medium | Email 006 |
| Dr. Smith | Witness / deponent | Jun 18 after 1 PM | Jun 14 | Remote only | Jun 3 email | Confirm location | Yes | High | Email 003 |
| Court Reporter | Vendor | Jun 18 | Jun 14 | Remote available | Jun 5 email | Confirm booking | Yes | High | Email 008 |

</details>

---

## 12.3 Missing Response Tracker

<details>
<summary>Example: Missing Response Tracker</summary>

| Person / Entity | Needed Response | Response Method | Last Contacted | Days Waiting | Firm Deadline for Response | Suggested Follow-Up | Priority |
|---|---|---|---:|---:|---|---|---|
| OPA | Confirm witness availability for Jun 18 | Email | Jun 5 | 2 | Jun 10 | Send follow-up email | High |
| Court Reporter | Confirm availability and remote/in-person format | Phone/email | Jun 5 | 2 | Jun 11 | Send vendor follow-up | Medium |
| Defense Counsel B | Confirm no conflict with Jun 18 | Email | Jun 4 | 3 | Jun 10 | Send reminder | Medium |
| Internal Attorney | Confirm Jun 24 tentative hold | Internal email | Jun 6 | 1 | Jun 9 | Ask attorney to confirm | Low |

</details>

---

## 12.4 Communication Timeline

<details>
<summary>Example: Communication Timeline</summary>

| Date | Event | Actor | Source | Notes |
|---|---|---|---|---|
| May 12 | Compliance conference order entered | Court | Order PDF | Possible EBT deadline set for Jun 30 |
| May 18 | Plaintiff requested availability | Paralegal | Email 001 | Asked for June dates |
| May 22 | Defense responded | Defense counsel | Email 003 | Available Jun 18 and Jun 24 |
| May 28 | OPA responded | OPA | Email 005 | Needs witness confirmation |
| Jun 3 | Witness availability received | OPA / witness | Email 006 | Jun 18 after 1 PM |
| Jun 5 | Court reporter availability requested | Paralegal | Email 008 | Awaiting response |
| Jun 6 | Copilot generated review packet | System | Audit log | Jun 18 marked best candidate for review |

</details>

---

# 13. Mini-Agents

The EBT tool can be split into small mini-agents.

```text
Email Extraction Agent
Calendar Conflict Agent
Deadline Extraction Agent
Availability Matrix Agent
Missing Response Agent
Contradiction Detection Agent
Confidence Scoring Agent
Source Citation Agent
Review Packet Agent
Timeline Agent
Audit Log Agent
Human Review Router
```

Each mini-agent should have:

```text
- Narrow input
- Narrow output
- JSON schema
- Source requirement
- Validation step
- Human review fallback
```

---

# 14. Human Review Workflow

Human review is required for:

```text
- Extracted availability
- Extracted possible deadline
- Calendar conflict severity
- Candidate date status
- Missing response status
- Low-confidence entries
- Contradictory sources
- Final packet export
```

Review states:

```text
Pending review
Confirmed
Corrected
Rejected
Needs attorney review
```

---

# 15. Extraction Confidence

Every extracted item should show:

```text
- Extracted value
- Source
- Confidence
- Reason
- Human confirmation status
```

Confidence labels:

```text
High:
Clear date/time stated directly.

Medium:
Likely date/time but conditional or incomplete.

Low:
Ambiguous, inferred, contradictory, or unclear.
```

Example:

```text
Extracted:
June 18 after 1 PM

Source:
OPA email dated June 5

Confidence:
High

Human status:
Pending confirmation
```

---

# 16. Source-Backed Design

Every major output should show its source.

Safe wording:

```text
Possible EBT deadline found: June 30.
Source: Compliance Conference Order dated May 12, page 2.
Human confirmation required.
```

Source reliability tiers:

```text
Primary:
Court order, official docket, filed stipulation.

Secondary:
Email from OPA, defense counsel, attorney, vendor.

Tertiary:
Internal note, voicemail summary, informal reminder.
```

---

# 17. Calendar Logic

Calendar access should be read-only first.

The system checks:

```text
- Free blocks
- Busy blocks
- Tentative holds
- Court appearances
- Other EBTs
- Trials
- OOO
- Private events
```

Conflict labels:

```text
No conflict:
Calendar appears free.

Possible conflict:
Tentative hold or unclear event.

Hard conflict:
Busy event likely blocks deposition.

Unknown:
Calendar unavailable or private.
```

---

# 18. Deadline and Risk Logic

The system should use cautious risk language.

Date types:

```text
- Proposed date
- Available date
- Unavailable date
- Internal target date
- Court appearance date
- Discovery deadline
- Possible EBT deadline
- Compliance conference date
- Follow-up date
```

Risk labels:

```text
Green:
Appears available and before possible deadline.

Yellow:
Possible but missing confirmation, tentative conflict, or limited buffer.

Red:
Likely conflict or close to possible deadline.

Critical:
Outside possible court-ordered deadline or requires attorney review.
```

Avoid:

```text
This date is legally compliant.
This violates the order.
```

Use:

```text
Possible deadline issue.
Attorney review recommended.
```

---

# 19. Email Follow-Up Support

MVP-1 should not require email drafting.

MVP-2 can add draft support.

Draft types:

```text
- Short follow-up
- Cooperative scheduling email
- Firm deadline-focused email
- Internal attorney update
- Clarification request
```

The system should never auto-send.

---

# 20. Security and Privacy

MVP security principles:

```text
- Read-only Outlook permissions
- Least-privilege access
- No auto-send
- No auto-calendar write
- No training on user data
- Configurable retention
- Delete uploaded source files after export option
- Audit logs for packet generation and export
```

For sensitive firms:

```text
- Private cloud option
- Self-hosted option
- Local database
- Local object storage
- Local OCR
- Local LLM option
```

---

# 21. Context Engineering

For MVP-1, use bounded direct context:

```text
- One email thread
- One court order
- One calendar export
```

Rules:

```text
Do not process the whole case file.
Do not silently truncate.
Do not assume long context means reliable recall.
Always attach sources to extracted dates.
Use chunking/RAG only when documents become too large.
```

Later:

```text
MVP-2:
Direct context + confirmed packet facts.

MVP-3:
RAG with pgvector and metadata filters.

MVP-4:
Firm knowledge repository and external research.
```

---

# 22. Technical Architecture

Recommended MVP architecture:

```text
Frontend:
Next.js / React

Backend:
Node / Next.js API or FastAPI

AI Worker:
Python FastAPI worker

Database:
Postgres

Storage:
Encrypted file storage

Exports:
Excel / CSV / PDF

Calendar:
Outlook read-only integration or manual calendar upload
```

Processing flow:

```text
Upload email/order/calendar data
        ↓
Create processing job
        ↓
Extract dates, people, availability, possible deadlines
        ↓
Validate structured output
        ↓
Assign confidence and risk
        ↓
Generate review packet
        ↓
Human corrects
        ↓
Export packet
        ↓
Log actions
```

---

# 23. Data Objects

## 23.1 EBT Task

```json
{
  "task_id": "task_001",
  "case_id": "case_001",
  "task_type": "ebt_coordination",
  "deponent": "Dr. Smith",
  "status": "review_packet_generated",
  "human_review_status": "pending"
}
```

## 23.2 Availability Entry

```json
{
  "participant_id": "p_001",
  "availability_type": "available",
  "date": "2026-06-18",
  "start_time": "13:00",
  "end_time": "17:00",
  "condition": "remote only",
  "confidence": "high",
  "source_id": "email_004",
  "human_confirmed": false
}
```

## 23.3 Calendar Conflict

```json
{
  "attorney_id": "attorney_001",
  "date": "2026-06-24",
  "start_time": "10:00",
  "end_time": "11:00",
  "calendar_status": "tentative",
  "event_label": "Hold",
  "conflict_level": "possible",
  "confidence": "medium",
  "human_review_required": true
}
```

## 23.4 Possible Deadline

```json
{
  "deadline_date": "2026-06-30",
  "deadline_type": "possible_court_ordered_ebt_deadline",
  "risk_level": "high_if_missed",
  "source_id": "order_002",
  "source_page": 2,
  "human_confirmed": false,
  "attorney_review_required_if_outside": true
}
```

---

# 24. MVP Phases

## MVP-1: Shadow-Mode Packet Generator

```text
- Upload email thread
- Upload court order
- Upload/manual calendar data
- Extract availability
- Extract possible deadline
- Check conflicts
- Generate review packet
- Export Excel/CSV/PDF
```

## MVP-2: Outlook Read-Only App

```text
- Connect Outlook read-only
- Select email thread
- Select attorney calendar
- Generate packet from live data
```

## MVP-3: Drafting and Reminders

```text
- Draft follow-up emails
- Suggest attachments
- Create follow-up reminders
- Save draft with human approval
```

## MVP-4: Broader Litigation Workflow

```text
- Filing packet support
- Transcript follow-up
- Subpoena tracking
- Compliance conference workflows
```

---

# 25. Success Metrics

## Time Savings

```text
Manual:
1-3 hours

With copilot:
5-15 minutes review time
```

## Quality

```text
- Availability extraction accuracy
- Calendar conflict accuracy
- Source citation accuracy
- Correction rate
- Low-confidence rate
```

## Trust

```text
- % confirmed without correction
- Number of source links opened
- Attorney review acceptance rate
- High-risk escalation accuracy
```

---

# 26. Final EBT Product Pitch

```text
The EBT Coordination Copilot is a shadow-mode review packet generator for deposition scheduling.

It reads Outlook email threads, court orders, and attorney calendar data, then creates an Excel-style packet showing who is available, who has not responded, which dates conflict with the attorney calendar, which dates may be risky, and where every extracted fact came from.

It does not send emails, create calendar events, file documents, or make legal decisions.

It helps paralegals prepare the scheduling review packet faster, while keeping every step source-backed and human-reviewed.
```
