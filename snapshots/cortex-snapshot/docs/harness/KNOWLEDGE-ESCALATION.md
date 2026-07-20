# Cortex knowledge escalation contract

**Audience:** users, driver authors, and evaluators.  
**Status:** normative target with current implementation truth called out explicitly.  
**Core rule:** retrieval depth is selected from observable evidence gaps. A model's confidence is
never a routing signal.

## The route a user should expect

```text
request
  -> canonical Brain recall (docs, accepted/reviewed material, promoted KEDB patterns)
  -> tenant/local recall (project docs, prior closeouts, incident history)
  -> deterministic coverage + freshness + risk decision
       -> sufficient: freeze the research/adoption brief
       -> insufficient: bounded external discovery and source registration
  -> cited local research report with unanswered questions surfaced
  -> freeze success contract
  -> execute
  -> task-matched hidden oracle / independent replay / human review
  -> incident, KEDB, and promotion updates
```

Brain recall, local project recall, internet discovery, and evaluation are different lanes. Internet
research does not replace a known-error lookup. A gold dataset does not replace product research.
Search results do not certify a final artifact.

## Who decides whether research is sufficient

No component may declare that research is universally complete. Cortex may only issue
`SUFFICIENT_FOR_DECISION` for a named decision, scope, risk tier, and timestamp. Research can always be
reopened by a scope change, new contradiction, source staleness, failed assumption, or human request.

The decision uses three authorities:

1. **Cortex policy gate:** checks the frozen decision questions, required source classes, authority,
   independence, freshness, coverage, contradictions, unresolved risks, and capture receipts. This is
   deterministic and may only reject or report that the mechanical floor is met.
2. **Independent domain evaluator:** checks whether the source set and synthesis actually address the
   decision rather than merely satisfying counts. This evaluator is not the builder and is calibrated
   against domain experts.
3. **Human owner/domain expert:** accepts risk, ambiguity, and consequential assumptions. This approval
   is mandatory for high-consequence domains such as legal work and for subjective product direction.

The driver that performed the research never has final sufficiency authority. A low-risk task may
advance after the mechanical floor with later independent outcome evaluation. A medium-risk task needs
independent review. A high-risk legal, medical, financial, security, or safety decision needs the
appropriate human/domain expert.

The stop rule is decision-based, not source-count-based. Before advancing:

- the decisions the research must inform are frozen;
- every material decision question is answered with qualifying evidence or explicitly unresolved;
- load-bearing claims meet authority, independence, and freshness requirements;
- material contradictions are resolved or carried forward as decision risk;
- remaining uncertainty is inside a named, human-approved risk tolerance; and
- the report states “sufficient for decision X as of Y,” never “all relevant research is complete.”

## Required routing gates

| Decision | Deterministic trigger | Required next action | Blocking result if unavailable |
|---|---|---|---|
| Consult Brain | Every non-trivial task, before mutation | Search or build a scope pack using the original request plus named constraints | `UNGOVERNED_RUN` if the execution contract required Cortex and no attributable Brain receipt exists |
| Consult local/tenant history | Existing project; request mentions prior behavior/decision; current error/signature exists; or Brain coverage is insufficient | Search project docs, closeouts, and incident records separately from the canonical Brain | `UNRESOLVED` when required history cannot be inspected |
| Apply promoted KEDB pattern | A retrieved active pattern's detection recipe reproduces the current symptom | Attach the pattern ID, run its detector, and use the fix only when the detector matches | Pattern is advisory when its detector was not run or did not match |
| Record first incident | A real failure is observed even when occurrence count is one | Persist driver/model/runtime, expected and observed behavior, trace, hashes, reproducer, and status | Never discard an n=1 failure merely because it cannot yet become a general pattern |
| Promote incident to KEDB pattern | Same failure class occurs at least twice and a deterministic detection recipe plus evidence links exist | Curated promotion; preserve provenance and last verification | Remain an incident; do not generalize from one example |
| Start external research | Any material sub-question has no evidence; required corroboration is missing; a required source is stale; named technology/version is absent; or an adoption/UX decision lacks its required evidence mix | Use a web-capable driver, register accepted candidate sources, then rerun Cortex research against fetched local copies | `ENVIRONMENT_UNAVAILABLE` for tool/provider failure, or `UNRESOLVED` for evidence gaps; never claim researched |
| Freeze research/adoption brief | Every material sub-question is answered or explicitly deferred; source mix and freshness policy pass; contradictions and assumptions are visible | Freeze the report/decision digest before implementation | Implementation may continue only under the execution contract's named advisory fallback |
| Select oracle/gold lane | The frozen success contract maps an acceptance criterion to a registered objective checker, hidden holdout, external observer, or human boundary | Keep evaluator fixtures hidden from the builder and bind the verdict to the final artifact hash | `ABSTAIN`/`UNRESOLVED` when no valid authority can decide the criterion |

## Source requirements are claim- and risk-based

There is no honest universal "minimum number of links." The execution contract freezes a source
policy appropriate to the task. The default floor for a substantive product/build task is:

- each material claim has at least one primary or authoritative source where one exists;
- a contested or load-bearing claim has at least two independent sources;
- a third-party adoption decision examines official documentation, current release/maintenance
  activity, license, integration/exit cost, and security posture, plus an independent production or
  user signal where available;
- a non-trivial UX/branding task uses applicable standards or primary research, directly inspects
  two or three relevant comparable products, records target-user assumptions, and reserves subjective
  identity/quality acceptance for the human owner;
- security, legal, financial, medical, and rapidly changing claims use a stricter policy named in the
  contract, including freshness limits and authoritative sources.

Source diversity and coverage matter more than accumulating low-quality URLs. Search-result snippets,
AI summaries, duplicate syndications, and multiple pages controlled by one vendor are not independent
corroboration.

For software adoption, NIST SSDF supplies the secure-development and third-party-component baseline;
OpenSSF Scorecard can provide reproducible security-health signals but is not a complete accept/reject
oracle. For web UX, WCAG 2.2 supplies testable accessibility criteria; Google's HEART paper provides a
reusable goals-signals-metrics method. These are examples of evidence lanes, not mandatory choices for
every task.

## What must be recorded

One run-correlated research record must contain:

1. original question and decomposed sub-questions;
2. Brain and tenant queries, hits, and corpus paths actually read;
3. coverage, corroboration, freshness, contradictions, and unanswered questions;
4. external search queries and provider/tool outcomes, including failures and retry/failover decisions;
5. every accepted source's URL, type, authority/independence group, discovery method, capture time,
   fetched corpus path, and content hash;
6. an adopt/adapt/build comparison with rejected candidates and reasons;
7. frozen research brief/report hash and the success-contract criteria it informed; and
8. any human decision, advisory fallback, or non-pass status.

Repeating the same failed search backend does not count as independent research. When discovery fails,
the driver must fail over to a permitted independent backend or surface `ENVIRONMENT_UNAVAILABLE`.

## Persistent local knowledge lifecycle

External discovery must leave Cortex more useful without poisoning the canonical Brain:

1. search results enter a tenant-side candidate quarantine with query, discovery tool, source owner,
   URL, source class, authority, independence group, and retrieval time;
2. Cortex fetches a permissible snapshot, computes its hash, records version/freshness/licensing and
   scan results, and stores it in the tenant corpus. If full capture is prohibited or impractical,
   store metadata, a lawful bounded extract, citation locator, and content/version fingerprint instead;
3. fetched material is scanned for malformed content and prompt-injection/corpus-poisoning indicators,
   then indexed only for the tenant;
4. research cites the local snapshot or locator and records which decisions/claims it supports;
5. reviewed, reusable, non-confidential material may be proposed for canonical Brain promotion with
   provenance, deduplication, license, freshness, and independent-review gates; and
6. matter/client-confidential material never leaves the tenant boundary and never becomes shared gold
   or training data.

“Download everything” is therefore bounded by relevance, rights, confidentiality, safety, and storage
policy. The desired property is durable, re-verifiable local evidence--not indiscriminate copying.

## Current implementation truth (2026-07-15)

Built today:

- `cortex_search` and `cortex_scope_pack` perform bounded composite retrieval across the canonical
  Brain and tenant corpus, KEDB incident metadata, promoted patterns, public gold-catalog metadata,
  and public oracle metadata. Results identify plane/store and expose per-store coverage; hidden
  evaluator rows are never searched.
- promoted patterns require occurrence count >=2, a detection recipe, and resolving evidence links;
  structured `kedb/incidents/` now preserves first occurrences.
- `cortex_research` persists async task state, fetches registered sources into the tenant workspace,
  reports coverage/corroboration/unanswered questions, surfaces `needs_sources`, and persists a cited
  report plus a faithfulness check.
- `cortex_research(action="register_source")` lets an owner/admin persist a web-discovered candidate
  into the tenant source registry before rerunning research.
- accepted external fetches retain URL, local snapshot path, and SHA-256 identity; research searches
  Brain plus tenant evidence and rebuilds the tenant index after persisting its report.
- the research-sufficiency core freezes report/evidence snapshots against a trusted registered
  policy, applies decision/lane/authority/independence/freshness/conflict rules, consumes opaque
  independent-review and human attestations, and stores immutable `SUFFICIENT_FOR_DECISION`,
  `UNRESOLVED`, or `ABSTAIN` receipts. Policies, source authority/ownership classifications,
  evaluator reviews, and human approvals require Ed25519 envelopes from an operator-owned public
  trust root; Cortex holds no evaluator/human private key.
- `assured_build` and `assured_research` bind receipt lookup into immutable state-machine chart
  topology. The builder-facing MCP can propose/finalize/reference receipts but cannot register a
  policy or mint an independent/human attestation.
- the state engine can abstain when an automatic review has neither a deterministic oracle nor a
  human authority.

Not enforced today:

- scope packs do not compute the documented coverage, freshness, task-type, risk, or depth verdict;
- deep research does not discover the open web itself; the driver must use a separate web tool;
- driver web calls are not automatically joined to the Cortex research task record;
- source trust tier is not weighted in corroboration, and no source-type/diversity/adoption gate is
  enforced;
- the asymmetric verification boundary is built, but production signer services, OS ACLs for the
  trust-root/receipt database, key rotation/revocation operations, and Hermes integration remain open;
- source registration writes candidates directly to the tenant registry; a quarantine, licensing,
  prompt-injection scan, and reviewed Brain-promotion route are not yet a closed pipeline;
- gold datasets and registered oracles are not automatically selected by `scope_pack`; the proposed
  `cortex_evaluate` router remains unbuilt;
- legacy `build`/`research` tracks still require only meaningful non-empty evidence and remain for
  compatibility; only the explicit `assured_build`/`assured_research` routes enforce the receipt.

Therefore the current live behavior is a collection of useful mechanisms, not yet a closed escalation
policy. A driver can still skip the intended route unless an external execution contract and observer
require the receipts listed here.
