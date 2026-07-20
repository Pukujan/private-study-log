# Litigation AI Copilot Platform Scope  
## Focus: AI Copilot Layer + EBT Coordination Copilot

## Table of Contents

1. [Project Concept](#1-project-concept)  
2. [Core Product Positioning](#2-core-product-positioning)  
3. [What the Copilot Actually Does](#3-what-the-copilot-actually-does)  
4. [Main Platform Layers](#4-main-platform-layers)  
5. [Priority Module: EBT Coordination Copilot](#5-priority-module-ebt-coordination-copilot)  
6. [EBT Coordination Workflow Pipeline](#6-ebt-coordination-workflow-pipeline)  
7. [EBT Review Packet Output](#7-ebt-review-packet-output)  
8. [Example Tables](#8-example-tables)  
9. [Source-Backed Summary System](#9-source-backed-summary-system)  
10. [Calendar and Availability Logic](#10-calendar-and-availability-logic)  
11. [Deadline and Risk Logic](#11-deadline-and-risk-logic)  
12. [Email Drafting and Back-and-Forth Support](#12-email-drafting-and-back-and-forth-support)  
13. [Suggested Attachments](#13-suggested-attachments)  
14. [Timeline and Audit Logs](#14-timeline-and-audit-logs)  
15. [Human Review and Approval Model](#15-human-review-and-approval-model)  
16. [MVP Scope](#16-mvp-scope)  
17. [Future Scope](#17-future-scope)  
18. [Technical Architecture](#18-technical-architecture)  
19. [Success Metrics](#19-success-metrics)  
20. [Final Product Pitch](#20-final-product-pitch)  

---

# 1. Project Concept

Build a **Litigation AI Copilot Platform** that reduces procedural litigation workload from hours to minutes.

The platform is not an “AI lawyer” and does not replace attorneys, paralegals, or legal judgment. It acts as a **source-backed workflow assistant** for litigation operations.

The system helps legal staff move faster through procedural tasks by reading case files, email threads, calendars, court orders, deposition notices, and prior filings, then turning them into guided next steps, review tables, summaries, draft communications, suggested attachments, timelines, and logs.

The first major module should be the **EBT Coordination Copilot**.

This module focuses on deposition scheduling, attorney calendar checking, OPA availability, multi-party availability tracking, court-ordered deadlines, back-and-forth emails, source-backed summaries, Excel-style scheduling tables, and human approval.

---

# 2. Core Product Positioning

## Product Category

**AI Copilot OS for Litigation Operations**

## First Module

**EBT Coordination Copilot**

## Core Value Proposition

```text
Turn messy deposition coordination into a structured, source-backed review packet.

Instead of manually reading 20 emails, checking attorney calendars, reviewing court orders,
building a spreadsheet, drafting follow-ups, and tracking responses,
the copilot prepares the scheduling review packet for human review.
```

## What Makes It Different

The product is not just a chatbot and not just a calendar tool.

It combines:

```text
Case documents
+ emails
+ attorney calendars
+ OPA availability
+ witness availability
+ court orders
+ procedural deadlines
+ source-backed summaries
+ Excel-style review tables
+ draft emails
+ follow-up reminders
+ audit logs
```

The result is a guided litigation workflow assistant.

---

# 3. What the Copilot Actually Does

The copilot helps answer questions like:

```text
What task are we trying to complete?
What documents matter for this task?
What court order controls this deadline?
Who has already responded?
Who has not responded?
What availability did each person provide?
Does our own attorney have a conflict?
Does OPA have availability?
Does the witness have availability?
Which dates are mutually available?
Which dates are risky?
Which dates are outside the court-ordered deadline?
What should we send next?
What PDF should be attached?
Where is the source proof?
What needs attorney review?
```

The copilot produces structured outputs:

```text
- Availability matrix
- Mutual date candidate table
- Attorney calendar conflict table
- OPA/witness response tracker
- Deadline risk table
- Source-backed summary
- Timeline of communications
- Follow-up tracker
- Draft emails
- Suggested attachments
- Audit log
```

---

# 4. Main Platform Layers

## 4.1 Case Workspace Copilot

This layer reads and organizes case-level information.

It handles:

```text
- Court orders
- Prior filings
- Deposition notices
- Email threads
- NYSCEF/PACER docket entries
- Stipulations
- Prior conference orders
- Internal notes
- Case task history
```

It helps users understand:

```text
- What happened
- What is pending
- What task is active
- What documents matter
- What deadline controls the next step
- What action needs review
```

---

## 4.2 Procedural Task Navigator

This layer converts messy case information into guided procedural tasks.

Examples:

```text
- Schedule EBT
- Follow up with OPA
- Prepare deposition notice
- Confirm court reporter
- Prepare compliance conference order
- File stipulation
- Follow up on transcript
- Confirm subpoena response
- Prepare motion packet
```

For each task, the copilot shows:

```text
- Current task
- Required steps
- Missing information
- Relevant documents
- Relevant deadlines
- Suggested next action
- Source proof
- Human approval checkpoint
```

---

## 4.3 Communication Copilot

This layer helps with emails and back-and-forth communication.

It handles:

```text
- Summarizing email threads
- Drafting OPA emails
- Drafting defense counsel emails
- Drafting internal attorney updates
- Suggesting response options
- Suggesting attachments
- Tracking who has responded
- Tracking who has not responded
- Creating follow-up reminders
```

The system should not auto-send emails without human approval.

---

## 4.4 Filing and Attachment Assembly Layer

This layer helps assemble the correct documents for a task.

It handles:

```text
- Suggested PDFs
- Prior orders
- Deposition notices
- Stipulations
- Email thread PDFs
- NYSCEF confirmations
- Subpoenas
- Prior correspondence
```

Each suggested attachment should explain:

```text
- Why it is suggested
- Which task it supports
- Whether it is required or optional
- What source supports it
```

---

## 4.5 EBT Coordination Copilot

This is the priority module.

It focuses on:

```text
- Deposition scheduling
- Internal attorney calendar checking
- OPA availability extraction
- Defense counsel availability extraction
- Witness availability extraction
- Court reporter availability tracking
- Mutual date calculation
- Excel-style scheduling tables
- Deadline risk flags
- Human review summaries
- Timeline and logs
- Email drafting
- Follow-up reminders
- Suggested attachments
```

---

# 5. Priority Module: EBT Coordination Copilot

## 5.1 Problem

EBT coordination is painful because it requires constant back-and-forth between multiple people.

A paralegal may need to check:

```text
- Plaintiff attorney calendar
- Defense counsel availability
- OPA availability
- Witness availability
- Court reporter availability
- Court-ordered EBT deadline
- Compliance conference order
- Deposition notice
- Prior emails
- Follow-up history
- Internal attorney instructions
```

For 10, 15, or 20+ people, this becomes a time-consuming coordination problem.

The work is not just “pick a date.”

The real work is:

```text
- Read every email
- Extract availability
- Check internal attorney calendars
- Find conflicts
- Review court orders
- Confirm deadline risk
- Build a spreadsheet
- Summarize the situation
- Draft follow-up emails
- Track who has not responded
- Attach the right PDFs
- Ask attorney for review when needed
```

---

## 5.2 Correct Product Framing

The EBT Coordination Copilot should not be described as simply “suggesting dates.”

Better framing:

```text
The EBT Coordination Copilot reads email threads, court orders, internal attorney calendars,
and OPA/witness availability, then generates a detailed Excel-style review packet with
mutual date candidates, conflicts, deadline risk, source-backed summaries, timelines,
logs, follow-up tasks, draft emails, and suggested attachments for human approval.
```

---

# 6. EBT Coordination Workflow Pipeline

## 6.1 High-Level Pipeline

```text
User starts EBT task
        ↓
Upload / connect email thread, order, calendar, deposition info
        ↓
AI extracts parties, witnesses, attorneys, OPA, and deadlines
        ↓
AI checks internal attorney calendar
        ↓
AI parses OPA / defense / witness availability
        ↓
AI compares all availability against calendar conflicts
        ↓
AI checks court-ordered EBT deadline
        ↓
AI generates Excel-style review tables
        ↓
AI creates source-backed summary
        ↓
AI drafts follow-up email
        ↓
AI suggests attachments
        ↓
Human reviews, edits, approves, and sends
        ↓
System logs action and creates follow-up reminders
```

---

## 6.2 Detailed Pipeline

<details>
<summary>Step 1: Start EBT Coordination Task</summary>

The user opens a case and starts an EBT coordination task.

The system asks for or retrieves:

```text
- Case name
- Index number
- County
- Judge / Part
- EBT subject
- Witness / deponent name
- Parties involved
- Assigned internal attorney
- Assigned paralegal
- Existing deadline, if known
```

Example:

```text
Task: Schedule EBT of Dr. Smith
Case: Doe v. Hospital
County: Westchester
Assigned attorney: Attorney A
Assigned paralegal: Paralegal B
```

</details>

---

<details>
<summary>Step 2: Ingest Documents and Emails</summary>

The system reads:

```text
- Email thread with OPA / defense counsel
- Court order
- Compliance conference order
- Deposition notice
- Prior stipulation
- Prior scheduling email
- Internal instruction email
```

The system extracts:

```text
- People
- Roles
- Dates
- Deadlines
- Availability statements
- Missing responses
- Attachments mentioned
- Court instructions
```

</details>

---

<details>
<summary>Step 3: Check Internal Attorney Calendar</summary>

The system checks the assigned attorney’s calendar before treating any date as usable.

It detects:

```text
- Existing court appearances
- Other depositions
- Trials
- Conferences
- Motion appearances
- Client meetings
- OOO / vacation
- Internal holds
- Travel blocks
```

Example finding:

```text
June 18 at 2:00 PM appears available from OPA and defense counsel,
but internal Attorney A has a motion conference from 1:30 PM to 2:30 PM.

Status: Conflict detected.
Action: Do not propose this date without human review.
```

</details>

---

<details>
<summary>Step 4: Parse OPA / External Availability</summary>

The system reads emails and extracts availability from natural language.

Example email:

```text
OPA can do any afternoon after June 12 except June 17 and June 20.
```

Extracted result:

```text
OPA availability:
- Available: afternoons after June 12
- Unavailable: June 17, June 20
- Needs clarification: whether June 18 at 2 PM works for the witness
```

The system classifies responses:

```text
- Available
- Unavailable
- Conditionally available
- Unclear
- Pending response
- Needs follow-up
```

</details>

---

<details>
<summary>Step 5: Compare All Availability</summary>

The system compares:

```text
- Internal attorney calendar
- OPA availability
- Defense counsel availability
- Witness availability
- Court reporter availability
- Existing EBT deadline
- Court conference dates
```

Then it produces a structured review table.

The point is not to secretly decide the date.

The point is to prepare the human-review table.

</details>

---

<details>
<summary>Step 6: Generate Excel-Style Review Packet</summary>

The system generates tables for review.

The packet includes:

```text
- Mutual Date Candidate Table
- Participant Availability Table
- Calendar Conflict Table
- Deadline Risk Table
- Missing Response Tracker
- Timeline of Communications
- Suggested Next Action
- Email Draft
- Suggested Attachments
- Audit Log
```

The user can export this as:

```text
- Excel
- CSV
- PDF
- Case workspace table
```

</details>

---

<details>
<summary>Step 7: Human Review and Approval</summary>

The user reviews:

```text
- Extracted availability
- Calendar conflicts
- Deadline risks
- Recommended candidate dates
- Missing confirmations
- Email draft
- Suggested attachments
```

The user can:

```text
- Accept
- Edit
- Reject
- Mark uncertain
- Request attorney review
- Save draft
- Create follow-up reminder
```

</details>

---

# 7. EBT Review Packet Output

The main output should be an **EBT Coordination Review Packet**.

```text
EBT Coordination Review Packet
├── Case Summary
├── Task Summary
├── Controlling Deadline Summary
├── Availability Matrix
├── Mutual Date Candidate Table
├── Internal Calendar Conflict Table
├── OPA / External Response Tracker
├── Missing Information Tracker
├── Deadline Risk Table
├── Suggested Next Action
├── Draft Email
├── Suggested Attachments
├── Communication Timeline
└── Audit Log
```

This review packet is the main product deliverable.

---

# 8. Example Tables

## 8.1 Mutual Date Candidate Table

<details>
<summary>Example: Mutual Date Candidate Table</summary>

| Date | Time | Internal Attorney | Defense Counsel | OPA | Witness | Court Reporter | Internal Conflict | Deadline Risk | Status | Sources |
|---|---:|---|---|---|---|---|---|---|---|---|
| Jun 12 | 10:00 AM | Available | Unavailable | Unknown | Available | Unknown | None | Within deadline | Not viable | Email 4, Calendar |
| Jun 14 | 2:00 PM | Available | Available | Available | Pending | Available | Attorney has conference at 3 PM | Within deadline | Risky | Email 7, Calendar |
| Jun 18 | 10:00 AM | Available | Available | Available | Available | Available | Clear | Within deadline | Best candidate | Email 9, Order, Calendar |
| Jun 24 | 2:00 PM | Available | Available | Partial | Available | Unknown | Clear | Close to deadline | Needs review | Email 12, Order |
| Jul 2 | 10:00 AM | Available | Available | Available | Available | Available | Clear | Outside deadline | Attorney review required | Order |

</details>

---

## 8.2 Participant Availability Table

<details>
<summary>Example: Participant Availability Table</summary>

| Person / Entity | Role | Availability Provided | Unavailable Dates | Last Response | Missing Info | Follow-Up Needed | Source |
|---|---|---|---|---|---|---|---|
| Attorney A | Internal plaintiff attorney | Jun 12, 14, 18, 24 | Jun 20 | Calendar checked | None | No | Internal calendar |
| Defense Counsel A | Defense attorney | Jun 18, 24 | Jun 12 | Jun 4 email | None | No | Email 004 |
| OPA | Opposing party / office contact | Jun 14, 18 | — | Jun 5 email | Confirm witness availability | Yes | Email 006 |
| Dr. Smith | Witness / deponent | Jun 18 after 1 PM | Jun 14 | Jun 3 email | Confirm location | Yes | Email 003 |
| Court Reporter | Vendor | Jun 18 | Jun 14 | Jun 5 email | Confirm remote/in-person | Yes | Email 008 |

</details>

---

## 8.3 Internal Attorney Calendar Conflict Table

<details>
<summary>Example: Internal Attorney Calendar Conflict Table</summary>

| Date | Time | Calendar Event | Conflict Type | Severity | Notes |
|---|---:|---|---|---|---|
| Jun 14 | 3:00 PM | Compliance conference | Partial conflict | Medium | EBT at 2 PM may run into conference |
| Jun 18 | 10:00 AM | No event | No conflict | Low | Clear calendar window |
| Jun 20 | All day | Trial block | Full conflict | High | Do not schedule |
| Jun 24 | 2:00 PM | Internal meeting | Possible conflict | Medium | Can be moved if attorney approves |

</details>

---

## 8.4 Deadline Risk Table

<details>
<summary>Example: Deadline Risk Table</summary>

| Date | Deadline Relationship | Risk Level | Reason | Human Review Needed | Source |
|---|---|---|---|---|---|
| Jun 18 | Before EBT deadline | Low | Within court-ordered deadline | No | CC Order p.2 |
| Jun 24 | Before deadline but close | Medium | Close to deadline; little room for adjournment | Yes | CC Order p.2 |
| Jun 30 | Deadline date | High | Same day as EBT deadline | Yes | CC Order p.2 |
| Jul 2 | After deadline | Critical | Outside court-ordered EBT deadline | Attorney review required | CC Order p.2 |

</details>

---

## 8.5 Missing Response Tracker

<details>
<summary>Example: Missing Response Tracker</summary>

| Person / Entity | Needed Response | Last Contacted | Days Waiting | Suggested Follow-Up | Priority |
|---|---|---:|---:|---|---|
| OPA | Confirm witness availability for Jun 18 | Jun 5 | 2 | Send follow-up email | High |
| Court Reporter | Confirm availability and remote/in-person format | Jun 5 | 2 | Send vendor follow-up | Medium |
| Defense Counsel B | Confirm no conflict with Jun 18 | Jun 4 | 3 | Send reminder | Medium |
| Internal Attorney | Confirm Jun 24 backup date is acceptable | Jun 6 | 1 | Internal Slack/email | Low |

</details>

---

## 8.6 Communication Timeline

<details>
<summary>Example: Communication Timeline</summary>

| Date | Event | Actor | Source | Notes |
|---|---|---|---|---|
| May 12 | Compliance conference order entered | Court | Order PDF | EBT deadline set for Jun 30 |
| May 18 | Plaintiff requested availability | Paralegal | Email 001 | Asked for June dates |
| May 22 | Defense responded | Defense counsel | Email 003 | Available Jun 18 and Jun 24 |
| May 28 | OPA responded | OPA | Email 005 | Needs witness confirmation |
| Jun 3 | Witness availability received | OPA / witness | Email 006 | Jun 18 after 1 PM |
| Jun 5 | Court reporter availability requested | Paralegal | Email 008 | Awaiting response |
| Jun 6 | Copilot generated review packet | System | Audit log | Best candidate Jun 18, pending OPA confirmation |

</details>

---

## 8.7 Audit Log

<details>
<summary>Example: Audit Log</summary>

| Time | Action | Actor | Result |
|---|---|---|---|
| 4:02 PM | Parsed email thread | AI | 8 availability entries extracted |
| 4:03 PM | Checked internal attorney calendar | AI | 2 conflicts found |
| 4:04 PM | Parsed court order | AI | EBT deadline identified as Jun 30 |
| 4:05 PM | Generated availability matrix | AI | 5 candidate dates created |
| 4:06 PM | Flagged deadline risk | AI | Jul 2 marked critical |
| 4:07 PM | Generated draft email | AI | Follow-up to OPA prepared |
| 4:10 PM | Reviewed extracted availability | Human | Jun 18 confirmed as viable |
| 4:12 PM | Edited email draft | Human | Draft saved for review |

</details>

---

# 9. Source-Backed Summary System

Every major AI statement should have a source.

The system should not simply say:

```text
EBT deadline is June 30.
```

It should say:

```text
The latest compliance conference order appears to require EBTs to be completed by June 30.

Source:
Compliance Conference Order dated May 12, page 2.
```

The user should be able to open:

```text
- Source document
- Exact page
- Relevant paragraph
- Related email
- Related attachment
```

Source navigation should be grouped by task.

Example:

```text
Task: Schedule EBT of Dr. Smith

Sources:
1. Compliance Conference Order
   - Contains EBT deadline

2. Defense Counsel Email
   - Provides availability

3. OPA Email
   - Provides conditional witness availability

4. Internal Attorney Calendar
   - Confirms no conflict on Jun 18
```

---

# 10. Calendar and Availability Logic

The system must check both internal and external availability.

## 10.1 Internal Calendar Logic

The system checks:

```text
- Assigned attorney calendar
- Paralegal calendar
- Court conference calendar
- Existing deposition calendar
- Trial blocks
- OOO blocks
- Travel blocks
- Internal holds
```

It should detect:

```text
- Hard conflict
- Soft conflict
- Partial conflict
- No conflict
- Needs human review
```

Example:

```text
June 18 at 10 AM:
Internal attorney calendar is clear.
Status: Available.

June 14 at 2 PM:
Internal attorney has a conference at 3 PM.
Status: Risky due to possible overlap.
```

---

## 10.2 OPA / External Availability Logic

The system parses language like:

```text
- Any afternoon after June 12
- Available June 18 or June 24
- Unavailable the week of June 17
- Witness can only appear after 1 PM
- Counsel is on trial that week
- We need to check with the doctor
- OPA can produce witness before the deadline
```

It converts those into structured availability entries.

Example:

```json
{
  "entity": "OPA",
  "available_dates": ["2026-06-18", "2026-06-24"],
  "unavailable_dates": ["2026-06-17", "2026-06-20"],
  "conditions": ["afternoon only"],
  "needs_follow_up": true,
  "source": "Email 006"
}
```

---

# 11. Deadline and Risk Logic

The system should distinguish between different types of dates.

Not every date has the same legal risk.

## 11.1 Deadline Types

```text
- Internal preference date
- Attorney preference date
- OOO / vacation conflict
- Discovery deadline
- Court-ordered EBT deadline
- Judge-ordered strict deadline
- Compliance conference deadline
- Final conference deadline
- Part-rule deadline
```

## 11.2 Risk Levels

```text
Green:
Date appears available and within deadline.

Yellow:
Date may work but has conflict, missing confirmation, or is close to deadline.

Red:
Date is outside deadline or conflicts with order.

Critical:
Date violates court-ordered deadline or requires attorney review.
```

## 11.3 Example Risk Logic

```text
June 24:
Within EBT deadline but close to compliance conference.
Risk: Medium.

July 2:
Outside court-ordered EBT deadline.
Risk: Critical.
Attorney review required before proposing.
```

---

# 12. Email Drafting and Back-and-Forth Support

The copilot should draft emails based on the current review packet.

## 12.1 Draft Email Example

<details>
<summary>Example: Follow-Up Email to OPA</summary>

```text
Subject: Availability for EBT of Dr. Smith

Counsel,

We are following up regarding availability for the EBT of Dr. Smith.

Based on the responses received so far, June 18 at 2:00 PM appears to be the first mutually available date. Please confirm whether that date works for your office and the witness.

For reference, the current compliance conference order appears to require EBTs to be completed by June 30.

Thank you,
[Name]
```

</details>

---

## 12.2 Draft Types

The system should provide different draft styles:

```text
- Short follow-up
- Cooperative scheduling email
- Firm deadline-focused email
- Internal attorney update
- Escalation for missing response
- Clarification request
- Attachment-forwarding email
```

## 12.3 Back-and-Forth Logic

When a new email comes in, the system should:

```text
- Parse new availability
- Update the review table
- Update candidate dates
- Update missing response tracker
- Update risk level
- Suggest next email
- Create follow-up reminder if needed
```

Example:

```text
New OPA email:
“June 18 no longer works. The doctor can do June 24 after 1 PM.”

System update:
- Jun 18 removed as viable
- Jun 24 added as candidate
- Jun 24 marked medium risk because it is close to the deadline
- Draft follow-up asking defense counsel to confirm Jun 24 after 1 PM
```

---

# 13. Suggested Attachments

The system should suggest attachments for each communication.

Examples:

```text
- Compliance conference order
- Preliminary conference order
- Deposition notice
- Prior stipulation
- Witness subpoena
- NYSCEF confirmation
- Prior email thread PDF
- Court reporter confirmation
```

## Example Attachment Suggestion

```text
Suggested attachment:
Compliance Conference Order dated May 12

Reason:
This order appears to contain the current EBT completion deadline of June 30.

Use:
Attach if counsel needs deadline reference or if proposing dates close to the deadline.

Status:
Recommended, not mandatory.
```

---

# 14. Timeline and Audit Logs

The system should keep both a **case timeline** and an **AI audit log**.

## 14.1 Case Timeline

The timeline explains what happened in the matter.

Examples:

```text
- Court entered order
- Plaintiff requested availability
- Defense responded
- OPA responded
- Witness availability received
- Follow-up sent
- Date confirmed
```

## 14.2 AI Audit Log

The audit log explains what the AI did.

Examples:

```text
- Parsed email thread
- Extracted availability
- Checked calendar
- Identified deadline
- Generated table
- Flagged risk
- Drafted email
- Suggested attachment
- Human approved
```

This matters because litigation workflows need accountability.

---

# 15. Human Review and Approval Model

The system must keep humans in control.

## 15.1 Human Approval Required For

```text
- Confirming controlling deadline
- Accepting extracted availability
- Selecting proposed EBT date
- Sending email
- Attaching PDFs
- Proposing date outside deadline
- Escalating to attorney
- Marking task complete
```

## 15.2 AI Should Never

```text
- Send emails automatically without approval
- File documents automatically
- Make final legal judgment
- Hide uncertainty
- Treat judge behavior patterns as law
- Change calendar events without approval
```

## 15.3 Trust Model

```text
AI prepares.
Human reviews.
Attorney decides when risk is high.
System logs everything.
```

---

# 16. MVP Scope

## 16.1 MVP Goal

Build a working **EBT Coordination Copilot** that creates a review packet from emails, court orders, and calendar data.

## 16.2 MVP Inputs

```text
- Email thread upload or paste
- Court order upload
- Deposition notice upload
- Internal attorney calendar data
- Manual party/witness list
- Manual deadline override if needed
```

## 16.3 MVP Outputs

```text
- Availability matrix
- Mutual date candidate table
- Internal calendar conflict table
- Deadline risk table
- Missing response tracker
- Source-backed summary
- Timeline
- Audit log
- Draft follow-up email
- Suggested attachments
```

## 16.4 MVP User Flow

```text
1. User opens case
2. User selects “Coordinate EBT”
3. User uploads email thread and court order
4. User selects assigned attorney calendar
5. AI extracts availability and deadlines
6. AI generates review packet
7. User reviews table
8. User selects candidate date
9. AI drafts email
10. User approves or edits
11. System logs action
12. System creates follow-up reminder
```

---

# 17. Future Scope

After the EBT module works, the broader platform can expand into:

```text
- Full deposition workflow management
- Transcript follow-up tracker
- Subpoena coordination
- Compliance conference order preparation
- Filing packet assembly
- Motion packet support
- PACER / NYSCEF similar-case search
- Firm-wide filing repository
- Medical research layer
- Med-mal issue research
- Personal injury workflow packs
- Landlord/tenant workflow packs
- Family law workflow packs
- E-discovery review layer
- Prior outcome comparison
- Judge / part behavior pattern system
```

These should come later.

The first strong wedge is still:

```text
EBT Coordination Copilot
```

---

# 18. Technical Architecture

## 18.1 Recommended Architecture

```text
Frontend:
Next.js / React

Backend Product Layer:
Node / Next.js API or FastAPI

AI Worker Layer:
Python FastAPI worker

Database:
Postgres

Job Queue:
Postgres jobs table first
Redis / Celery later if needed

Storage:
Document storage for PDFs and email files

AI Layer:
LLM extraction
Structured JSON output
Source citation mapping
Calendar conflict detection
Deadline risk classification
Human review state
```

---

## 18.2 Processing Flow

```text
User uploads email thread / order / calendar info
        ↓
Backend creates EBT coordination job
        ↓
Python AI worker extracts data
        ↓
System stores structured availability
        ↓
System checks calendar conflicts
        ↓
System checks deadline risk
        ↓
System creates review packet
        ↓
Frontend displays tables and summaries
        ↓
User reviews and edits
        ↓
System drafts email and suggests attachments
        ↓
User approves
        ↓
System logs action and sets reminders
```

---

## 18.3 Core Data Objects

<details>
<summary>Case Object</summary>

```json
{
  "case_id": "case_001",
  "case_name": "Doe v. Hospital",
  "court": "Supreme Court",
  "county": "Westchester",
  "judge": "Judge Name",
  "part": "Part 1",
  "assigned_attorney": "Attorney A",
  "assigned_paralegal": "Paralegal B"
}
```

</details>

---

<details>
<summary>EBT Participant Object</summary>

```json
{
  "participant_id": "p_001",
  "name": "Dr. Smith",
  "role": "Witness",
  "party": "Defendant",
  "represented_by": "Defense Counsel A",
  "status": "pending_confirmation"
}
```

</details>

---

<details>
<summary>Availability Entry Object</summary>

```json
{
  "participant_id": "p_001",
  "available_dates": [
    {
      "date": "2026-06-18",
      "start_time": "13:00",
      "end_time": "17:00",
      "confidence": "high",
      "source_id": "email_004"
    }
  ],
  "unavailable_dates": [
    {
      "date": "2026-06-14",
      "reason": "trial conflict",
      "source_id": "email_004"
    }
  ]
}
```

</details>

---

<details>
<summary>Deadline Risk Object</summary>

```json
{
  "deadline_date": "2026-06-30",
  "deadline_type": "court_ordered_ebt_deadline",
  "risk_level": "high_if_missed",
  "source_id": "order_002",
  "requires_attorney_review_if_outside": true
}
```

</details>

---

<details>
<summary>Review Packet Object</summary>

```json
{
  "task_id": "task_001",
  "task_type": "ebt_coordination",
  "case_id": "case_001",
  "best_candidate_dates": ["2026-06-18T14:00:00"],
  "backup_candidate_dates": ["2026-06-24T14:00:00"],
  "deadline_risk": "medium",
  "missing_confirmations": ["OPA", "Court Reporter"],
  "draft_email_id": "draft_001",
  "status": "ready_for_human_review"
}
```

</details>

---

# 19. Success Metrics

## 19.1 Time Savings

Target:

```text
Manual EBT coordination:
1-3 hours

With copilot:
5-15 minutes of review
```

## 19.2 Quality Metrics

Track:

```text
- Number of emails summarized correctly
- Number of availability entries extracted correctly
- Number of calendar conflicts caught
- Number of missed responses identified
- Number of deadline risks flagged
- Number of draft emails accepted with minor edits
- Number of source links opened by user
```

## 19.3 Trust Metrics

Track:

```text
- User confidence in summaries
- User confidence in source navigation
- Attorney review acceptance rate
- Number of AI suggestions corrected by humans
- Number of high-risk actions escalated properly
```

---

# 20. Final Product Pitch

```text
We are building an AI Copilot OS for litigation operations, starting with EBT coordination.

The first module reads email threads, court orders, deposition notices, internal attorney calendars,
OPA availability, witness responses, and court deadlines. It then generates a detailed Excel-style
review packet with availability tables, calendar conflicts, mutual date candidates, deadline risks,
source-backed summaries, timelines, follow-up tasks, draft emails, suggested attachments, and audit logs.

The goal is not to replace paralegals or attorneys.

The goal is to reduce procedural coordination work from hours to minutes while keeping humans in control
and making every AI suggestion reviewable through source-backed navigation.
```

---

# Short Name Options

## First Module

```text
EBT Coordination Copilot
```

## Broader Platform

```text
Litigation Copilot OS
```

## More Formal Product Category

```text
Source-Backed Litigation Workflow Copilot
```

## Strongest Practical Framing

```text
A procedural workflow copilot for litigation teams that prepares review packets,
drafts communications, tracks follow-ups, and keeps every recommendation tied to source documents.
```
