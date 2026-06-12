# Study Log: Hermes as an Agent Runtime Layer

**Date:** June 11, 2026  
**Topic:** Whether Hades OS is currently using Hermes as a true runtime agent layer inside the Node backend, or only as a service abstraction around model calls.  
**Repo:** `hades-os-monorepo`  
**Focus:** Backend runtime architecture, agent boundary, and the next TDD migration slice.

## Question Being Studied

The core question was:

Can Hades OS use Hermes as the runtime layer for agent execution, while the backend still owns product state, routes, persistence, and workflow records?

The answer is yes in architecture, but not yet in full implementation.

## Current Reading Of The System

Right now, the repo has a `Hermes`-named service, but it still behaves mostly like an internal backend abstraction:

- it can call OpenRouter directly
- it can fall back to the local parser
- it does not yet act as a subprocess wrapper around the external Hermes install

That means the backend is still doing the orchestration work itself.

## Current State Diagram

```mermaid
flowchart TD
  UI[Frontend] --> RT[backend/src/modules/hades/routes/hades.routes.js]
  RT --> SV[backend/src/modules/hades/services/hades.service.js]
  SV --> HS[backend/src/modules/hades/services/hermes.service.js]
  HS --> OR[backend/src/modules/hades/services/openRouterClient.js]
  HS --> LP[backend/src/modules/hades/parser.js]
  SV --> REPO[backend/src/modules/hades/repositories/hades.repository.js]
  REPO --> DB[(memory or Supabase)]
```

This is the current shape:

- the backend owns the flow
- `hermes.service.js` is a service boundary, not a runtime boundary
- OpenRouter is still called directly from backend code
- the local parser is still the fallback path

## What The Study Log Is Pointing Toward

The desired architecture is different:

- the backend becomes a control plane
- Hermes becomes the execution engine
- Node wraps Hermes instead of pretending to be Hermes
- the result is persisted back into the product store

## Target Runtime Diagram

```mermaid
flowchart TD
  UI[Frontend] --> ROUTES[backend/src/modules/hades/routes/hades.routes.js]
  ROUTES --> SERVICE[backend/src/modules/hades/services/hades.service.js]

  SERVICE --> WRAPPER[backend/src/modules/hades/services/hermesRuntime.service.js]
  WRAPPER --> SPAWN[Node child_process / bundled Hermes CLI]
  SPAWN --> HERMES[External Hermes runtime under ~/.hermes/hermes-agent]

  HERMES --> ENV[backend/.env]
  HERMES --> MEM[Hermes memory / config]
  HERMES --> TOOLS[skills / MCP / tools]
  HERMES --> MODEL[OpenRouter / provider]

  WRAPPER --> PARSE[Validate structured result]
  PARSE --> SERVICE
  SERVICE --> REPO[backend/src/modules/hades/repositories/hades.repository.js]
  REPO --> DB[(memory or Supabase)]
```

## What This Changes

The important shift is this:

- today, the backend performs the agent work directly
- tomorrow, the backend should hand work to Hermes and store the result

That makes Hermes an actual runtime layer instead of just a named abstraction.

## File-Level Map

```mermaid
flowchart TD
  A[backend/src/modules/hades/routes/hades.routes.js] --> B[backend/src/modules/hades/services/hades.service.js]
  B --> C[backend/src/modules/hades/services/hermes.service.js]
  C --> D[backend/src/modules/hades/services/openRouterClient.js]
  C --> E[backend/src/modules/hades/parser.js]
  B --> F[backend/src/modules/hades/repositories/hades.repository.js]
  B --> G[backend/.env]
```

This is the repo-specific path the runtime refactor would likely follow:

1. keep routes thin
2. move runtime execution behind a Hermes wrapper service
3. persist Hermes execution metadata
4. preserve the local parser as fallback only
5. keep backend `.env` as the shared config source

## What Is Missing

```mermaid
flowchart TD
  M[Missing pieces] --> M1[Hermes subprocess adapter in Node]
  M --> M2[Stable request/response contract]
  M --> M3[Session and job persistence]
  M --> M4[Timeouts / retries / cancellation]
  M --> M5[Structured JSON output validation]
  M --> M6[Provider/context selection from backend .env]
  M --> M7[Tests proving backend uses Hermes, not direct OpenRouter]
```

The missing pieces are mostly boundary and contract work, not UI work.

## Runtime Flow We Want

```mermaid
flowchart TD
  A[Minion request] --> B[Backend creates job record]
  B --> C[Wrapper builds Hermes command]
  C --> D[Wrapper injects backend/.env]
  D --> E[Hermes runs as subprocess]
  E --> F[Hermes returns structured JSON]
  F --> G[Backend validates response]
  G --> H[Save draft/session/status]
  H --> I[Return result to UI]
```

This is the practical behavior we want:

- backend creates the request and owns the record
- Hermes performs the agent step
- Node handles validation and persistence

## TDD Plan

The next implementation slice should be test-driven.

```mermaid
flowchart TD
  S1[Write failing runtime-wrapper tests] --> S2[Write failing service integration tests]
  S2 --> S3[Add Hermes subprocess adapter]
  S3 --> S4[Wire backend service to wrapper]
  S4 --> S5[Persist Hermes session and result state]
  S5 --> S6[Run smoke tests against the live CLI]
```

### Suggested test order

1. add a Node wrapper test for spawning Hermes
2. add a service test proving `hades.service.js` calls the wrapper
3. add a repository test for saving session/result metadata
4. add an integration test for the chat route
5. verify the live Hermes smoke still passes

## Bottom Line

The study log points to the correct architecture for Hades OS:

- Hades backend owns product state and routes
- Hermes owns agent execution
- OpenRouter becomes a provider behind Hermes, not a direct backend dependency

That architecture is not fully implemented yet, but the repo is now close enough to start the runtime-wrapper slice cleanly.
