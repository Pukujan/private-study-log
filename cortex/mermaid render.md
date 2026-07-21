flowchart TB
  subgraph AUTH["Authority"]
    OG[Owner gold scores<br/>owner_abc_scores.jsonl]
    ITEMS[items.jsonl 24]
    SPLIT[split.json train/holdout]
    INJ[P5-lite inject rules<br/>OWNER-LEGIBLE-OPENCODE-INJECT]
    FU[Frozen unit write_set<br/>wu_20260720_...]
  end

  subgraph IN["Inputs per gold item"]
    ITEMS --> SRC[Source text]
  end

  subgraph PROD["Produce arms writers"]
    SRC --> GOLD[gold_as_is]
    SRC --> DUMP[baseline_dump_corrupt]
    SRC --> P5[p5lite_clear_rewrite]
    SRC --> FS[Fable few-shots from train clear/high]
    FS --> GR[grok_fable_style<br/>square-grok]
    FS --> LU[luna_fable_style<br/>gpt-5.6-luna]
    SRC --> MID[mid_jargon_trim]
    SRC --> BW[bullet_wall]
    SRC --> VFL[verdict_first_long]
    GR -.->|live or synthetic| GR
    LU -.->|live or synthetic| LU
  end

  subgraph SCORE["Score each cell"]
    GOLD & DUMP & P5 & GR & LU & MID & BW & VFL --> D0[D0 deterministic<br/>soft-fail flags]
    GOLD & DUMP & P5 & GR & LU & MID & BW & VFL --> FC[Fable-compare<br/>vs high-gold refs]
    GOLD & DUMP & P5 & GR & LU & MID & BW & VFL --> TE[Terra judge 1-10<br/>live gpt-5.6-terra<br/>or heuristic proxy]
  end

  subgraph PAIR["Pairwise"]
    DUMP --> PW1[vs p5lite]
    DUMP --> PW2[vs grok]
    DUMP --> PW3[vs luna]
    GR --> PW4[vs luna]
  end

  subgraph M19["M19 calibration"]
    OG --> AGR[Agreement train/holdout<br/>pearson/spearman Terra vs owner]
    TE --> AGR
    AGR -->|fail| NP[Terra NOT promoted]
    AGR -->|pass later| PR[Promote Terra judge]
  end

  subgraph OUT["Artifacts"]
    D0 & FC & TE & PW1 & PW2 & PW3 & PW4 --> CELLS[cells.jsonl ~180]
    CELLS --> REP[report.json + SUMMARY.md]
    CELLS --> PC[produce_cache.json]
    REP --> RANK[Arm ranking by fable_compare]
  end

  INJ -.->|style rules| P5
  INJ -.->|few-shot rules| FS
  FU -.->|paths allowed| OUT
  OG -.->|anchor only| FC
