# Study Log: Why I Chose RTX 3090 on Vast.ai for Local Coding Agents

**Date:** 2026-06-07  
**Project:** Local Qwen Coding Agent on Vast.ai  
**Model:** Qwen3.6 27B MTP GGUF  
**Runtime:** llama.cpp + Caddy + OpenCode  
**Decision:** Use RTX 3090 as the default daily coding GPU

---

## Introduction

I tested different Vast.ai GPU options for running a local coding model.

The main comparison was:

```text
RTX 3090 24GB
vs
Q RTX 8000 45–48GB
vs
RTX 4090 24GB
vs
RTX 5090 32GB
```

The surprising result was that **more VRAM was not automatically better**.

At first, I was thinking mostly in terms of memory:

```text
more VRAM = more context = better coding agent
```

But after testing, that was too simple.

For coding agents, the important bottleneck is not only how much context fits. It is also how fast the model can read that context.

That means the real comparison is:

```text
VRAM
+ prompt processing speed
+ generation speed
+ hourly cost
+ practical responsiveness
```

The RTX 8000 could handle much larger context, but it felt slow for daily coding. The RTX 3090 had less VRAM, but the coding loop felt much better because prompt processing was faster and the price was lower.

```mermaid
flowchart TB
    A[Local Coding Agent] --> B[Prompt Processing Speed]
    A --> C[Generation Speed]
    A --> D[Context Size]
    A --> E[Hourly Cost]
    B --> F[Daily Coding Feel]
    C --> F
    D --> F
    E --> F
    F --> G[RTX 3090 Value Winner]
```

The key lesson:

```text
VRAM decides how much context can fit.
Prompt processing decides how painful that context feels.
Price decides whether it is usable daily.
```

---

## Table of Contents

1. What I Was Optimizing For
2. Why Prompt Processing Matters
3. Actual llama.cpp Prompt Speed by GPU
4. GPU Comparison Table
5. RTX 8000 Test Result
6. Why RTX 3090 Felt Better
7. Why Not RTX 4090?
8. Why Not RTX 5090?
9. Why Not RTX 8000?
10. Model Choice: 27B Dense vs 35B MoE
11. Final Decision
12. Practical Rule Going Forward

---

## 1. What I Was Optimizing For

I was not optimizing for the biggest possible context window.

I was optimizing for:

```text
- fast OpenCode coding loop
- enough context for phase-based repo work
- low hourly cost
- reliable coding behavior
- good prompt processing speed
- manageable setup
```

For coding agents, the model spends a lot of time reading:

```text
- repo files
- AGENTS.md
- phase plans
- module contracts
- diffs
- test output
- previous tool results
- instructions
```

That means **prompt processing speed** matters a lot.

A GPU that can ingest context faster may feel better than a GPU with more VRAM but slower processing.

This was the main mistake in the first version of the study log. I looked too much at VRAM and bandwidth, and not enough at actual prompt-processing throughput.

---

## 2. Why Prompt Processing Matters

There are two different speeds that matter in local LLM inference:

| Metric | Meaning | Why it matters |
|---|---|---|
| **Prompt processing / PP** | How fast the model reads input tokens | Critical for coding agents |
| **Generation / TG** | How fast the model writes output tokens | Important for response speed |
| **Total loop time** | PP + tool calls + generation + tests | What actually matters |

For chat, generation speed is very visible because I am waiting for the answer to appear.

For coding agents, prompt processing can matter even more because the agent repeatedly consumes:

```text
repo context
file contents
terminal output
test failures
diffs
instructions
previous actions
```

So even if generation speed is decent, slow prompt processing can make the agent feel sluggish.

This matters especially when using OpenCode because the model is not just answering one prompt. It is repeatedly reading and acting.

The loop looks more like this:

```text
read context
think
edit file
read terminal output
read diff
read test failure
edit again
read more context
generate final answer
```

That means a slow prompt processor hurts the whole workflow.

---

## 3. Actual llama.cpp Prompt Speed by GPU

A useful public proxy is the llama.cpp CUDA benchmark scoreboard.

The benchmark commonly reports:

```text
pp512 = prompt processing speed for 512 input tokens
tg128 = text generation speed for 128 generated tokens
```

These are not my exact Qwen3.6 27B MTP numbers, so they should not be treated as exact performance predictions.

But they are useful for comparing the relative behavior of GPUs under the same llama.cpp benchmark format.

Public llama.cpp CUDA benchmark numbers show roughly:

| GPU | VRAM | pp512 prompt speed | tg128 generation speed | What it shows |
|---|---:|---:|---:|---|
| **RTX 3090** | 24GB | ~5,174 t/s | ~158 t/s | Strong value baseline |
| **Quadro RTX 8000** | 48GB | ~2,710 t/s | ~103 t/s | Much slower despite more VRAM |
| **RTX 4090** | 24GB | ~11,993 t/s | ~186 t/s | Much faster prompt ingestion |
| **RTX 5090** | 32GB | ~14,073 t/s | ~290 t/s | Fastest consumer option in this comparison |

This changes the argument.

The RTX 3090 did **not** win because it was the fastest GPU.

It clearly was not.

The RTX 4090 and RTX 5090 are much faster for prompt processing.

The RTX 3090 won because it had the best balance for my daily use:

```text
good enough prompt speed
+ low hourly price
+ 24GB VRAM
+ easy availability
+ responsive enough coding loop
```

The RTX 8000 lost as the default because its prompt-processing speed was much lower than the RTX 3090 in the public llama.cpp benchmark, even though it had much more VRAM.

That matches my own experience:

```text
RTX 8000 handled huge context.
RTX 8000 did not feel good as the daily coding card.
```

---

## 4. GPU Comparison Table

| GPU | VRAM | Actual Prompt Speed Pattern | Price Seen | Best For | Decision |
|---|---:|---|---:|---|---|
| **RTX 3090** | 24GB | ~5.1k pp512 t/s public llama.cpp benchmark | ~$0.23–$0.25/hr on-demand, ~$0.12–$0.16/hr interruptible | Daily coding, fast-enough 40k context loops | **Chosen** |
| **Q RTX 8000** | 45–48GB | ~2.7k pp512 t/s public llama.cpp benchmark | ~$0.26/hr seen | Huge context, 100k+ experiments | Not default |
| **RTX 4090** | 24GB | ~12k pp512 t/s public llama.cpp benchmark | ~$0.25 interruptible in one listing, often ~$0.42+ on-demand | Faster tests, speed experiments | Optional upgrade |
| **RTX 5090** | 32GB | ~14k pp512 t/s public llama.cpp benchmark | ~$0.37 interruptible, often higher | Max speed + more context | Future upgrade |

The corrected interpretation:

```text
RTX 8000 = context capacity winner among cheap 48GB options
RTX 3090 = daily value winner
RTX 4090 = speed winner at 24GB
RTX 5090 = speed + 32GB winner, but more expensive
```

So the RTX 3090 is not the best card in absolute performance.

It is the best default card for this specific constraint:

```text
daily local coding agent
low cost
good enough context
fast enough prompt processing
```

---

## 5. RTX 8000 Test Result

The RTX 8000 successfully handled huge context.

Measured result from my own test:

```text
Prompt tokens: 117,826
Truncated: 0
Prompt processing: ~366.66 tokens/sec
Generation speed: ~37.64 tokens/sec
```

This proved that the RTX 8000 could run large context.

It was useful for testing around 100k+ context.

But for actual coding, it felt slow.

The important lesson:

```text
RTX 8000 gave bigger context.
RTX 3090 gave a better daily coding feel.
```

The public llama.cpp benchmark helps explain why.

The Quadro RTX 8000 has more VRAM, but its prompt-processing benchmark is much lower than the RTX 3090. So the card can fit more context, but reading that context can feel slower.

That is exactly the wrong tradeoff for a daily coding loop.

For long planning sessions, huge repo scans, or one-off context experiments, the RTX 8000 still has a role.

But it should not be my default daily coding GPU.

---

## 6. Why RTX 3090 Felt Better

The RTX 3090 felt better because it had a stronger balance of speed, price, and enough VRAM.

Even though it only has 24GB VRAM, it had enough memory for:

```text
Qwen3.6 27B MTP GGUF
UD-Q4_K_XL
40k context
OpenCode
```

That was enough because the project already uses context engineering:

```text
AGENTS.md
project-overview.md
phase files
module contracts
failing tests
work logs
```

So I do not need to keep the entire repo and the entire conversation in context all the time.

The workflow is:

```text
smaller context
+ better context structure
+ faster prompt processing
= better coding loop
```

This is why 40k fast context can beat 100k slow context.

The RTX 3090 is not the best GPU.

It is the best value GPU for this workflow.

That distinction matters.

The corrected conclusion is:

```text
The RTX 3090 is not the performance winner.
It is the value winner.
```

---

## 7. Why Not RTX 4090?

The RTX 4090 is much faster than the RTX 3090 in public llama.cpp prompt-processing benchmarks.

That means it is not just a small upgrade.

It is a real speed upgrade.

The issue is that it still has:

```text
24GB VRAM
```

So for this model, the 4090 does not solve the main memory limit. It mostly improves speed.

That makes it good for:

```text
- benchmarking
- short speed tests
- expensive but fast sessions
- daily use if the interruptible price is close to 3090 pricing
```

But for daily coding, the RTX 3090 gives better value when the 4090 costs much more.

The practical rule:

```text
Use 4090 if the price gap is small.
Use 3090 if the price gap is large.
```

A cheap 4090 interruptible instance can be worth it.

But if the 4090 costs nearly double the 3090, the 3090 remains the better daily default.

---

## 8. Why Not RTX 5090?

The RTX 5090 is the most exciting option technically.

It gives:

```text
32GB VRAM
higher memory bandwidth
much higher prompt-processing speed
much higher generation speed
better future ceiling
```

Compared to the RTX 3090, the 5090 is a real upgrade because it improves both:

```text
speed
and
memory headroom
```

That matters more than the 4090 upgrade, because the 4090 improves speed but still stays at 24GB VRAM.

The 5090 makes sense if the goal is:

```text
- maximum local model speed
- larger context than 3090
- fewer compromises
- short high-speed work bursts
- testing larger models or higher KV precision
```

But for my current goal, I wanted a daily coding backend, not the fastest possible benchmark.

So the 5090 is a future upgrade, not the default.

The practical rule:

```text
5090 is better.
3090 is cheaper.
```

For this project, cheaper matters because the goal is sustained daily use.

---

## 9. Why Not RTX 8000?

The RTX 8000 has the best context headroom among the cheaper options because of its 45–48GB VRAM.

It is useful for:

```text
- 75k–120k context tests
- big repo scans
- huge planning sessions
- fewer compaction cycles
- testing whether long context changes agent behavior
```

But it is older and slower.

In my test, it processed a huge prompt successfully, but actual coding felt slower.

The public llama.cpp benchmark supports that feeling: the Quadro RTX 8000 has much lower prompt-processing throughput than the RTX 3090, even though it has twice the VRAM.

That means the RTX 8000 is not bad.

It is just solving a different problem.

The decision became:

```text
Use RTX 8000 when I need huge context.
Use RTX 3090 when I need fast daily coding.
```

This is the cleanest way to frame it.

The RTX 8000 is a context experiment card.

The RTX 3090 is a daily work card.

---

## 10. Model Choice: 27B Dense vs 35B MoE

The GPU decision connects to the model decision.

The practical rule from my testing was:

```text
If I can run 27B dense fast enough, use 27B dense.
If 27B dense is too slow or too tight, consider 35B MoE.
```

The distinction:

| Model | Strength | Weakness | Best Use |
|---|---|---|---|
| **Qwen3.6 27B Dense** | More reliable for hard coding | Slower than MoE | Contracts, tests, debugging |
| **Qwen3.6 35B-A3B MoE** | Faster active-parameter path | Can be sloppier for coding | Planning, summaries, lighter agent loops |

For coding, I care more about:

```text
correct edits
following contracts
not breaking tests
handling repo structure
respecting instructions
```

So the default should favor reliability.

For this project, the default is:

```text
Qwen3.6 27B MTP UD-Q4_K_XL
40k context
RTX 3090
OpenCode
```

Reason:

```text
coding correctness > raw generation speed
```

The 35B MoE can still be useful, but I would treat it as optional for:

```text
planning
summaries
repo explanation
large-context reading
low-stakes agent loops
```

For implementation and debugging, I prefer the 27B dense model.

---

## 11. Final Decision

The chosen daily setup:

```text
GPU:
RTX 3090

Model:
Qwen3.6 27B MTP GGUF

Quant:
UD-Q4_K_XL

Context:
40,960 tokens

Runtime:
llama.cpp

Coding Tool:
OpenCode

Access:
Caddy API key proxy + Cloudflare tunnel
```

Why:

```text
- fast enough prompt processing
- enough context for phase-based coding
- cheaper than 4090/5090
- more responsive than RTX 8000
- reliable enough for coding
- compatible with context engineering workflow
```

The corrected reasoning:

```text
RTX 3090 is not the fastest.
RTX 3090 is not the largest VRAM option.
RTX 3090 is not the future ceiling.

RTX 3090 wins because it is the best daily value point.
```

That is the real decision.

---

## 12. Practical Rule Going Forward

Use this decision rule:

| Situation | Use |
|---|---|
| Daily coding | **RTX 3090 + 27B dense** |
| Huge context experiment | **RTX 8000** |
| Short speed benchmark | **RTX 4090** |
| Max speed / 32GB VRAM | **RTX 5090** |
| Implementation / tests | **27B dense** |
| Planning / summaries | **35B MoE optional** |

The final lesson:

```text
For coding agents, bigger context is not always better.
Fast prompt processing + disciplined context engineering is usually better.
```

The RTX 3090 won because it gave the best balance:

```text
cost
speed
good-enough context
stable coding workflow
```

But the more precise version is:

```text
The RTX 3090 is the value winner.
The RTX 4090 is the 24GB speed winner.
The RTX 5090 is the speed + 32GB winner.
The RTX 8000 is the cheap huge-context experiment card.
```

For this project, the daily balance matters more than chasing the biggest context window.

The goal is not to own the strongest GPU.

The goal is to keep the coding agent fast enough, cheap enough, and structured enough to use every day.
