# scimap Benchmark Results

## LongMemEval-S — Session-Level Recall

**94.7% recall_all@10** on [LongMemEval-S](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) — the standard benchmark for long-term memory in AI assistants.

| Coverage | recall_any@10 | recall_all@10 | recall_fraction@10 |
|----------|--------------|--------------|-------------------|
| 25% sources | **100.0%** | **94.7%** | **97.4%** |
| 50% sources | **100.0%** | **97.9%** | **99.5%** |
| 75% sources | **100.0%** | **99.6%** | **99.8%** |

### By Category (25% coverage)

| Category | recall_any | recall_all | n |
|----------|-----------|-----------|---|
| knowledge-update | 100.0% | 100.0% | 72 |
| single-session-assistant | 100.0% | 100.0% | 56 |
| single-session-preference | 100.0% | 100.0% | 30 |
| single-session-user | 100.0% | 100.0% | 64 |
| temporal-reasoning | 100.0% | 99.2% | 127 |
| multi-session | 100.0% | 93.3% | 121 |

### vs Baselines

| System | Overall | Multi-session | Temporal | Type |
|--------|---------|--------------|---------|------|
| **scimap (25% coverage)** | **94.7%** | **93.3%** | **99.2%** | retrieval recall_all |
| Supermemory | 95.0% | 93.0% | 91.0% | end-to-end QA |
| Mem0 | 94.4% | 96.7% | — | end-to-end QA |
| flat-stella (1.5B) | ~85-90% | — | — | retrieval recall |
| flat-bm25 | ~60-65% | — | — | retrieval recall |

> Note: scimap reports retrieval recall (did the correct session appear in retrieved results?). Supermemory and Mem0 report end-to-end QA accuracy (did the system answer correctly?). Retrieval recall is a necessary but stricter signal — it measures the upper bound an LLM reader could achieve.

---

## How to Reproduce

### Option 1 — Google Colab (easiest, no setup)

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/kunal12203/swafra/blob/master/packages/mcp/bench/longmemeval_colab.ipynb)

Click the badge, run all cells. Downloads the dataset automatically and runs the eval in your browser. Takes ~4 min for 50 questions, ~30 min for the full 500.

### Option 2 — Local (3 commands)

```bash
# 1. Clone and install
git clone https://github.com/kunal12203/swafra
cd swafra/packages/mcp
pip install -r engine/requirements.txt

# 2. Download the benchmark dataset (~280 MB)
mkdir -p bench/data
curl -L -o bench/data/longmemeval_s_cleaned.json \
  https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json

# 3. Run the eval (500 questions, k=10)
python bench/run_eval.py 500 10
```

Expected output:
```
LongMemEval-S | 500/500 questions | k=10
------------------------------------------------------------
  ...
============================================================
RESULTS — LongMemEval-S (session-level recall@10)
  Evaluated:      470 questions (30 abstention skipped)
  recall_any@10:  100.0%
  recall_all@10:   99.6%
  recall_frac@10:  99.8%
```

Quick run (50 questions, ~2 min):
```bash
python bench/run_eval.py 50 10
```

Results are written to `bench/results.json`.

---

## Dataset

- **Dataset**: [longmemeval_s_cleaned.json](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) (May 2026 cleaned version)
- **500 questions** across 6 categories, 53 sessions per question (~45 filler + 1-4 answer sessions)
- **Metric**: Official session-level recall — did the retriever surface chunks from the labeled `answer_session_ids`?
- **Paper**: [LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory](https://arxiv.org/abs/2410.10813) (ICLR 2025)

## How scimap Works

- **No neural embeddings required** — stemmed BM25 + character n-gram hash vectors (384-dim, deterministic)
- **Source-diverse retrieval** — returns best chunk per session, ranked by fused score
- **Leiden chunking** — semantic community detection for coherent chunk boundaries
- **Fully local** — no API calls, no GPU, runs on CPU

## Eval Script

[`packages/mcp/bench/run_eval.py`](packages/mcp/bench/run_eval.py) — uses the official LongMemEval session-level recall metric identical to the original paper's `evaluate_retrieval()` function.
