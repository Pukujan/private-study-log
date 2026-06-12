# Study Log: Hermes Isolation, Tenant Safety, and Shared Runtime Risk

**Date:** June 11, 2026  
**Topic:** How Hades OS should keep user data isolated when multiple people share the same Hermes service, the same OpenRouter provider, or the same local model server.  
**Repo:** `hades-os-monorepo`  
**Focus:** database isolation, request isolation, prompt-injection resistance, auth, and multi-user safety.

## Table of Contents

- [1. Question Being Studied](#1-question-being-studied)
- [2. The Main Design Principle](#2-the-main-design-principle)
- [3. Scale, Isolation, and Shared Infrastructure](#3-scale-isolation-and-shared-infrastructure)
  - [3.1 Backend-Owned Boundary](#31-backend-owned-boundary)
  - [3.2 Fast Memory vs Durable Memory](#32-fast-memory-vs-durable-memory)
  - [3.3 Load Balancing vs Job Queueing](#33-load-balancing-vs-job-queueing)
  - [3.4 Multi-User Hermes Safety](#34-multi-user-hermes-safety)
  - [3.5 Prompt Injection and Secret Hygiene](#35-prompt-injection-and-secret-hygiene)
- [4. Recommended Data Separation](#4-recommended-data-separation)
- [5. How Hermes Should Interact With Memory](#5-how-hermes-should-interact-with-memory)
- [6. Authentication Model](#6-authentication-model)
- [7. Encryption And Storage](#7-encryption-and-storage)
- [8. Risk / Tradeoff Summary](#8-risk--tradeoff-summary)
- [9. Recommended MVP Path](#9-recommended-mvp-path)
- [10. MVP Security Flow](#10-mvp-security-flow)
- [11. Final Answer To The Core Question](#11-final-answer-to-the-core-question)

## Question Being Studied

The core question is:

How do we let 2, 3, 10, or 20 people use the same Hermes-backed system at the same time without mixing data, leaking private context, or letting one user's prompt injection affect another user's memory or output?

The short answer is:

- the backend must own tenant boundaries
- Hermes must be treated as a stateless execution layer
- memory must be stored and fetched per user or per tenant
- OpenRouter or a local model server must never see unscoped private data

## The Main Design Principle

Hermes should not be the source of truth for user data.

Instead:

- the app backend owns identity
- the app backend owns isolation
- the app backend owns memory retrieval
- Hermes only receives a small, scoped prompt for one request

That means shared infrastructure is acceptable as long as each request is isolated before it reaches Hermes.

## 3. Scale, Isolation, and Shared Infrastructure

The big operational question is not whether Hermes can run on shared servers.

It can.

The real question is how the system keeps one user's data separate from another user's data when:

- many users are active at once
- some requests are offline-queued
- some requests are retried
- some jobs are batched
- Hermes runs on one shared provider or one shared local server

The answer is to separate **ownership**, **retrieval**, and **execution**.

### 3.1 Backend-Owned Boundary

The backend must own the trust boundary.

That means:

- authenticate the user first
- resolve tenant and user identity
- load only that tenant's data
- build one scoped prompt
- call Hermes with that scoped prompt
- save the response back under the same tenant

Hermes should never be the place where tenant separation is decided.

If the backend does the scoping correctly, the same Hermes service can serve many users safely because each call is already isolated before it leaves the backend.

### 3.2 Fast Memory vs Durable Memory

We should not hit the database for every single repeated prompt if we can avoid it.

The better design is:

- **durable memory** in the database for truth, history, summaries, and audit
- **fast memory** in cache for recent scoped context and low-latency reuse

The backend should check fast memory first.

If there is no cache hit, the backend reads the tenant-scoped records from durable storage, compacts them, and then refreshes the cache.

That gives us:

- speed
- isolation
- recovery
- auditability

It also means Hermes stays stateless instead of carrying cross-user state in its own process.

### 3.3 Load Balancing vs Job Queueing

This is where the design splits into two different scaling tools:

- **load balancing** spreads incoming requests across multiple backend instances
- **job queueing** stores work that can be processed later by workers

They solve different problems.

Load balancing helps when many users are actively talking to the app.

Job queueing helps when a task should keep running even if the user disconnects or the server is busy.

For Hades OS, both are useful:

- load balancer in front of the backend API
- durable job queue behind the API
- worker pool processing queued Hermes jobs

That way:

- traffic is spread across servers
- work is not lost if a user drops offline
- overflow jobs wait safely in queue

### 3.4 Multi-User Hermes Safety

If 10 or 20 users hit the same Hermes service at once, the service itself is not the thing that must keep them separated.

The backend must do that before the request ever reaches Hermes.

Safe multi-user behavior requires:

- scoped prompt construction per request
- tenant-scoped cache keys
- tenant-scoped database reads
- no shared global memory injection
- request IDs and session IDs bound to one user or tenant
- validated response schemas before storage

That is how one Hermes runtime can stay shared while the data remains private.

### 3.5 Prompt Injection and Secret Hygiene

Prompt injection is a separate risk from tenant mixing, and both have to be handled.

The rule is simple:

- treat all user content as untrusted input
- keep policy/system instructions separate
- never let user-supplied text override backend rules
- never inject secrets unless absolutely required
- redact sensitive fields before sending to Hermes
- validate output against a strict allowlist schema

If a prompt tries to trick Hermes into revealing another user's memory, that should fail because the backend never placed that other user's memory into the prompt in the first place.

If a prompt tries to hijack instructions, the backend should still validate and reject any unsafe or out-of-contract output.

## 4. Recommended Data Separation

```mermaid
flowchart LR
  DB[(Database)] --> TA[Tenant A bucket]
  DB --> TB[Tenant B bucket]
  DB --> TC[Tenant C bucket]

  TA --> MA[Tenant A memory]
  TB --> MB[Tenant B memory]
  TC --> MC[Tenant C memory]
```

Practical rule:

- one user should only read their own records unless explicitly shared
- a shared Hermes process is fine
- a shared database is fine
- shared data must be tenant-scoped at query time

## 5. How Hermes Should Interact With Memory

Hermes itself should not decide which user's memory to load.

The backend should do this:

1. authenticate the request
2. resolve tenant and user id
3. fetch only that user's relevant memory slice
4. optionally summarize old memory
5. build the Hermes prompt
6. send the scoped prompt to Hermes
7. save the result back under the same tenant

```mermaid
sequenceDiagram
  participant User
  participant Backend
  participant MemoryStore
  participant Hermes

  User->>Backend: Authenticated request
  Backend->>MemoryStore: Fetch tenant-scoped memory
  MemoryStore-->>Backend: Only that user's records
  Backend->>Hermes: Prompt + scoped memory
  Hermes-->>Backend: Structured response
  Backend->>MemoryStore: Save result under same tenant
```

## What Must Stay Isolated

The following must never be mixed across users:

- memory records
- drafts
- minion inventories
- session summaries
- tool results
- social assignment data
- prompt history used for retrieval
- error logs that contain sensitive payloads

The tenant boundary should be applied before prompt construction, not after the model responds.

## Why A Shared Hermes Service Can Still Be Safe

Multiple users can hit the same Hermes service safely if:

- each request is independent
- prompts are built from tenant-scoped data only
- no raw global memory bucket is injected
- no request reuses another request's private context
- the backend verifies the response before persistence

That is how 20 users can share one Hermes runtime without sharing one another's data.

## Authentication Model

Your instinct is correct: token-based authentication should be required.

Recommended structure:

- short-lived auth token or session cookie for the frontend
- backend validates the token
- backend derives `userId` and `tenantId`
- every database query includes the tenant filter
- every Hermes prompt is built from the resolved tenant only

Important nuance:

- auth is for the backend boundary
- Hermes does not need to trust users directly
- Hermes only trusts the backend's filtered request

## Prompt Injection Risk

Prompt injection is still a real risk, even with tenant isolation.

The main defense is to treat all user-provided text as untrusted content, not instructions.

```mermaid
flowchart TD
  INPUT[User content] --> SANDBOX[Mark as untrusted]
  SANDBOX --> FILTER[Strip secrets / redact sensitive fields]
  FILTER --> PROMPT[System + task prompt]
  PROMPT --> HERMES[Hermes runtime]
  HERMES --> VALIDATE[Validate structured output]
  VALIDATE --> STORE[Persist safe result]
```

Recommended defenses:

- prefix user data in prompts as quoted or fenced input
- keep system instructions separate from user text
- never let retrieved memory override current system policy
- do not inject secrets into prompts unless absolutely necessary
- validate Hermes output against an allowlist schema
- reject unexpected fields, tool calls, or free-form command execution

## Shared Model Server Risk

If 20 users hit the same OpenRouter or local model server at once, the risk is not that the model magically merges databases.

The real risks are:

- the backend sends the wrong user's memory
- caching is shared across tenants incorrectly
- request IDs get mixed up
- logs contain unredacted private context
- retries replay stale payloads

So the real safety layer is not the provider.

It is the backend boundary.

## Encryption And Storage

Encryption is still important, but it is not enough by itself.

Recommended layers:

- TLS in transit
- encrypted storage at rest
- encrypted secrets in env/config
- tenant-scoped database access
- audit logging for memory reads and writes

Encryption helps if storage is compromised.

It does not replace scoping, auth, or prompt hygiene.

## Risk / Tradeoff Summary

| Choice | Benefit | Tradeoff |
|---|---|---|
| Shared Hermes runtime | Easier to operate, cheaper, simpler deployment | Must enforce strict tenant boundaries in backend |
| Per-user isolated Hermes instance | Stronger process separation | Harder to scale, more expensive, more ops burden |
| Shared database with tenant filters | Practical and fast for MVP | Requires careful query discipline and tests |
| Per-tenant database | Stronger isolation | More infrastructure and migration overhead |
| Backend-owned memory retrieval | Full control over what Hermes sees | More backend logic to maintain |
| Hermes-owned persistent memory | Less backend logic | Harder to trust, harder to isolate, harder to audit |

## Recommended MVP Path

For Hades OS MVP, the safest practical approach is:

1. one shared Hermes runtime
2. one backend-authenticated request pipeline
3. tenant-scoped memory buckets
4. isolated database queries
5. strict response validation
6. prompt-injection-safe prompt construction
7. no raw cross-tenant memory sharing

## MVP Security Flow

```mermaid
flowchart TD
  LOGIN[User logs in] --> TOKEN[JWT/session token]
  TOKEN --> BACKEND[Backend validates identity]
  BACKEND --> FILTER[Load only tenant-scoped memory]
  FILTER --> PROMPT[Build safe prompt]
  PROMPT --> HERMES[Hermes runtime]
  HERMES --> CHECK[Schema validation]
  CHECK --> SAVE[Save under same tenant]
  SAVE --> AUDIT[Record access event]
```

## Final Answer To The Core Question

Yes, multiple people can safely use the same Hermes service or the same OpenRouter/local model server at the same time.

The data stays safe if:

- the backend authenticates each request
- tenant boundaries are enforced in queries
- only scoped memory is injected into each prompt
- Hermes is treated as stateless
- output is validated before storage
- logs and caches do not cross tenants

So the safest answer is not “give Hermes everyone’s memory.”

The safest answer is:

```txt
give Hermes only the current user's scoped context, one request at a time, and keep all long-term memory in the backend with tenant filtering and audit trails.
```
