# Litigation AI Copilot Platform Scope

## Focus

A modular litigation workflow copilot platform built one mini-tool at a time, starting with the **EBT Coordination Copilot** because it is the easiest to test through Outlook emails and calendars.

## Table of Contents

1. [Project Thesis](#1-project-thesis)
2. [Build Strategy](#2-build-strategy)
3. [Product Positioning](#3-product-positioning)
4. [Modular Platform Map](#4-modular-platform-map)
5. [First Product Wedge](#5-first-product-wedge)
6. [What the Platform Does](#6-what-the-platform-does)
7. [What the Platform Does Not Do](#7-what-the-platform-does-not-do)
8. [Core Product Modules](#8-core-product-modules)
9. [Core Engineering Layers](#9-core-engineering-layers)
10. [Mini-Agent Architecture](#10-mini-agent-architecture)
11. [Trust Architecture](#11-trust-architecture)
12. [Security and Confidentiality Architecture](#12-security-and-confidentiality-architecture)
13. [AI Architecture](#13-ai-architecture)
14. [Context Engineering Strategy](#14-context-engineering-strategy)
15. [Smart Memory Layer](#15-smart-memory-layer)
16. [Deployment Tiers](#16-deployment-tiers)
17. [Self-Contained / Air-Gapped Architecture](#17-self-contained--air-gapped-architecture)
18. [Cost Architecture](#18-cost-architecture)
19. [Human Review and UPL Safeguards](#19-human-review-and-upl-safeguards)
20. [MVP Phases](#20-mvp-phases)
21. [Technical Architecture](#21-technical-architecture)
22. [Future Modules](#22-future-modules)
23. [Success Metrics](#23-success-metrics)
24. [Final Platform Pitch](#24-final-platform-pitch)

---

# 1. Project Thesis

The platform is a **Litigation AI Copilot OS** for procedural litigation operations.

The product should not start as one giant legal AI platform. It should start as a set of small, testable, useful workflow tools.

The first tool is the **EBT Coordination Copilot**, because EBT coordination is repetitive, Outlook-heavy, calendar-heavy, and easy to validate with real or synthetic email threads.

The broader thesis:

```text
Legal teams do not only need AI answers.
They need source-backed procedural review packets, timelines, tables,
task status, confidence flags, and human approval checkpoints.
```

The product should grow like this:

```text
EBT Review Packet Generator
↓
EBT Coordination Copilot
↓
Email / Drafting / Filing / Workflow Assistants
↓
Litigation Copilot OS
```

---

# 2. Build Strategy

The build strategy is:

```text
One working mini-tool at a time.
```

The first mini-tool should be narrow enough to test quickly:

```text
Input:
Outlook email thread + attorney calendar + court order

Output:
EBT coordination review packet
```

This avoids overbuilding the full litigation OS before proving one real workflow.

## Why EBT First

EBT coordination is a good first wedge because it is:

```text
- Common
- Repetitive
- Email-heavy
- Calendar-heavy
- Spreadsheet-heavy
- Procedural
- Easy to test
- Easy to explain
- Useful without making legal decisions
```

The first goal is not to automate legal work.

The first goal is:

```text
Reduce manual EBT coordination review from hours to minutes.
```

---

# 3. Product Positioning

## Product Category

```text
AI Copilot OS for Litigation Operations
```

## First Product Wedge

```text
Shadow-Mode EBT Review Packet Generator
```

## Practical Positioning

```text
A source-backed litigation workflow assistant that prepares review packets,
summaries, timelines, tables, and follow-up status for procedural legal work.
```

## First Pitch

```text
The first tool reads Outlook email threads, attorney calendars, and court orders,
then creates an Excel-style EBT review packet showing availability, calendar conflicts,
possible deadline risks, missing responses, sources, and timeline logs.
```

---

# 4. Modular Platform Map

The platform should be designed as modular product sections plus reusable engineering layers.

## 4.1 Product Modules

```text
Litigation Copilot OS
├── Email Intelligence Module
├── E-Filing Support Module
├── E-Drafting Module
├── Summarizer Module
├── Coordinator Module
├── Workflow Assistant Module
├── Human-in-the-Loop Review Module
├── Timeline and Logs Module
├── Source Navigation Module
├── Review Packet Generator
└── Research / Knowledge Module
```

## 4.2 Engineering Layers

```text
Engineering Foundation
├── Context Engineering Layer
├── Smart Memory Layer
├── API Contract Layer
├── Validation Layer
├── Extraction Confidence Layer
├── Source Anchoring Layer
├── Human Review Flagging Layer
├── Audit Log Layer
├── Security / RBAC Layer
├── Data Retention Layer
├── Deployment Router
└── Cost Control Layer
```

---

# 5. First Product Wedge

The first wedge is:

```text
Shadow-Mode EBT Review Packet Generator
```

It does not start by sending emails, filing documents, or controlling calendars.

It starts by producing a review packet from:

```text
- Outlook email thread
- Court order
- Internal attorney calendar data
- OPA / defense / witness availability
```

The packet includes:

```text
- Availability tables
- Calendar conflict checks
- Missing response tracker
- Possible deadline flags
- Source-backed summary
- Communication timeline
- Audit log
- Human correction fields
```

---

# 6. What the Platform Does

The platform helps answer:

```text
What task is active?
What documents matter?
What has already happened?
What remains?
Who needs to respond?
Which source supports this?
Is the deadline extracted or human-confirmed?
What is missing?
What is risky?
What needs attorney review?
What can be prepared for human approval?
```

The platform prepares:

```text
- Review packets
- Source-backed summaries
- Timelines
- Task checklists
- Availability tables
- Draft emails
- Draft filing support materials
- Suggested attachments
- Follow-up reminders
- Audit logs
```

---

# 7. What the Platform Does Not Do

The platform does not:

```text
- Practice law
- Give legal advice
- Make final legal decisions
- Send emails automatically
- File documents automatically
- Replace attorney review
- Replace paralegal judgment
- Guarantee legal deadline correctness
- Interpret substantive legal rights
```

The system only:

```text
- Extracts procedural information
- Surfaces possible deadlines
- Shows source references
- Flags risk
- Prepares reviewable work products
- Helps humans review faster
```

---

# 8. Core Product Modules

## 8.1 Email Intelligence Module

Purpose:

```text
Read, summarize, organize, and extract structured data from litigation email threads.
```

Responsibilities:

```text
- Identify parties and senders
- Extract availability
- Extract requested actions
- Detect missing responses
- Detect contradictions
- Summarize long email threads
- Identify attachments referenced in emails
- Generate follow-up status
```

---

## 8.2 E-Filing Support Module

Purpose:

```text
Help users prepare and review filing-related procedural packets.
```

Responsibilities:

```text
- Identify required documents for a filing step
- Group source documents
- Track missing filing materials
- Summarize filing status
- Generate filing checklist
- Surface prior related filings
```

This module should not auto-file.

---

## 8.3 E-Drafting Module

Purpose:

```text
Draft procedural communications and routine legal operations text for human review.
```

Responsibilities:

```text
- Draft OPA emails
- Draft defense counsel follow-ups
- Draft internal attorney summaries
- Draft court clerk communication
- Draft scheduling confirmations
```

Safeguards:

```text
- Never send automatically
- Mark legal-position language for attorney review
- Show source basis for claims
- Require human approval
```

---

## 8.4 Summarizer Module

Purpose:

```text
Create source-backed summaries of documents, email threads, case events, and task status.
```

Outputs:

```text
- Short summary
- Detailed summary
- Source map
- Confidence flags
```

---

## 8.5 Coordinator Module

Purpose:

```text
Handle multi-party coordination workflows.
```

Initial use case:

```text
EBT coordination
```

Future use cases:

```text
- Deposition scheduling
- Court reporter coordination
- Expert scheduling
- Subpoena follow-up
- Transcript follow-up
- Conference order coordination
```

---

## 8.6 Workflow Assistant Module

Purpose:

```text
Guide users through procedural tasks step by step.
```

Responsibilities:

```text
- Identify current task
- Show required steps
- Track completed steps
- Show remaining steps
- Create follow-up reminders
- Escalate risky items
```

---

## 8.7 Human-in-the-Loop Review Module

Purpose:

```text
Make human review the center of the product.
```

Responsibilities:

```text
- Show extracted item
- Show source
- Show confidence
- Ask for confirmation
- Allow correction
- Log correction
- Require attorney review where appropriate
```

---

## 8.8 Timeline and Logs Module

Purpose:

```text
Maintain a procedural timeline and system audit trail.
```

Tracks:

```text
- Court orders
- Email requests
- OPA responses
- Defense responses
- Witness updates
- Follow-ups
- Confirmations
- AI extraction events
- User corrections
- Packet exports
```

---

## 8.9 Source Navigation Module

Purpose:

```text
Let users quickly open the exact source behind every AI output.
```

Supports:

```text
- Document page reference
- Email excerpt
- Calendar event
- Attachment reference
- Source reliability tier
```

---

# 9. Core Engineering Layers

## 9.1 Context Engineering Layer

Controls what information enters each model call.

Responsibilities:

```text
- Select relevant documents
- Limit context size
- Chunk long documents
- Retrieve source-specific chunks
- Avoid silent truncation
- Keep task-specific context bounded
```

---

## 9.2 Smart Memory Layer

Stores confirmed, reusable procedural facts.

Examples:

```text
- Human-confirmed EBT deadline
- Confirmed attorney availability
- Confirmed witness availability
- Corrected OPA response
- Case task state
- Prior review packet result
```

Memory statuses:

```text
- AI-extracted
- Human-confirmed
- Attorney-approved
- Stale
- Contradicted
- Superseded
```

---

## 9.3 API Contract Layer

Defines strict contracts between modules.

Example:

```json
{
  "task_id": "task_001",
  "case_id": "case_001",
  "module": "ebt_coordination",
  "input_documents": ["email_001", "order_001"],
  "required_outputs": ["availability_matrix", "deadline_risk_table"]
}
```

---

## 9.4 Validation Layer

Checks model outputs before they reach the user.

Responsibilities:

```text
- Validate JSON schema
- Check required fields
- Check date formats
- Detect missing source IDs
- Detect impossible dates
- Detect unsupported claims
```

---

## 9.5 Extraction Confidence Layer

Every extracted item includes:

```text
- Value
- Source
- Confidence
- Reason
- Human status
```

---

## 9.6 Source Anchoring Layer

Every claim maps to a source.

Source types:

```text
Primary:
Court order, official docket, filed stipulation.

Secondary:
Email from counsel, OPA, vendor, attorney.

Tertiary:
Internal note, voicemail summary, informal reminder.
```

---

## 9.7 Human Review Flagging Layer

Flags items that need human or attorney review.

Examples:

```text
- Low-confidence extraction
- Contradictory sources
- Date outside possible deadline
- Unclear calendar conflict
- Legal-position draft language
- Missing source
```

---

## 9.8 Cost Control Layer

Controls API and compute cost.

Mechanisms:

```text
- Token budget per packet
- Page limit per upload
- Caching processed files
- Reusing confirmed facts
- Smaller model for simple extraction
- Stronger model for ambiguous items
- No full-case reprocessing unless needed
```

---

# 10. Mini-Agent Architecture

The platform can use mini-agents, but each should be bounded and schema-driven.

## 10.1 Mini-Agent Types

```text
Email Extraction Agent
Calendar Conflict Agent
Deadline Extraction Agent
Source Citation Agent
Confidence Scoring Agent
Contradiction Detection Agent
Review Packet Agent
Drafting Agent
Attachment Suggestion Agent
Timeline Agent
Audit Log Agent
Human Review Router
```

## 10.2 Agent Rule

Each mini-agent should have:

```text
- Narrow input
- Narrow output
- JSON schema
- Source requirement
- Validation step
- Human review fallback
```

Avoid:

```text
One giant agent reads everything and decides everything.
```

Prefer:

```text
Small extraction agents produce structured outputs.
Validation layer checks them.
Review packet agent assembles the final packet.
Human confirms uncertain items.
```

---

# 11. Trust Architecture

Trust comes from:

```text
- Source links
- Confidence scores
- Human correction
- Audit logs
- Clear uncertainty
- No auto-send
- No auto-file
- No hidden model decisions
```

Every important output should answer:

```text
What did the AI extract?
Where did it come from?
How confident is it?
Has a human confirmed it?
Does it need attorney review?
```

---

# 12. Security and Confidentiality Architecture

Legal workflows involve:

```text
- Client confidential information
- Attorney-client privileged material
- Work product
- Medical records
- HIPAA-sensitive material in med-mal cases
- Internal litigation strategy
```

Required principles:

```text
- Read-only access first
- Least-privilege permissions
- Encryption in transit
- Encryption at rest
- Firm-level tenant separation
- Case-level permissions
- Role-based access control
- Configurable retention
- No training on firm data without explicit agreement
- Data access audit logs
```

Retention options:

```text
- Delete source files after export
- Retain structured packet only
- Retain audit logs for firm-defined period
- Delete case-level data
- Delete firm-level data
```

---

# 13. AI Architecture

The AI layer has four stages:

```text
1. Extraction Layer
   Pull dates, names, parties, availability, deadlines, conditions.

2. Verification Layer
   Attach source references, confidence scores, contradiction flags.

3. Review Packet Layer
   Generate tables, timelines, logs, summaries, exports.

4. Human Correction Layer
   Let users confirm, fix, override, and log corrections.
```

---

# 14. Context Engineering Strategy

Long-context models help, but they do not replace context engineering.

The system may use models with:

```text
- 128K context
- 200K context
- 1M+ context
```

But legal workflows still require:

```text
- Source-first retrieval
- Structured chunking
- Metadata filtering
- Human-confirmed extraction
- Contradiction detection
- Stale document handling
```

## Phase Strategy

```text
MVP-1:
Direct context loading for a bounded EBT packet.

MVP-2:
Direct context + confirmed packet facts + templates.

MVP-3:
RAG + metadata filters + structured case state.

MVP-4:
Full RAG + source ranking + external research connectors.
```

Rules:

```text
Do not silently truncate.
Do not process the whole case file for a narrow task.
Do not assume long context means reliable recall.
Prefer source-specific retrieval for deadline questions.
```

---

# 15. Smart Memory Layer

Memory should distinguish:

```text
AI-extracted
Human-confirmed
Attorney-approved
Stale
Contradicted
Superseded
```

Example:

```text
AI-extracted:
OPA may be available June 18.

Human-confirmed:
OPA confirmed June 18 after 1 PM.

Attorney-approved:
June 18 at 2 PM approved as proposed EBT date.

Stale:
June 14 was proposed but later rejected.

Contradicted:
OPA first said June 18 was available, then later said unavailable.
```

---

# 16. Deployment Tiers

## Tier 1: Cloud API MVP

```text
Use:
Demo, early pilot, small firms with approved cloud use.

Benefits:
Fastest setup, strong extraction quality.

Requirements:
Clear privacy policy, subprocessors, no-training commitment, retention controls.
```

## Tier 2: Private Cloud

```text
Use:
Firms that require approved cloud boundaries.

Examples:
Azure OpenAI, AWS Bedrock, firm-controlled cloud environment.
```

## Tier 3: Self-Hosted / VPC

```text
Use:
Security-sensitive firms, med-mal, PI, enterprise legal departments.

Stack:
Firm-controlled app, database, storage, OCR, vector search, and LLM inference.
```

## Tier 4: Hybrid

```text
Use:
Most realistic long-term option.

Pattern:
Sensitive extraction local.
Non-sensitive formatting or table generation cloud.
Router decides based on document classification and firm policy.
```

---

# 17. Self-Contained / Air-Gapped Architecture

A fully self-contained deployment should include:

```text
Frontend:
Next.js served locally.

Backend:
FastAPI or Node API inside firm network.

Database:
Postgres with pgvector.

Document Storage:
MinIO or local encrypted storage.

OCR:
Local OCR / document parsing.

LLM Inference:
Local vLLM or similar inference server.

Exports:
Local Excel / CSV / PDF generation.

Network:
No outbound internet required after installation.
No external telemetry.
No external logging.
No external API calls unless enabled by firm.
```

---

# 18. Cost Architecture

Cost must be designed, not guessed.

Cost controls:

```text
- Token budget per packet
- Page limit per upload
- Cache processed documents
- Reuse confirmed facts
- Use smaller models for simple extraction
- Use stronger models for ambiguous or high-risk items
- Avoid full-case reprocessing
- Batch low-priority jobs
```

Cost rule:

```text
Do not send the whole case file to the model every time.
Process once, store extracted facts, source anchors, and human confirmations.
```

---

# 19. Human Review and UPL Safeguards

Use safe language:

```text
Possible deadline found.
Source indicates.
Human confirmation required.
Attorney review recommended.
```

Avoid:

```text
This is the legal deadline.
This satisfies the order.
This is legally compliant.
You are allowed to adjourn.
```

Attorney review required for:

```text
- Date outside possible deadline
- Ambiguous order language
- Conflicting sources
- Drafts with legal-position language
- Adjournment requests
- High-risk procedural issues
```

---

# 20. MVP Phases

## MVP-1: Shadow-Mode EBT Review Packet Generator

Features:

```text
- Upload email thread
- Upload court order
- Manual or read-only calendar input
- Extract availability
- Extract possible deadline
- Detect conflicts
- Add confidence scores
- Human correction UI
- Generate tables
- Export Excel/CSV/PDF
- Audit log
```

No:

```text
- Auto-send
- Auto-calendar write
- Auto-file
- Full case management integration
```

## MVP-2: EBT Coordination Copilot

Add:

```text
- Draft follow-up emails
- Suggested attachments
- Reminder tracker
- Timeline updates
- Outlook read-only integration
```

## MVP-3: Outlook-Connected Litigation Workflow Copilot

Add:

```text
- Select email thread from Outlook
- Select attorney calendar
- Save packet to case workspace
- Create draft email with approval
- Optional tentative calendar hold with approval
```

## MVP-4: Litigation Copilot OS

Add:

```text
- E-filing support
- Drafting workflows
- Case workspace
- Prior filing repository
- Research layer
- Broader procedural workflow modules
```

---

# 21. Technical Architecture

Recommended architecture:

```text
Frontend:
Next.js / React

Backend Product Layer:
Node / Next.js API or FastAPI

AI Worker:
Python FastAPI worker

Database:
Postgres

Vector Search:
pgvector

Storage:
Encrypted object storage or MinIO

Job Processing:
Postgres jobs table first
Redis/Celery later if needed

Exports:
Excel / CSV / PDF generation
```

Processing flow:

```text
User uploads email/order/calendar data
        ↓
Backend creates processing job
        ↓
AI worker extracts data
        ↓
Validation layer checks schema and sources
        ↓
Confidence layer labels uncertain items
        ↓
Review packet generator builds tables
        ↓
Human reviews and corrects
        ↓
System exports packet
        ↓
System logs all actions
```

---

# 22. Future Modules

Future modules:

```text
- EBT coordination
- Transcript follow-up
- Subpoena tracking
- Compliance conference order preparation
- Filing packet assembly
- Motion packet support
- OPA communication assistant
- Court clerk communication assistant
- Firm-wide knowledge repository
- PACER/NYSCEF similar-case search
- Medical research layer
- Personal injury workflow packs
- Landlord/tenant workflow packs
- Family law workflow packs
```

---

# 23. Success Metrics

## Time Savings

```text
Manual EBT coordination:
1-3 hours

With copilot:
5-15 minutes review time
```

## Extraction Quality

```text
- Availability extraction accuracy
- Deadline extraction accuracy
- Calendar conflict accuracy
- Source citation accuracy
- Correction rate
- Low-confidence extraction rate
```

## Trust Metrics

```text
- % of AI outputs human-confirmed without correction
- Number of source links opened
- Attorney acceptance rate
- Number of high-risk items escalated correctly
```

## Adoption Metrics

```text
- Number of packets generated
- Repeat use by paralegals
- % of EBT tasks using copilot
- Exports per week
- Time saved per case
```

---

# 24. Final Platform Pitch

```text
We are building a modular, source-backed AI copilot for litigation operations.

The platform starts with one working mini-tool: an EBT coordination review packet generator.
It reads Outlook email threads, court orders, and attorney calendar data, then generates
an Excel-style review packet showing availability, conflicts, possible deadline risks,
missing responses, source-backed summaries, communication timelines, and audit logs.

It does not send emails, file documents, or make legal decisions.

The plan is to prove one small workflow first, then expand into drafting, e-filing support,
summarization, workflow assistance, and broader litigation operations modules.
```
