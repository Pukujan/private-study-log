# Worked escalation example: litigation CaseOS with an attorney copilot

**Example user request:** “I want a CaseOS for my litigation workflow pipeline. Find production tools
that follow this pattern and an AI copilot that helps attorneys perform the work.”

This is a high-consequence, novel-domain request. A generic Cortex hit or a model's knowledge cannot
unlock implementation. The required result of the first phase is a decision-ready research corpus and
brief, not an application.

## Forced research decision

External discovery is mandatory when the Brain has no current, authoritative coverage of the named
jurisdiction, litigation lifecycle, professional obligations, existing systems, AI capabilities, and
integration/security constraints. The expected initial verdict is `WEB_DISCOVERY_REQUIRED`, not a
guess about what “CaseOS” means.

Before searching, freeze questions in these lanes:

1. **People and authority:** attorneys, paralegals, litigation support, clients, outside parties,
   administrators, reviewers, and who may approve AI-assisted work.
2. **Matter lifecycle:** intake/conflicts, pleadings, deadlines/calendaring, discovery, evidence/facts,
   research, motions, depositions, settlement, trial, appeal, close/retention.
3. **Records and invariants:** matter, party, issue, fact, source/evidence, document/version, task,
   deadline/rule source, communication, legal authority/treatment, work product, decision, audit event,
   confidentiality/privilege and ethical wall.
4. **Production systems:** practice/case management, document management, court-rule calendaring,
   e-discovery/fact management, legal research/citation validation, billing/client portal, APIs and
   identity/governance.
5. **Attorney copilot:** source-grounded research, record/document review, chronology/fact extraction,
   drafting, citation verification, discovery/deposition workflows, explicit uncertainty, approval and
   audit.
6. **Risk and adoption:** jurisdiction, confidentiality, privilege, retention/legal hold, data use,
   model/vendor controls, human supervision, security attestations, integrations, export/exit, cost and
   implementation burden.

## Initial external source spine

These are candidates to capture and compare, not a recommendation or proof of completeness:

- [ABA Formal Opinion 512](https://www.americanbar.org/content/dam/aba/administrative/professional_responsibility/ethics-opinions/aba-formal-opinion-512.pdf): competence, confidentiality, communication, supervision, candor, meritorious claims, and fees for generative-AI use.
- [Clio automated workflows](https://help.clio.com/hc/en-150/articles/35132279298843-Clio-Manage-Automated-Workflows) and [developer documentation](https://docs.developers.clio.com/): matter-stage automation, tasks/templates, and integration surface.
- [Filevine case management](https://www.filevine.com/platform/case-management-software/) and [workflow software](https://www.filevine.com/platform/case-management-software/legal-workflow-software/): configurable matter workflows, documents, tasks, and communications.
- [MyCase workflow automation](https://www.mycase.com/features/workflow-automation/): triggers, templates, tasks/events/documents, and rules-based calendaring.
- [Everlaw fact management](https://www.everlaw.com/blog/legal-technology/introducing-fact-management/): connection between discovery evidence, facts, chronology, and case strategy.
- [Thomson Reuters CoCounsel](https://www.thomsonreuters.com/en-us/help/cocounsel/legal/get-started/how-it-works) and [CoCounsel Legal](https://www.thomsonreuters.com/en/press-releases/2025/august/thomson-reuters-launches-cocounsel-legal-transforming-legal-work-with-agentic-ai-and-deep-research): skills, sources, guided litigation workflows, and source-grounded legal research.
- [LexisNexis Protégé workflows](https://www.lexisnexis.com/community/pressroom/b/news/posts/lexisnexis-unveils-global-launch-of-protege-ai-workflows-for-legal-professionals): private authoritative workspace and end-to-end civil-litigation analysis workflow.
- [Harvey legal AI workflows](https://www.harvey.ai/blog/top-harvey-use-cases): research, document review, drafting, case management, workflow agents, and knowledge/vault surfaces.

Vendor pages establish claimed behavior, not independent production quality. The research plan must
also obtain current security/privacy documentation, API/export limits, contracts/licensing, pricing,
release/support posture, appropriate independent user or implementation evidence, and jurisdiction-
specific authority before an adoption decision.

## Local Cortex synchronization

For each accepted candidate, the driver must call the tenant-side registration route, fetch a
permissible snapshot or durable locator, record metadata/hash/freshness/rights, scan it, and reindex the
tenant corpus. Research is then rerun against those local records. The final report cites local snapshot
paths and maps sources to the frozen decision questions.

Confidential client/matter records, licensed legal databases, and restricted vendor documentation need
special handling: Cortex may store authorized local references or bounded extracts but must not copy or
promote material beyond the user's rights. Nothing client-confidential is promoted to the shared Brain.

## Who may unlock design

- Cortex policy may confirm the mechanical evidence floor.
- An independent legal-domain evaluator checks the workflow/adoption synthesis.
- The attorney owner approves jurisdiction, professional-responsibility boundaries, privilege and
  confidentiality handling, deadline authority, AI review/approval points, and acceptable residual
  risk.

Only then can the system emit `SUFFICIENT_FOR_DECISION: freeze CaseOS product brief`. It is not a claim
that no more research exists; it is permission to make the named design decision using the captured
evidence and recorded uncertainty.

