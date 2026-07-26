# swafra Benchmark Results

## LongMemEval-S — Session-Level Retrieval Recall

**99.6% recall_all@10** on [LongMemEval-S](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) — the standard benchmark for long-term memory in AI assistants.

> **What this measures:** Given a question and 53 conversation sessions (1–4 containing the answer, rest filler), did swafra retrieve chunks from the correct sessions? This is retrieval recall — the upper bound on how well any LLM reader could answer using swafra's output.

| k | recall_any@10 | recall_all@10 | recall_fraction@10 |
|---|--------------|--------------|-------------------|
| 10 | **100.0%** | **99.6%** | **99.8%** |

### By Category

| Category | recall_any | recall_all | n |
|----------|-----------|-----------|---|
| knowledge-update | 100.0% | 100.0% | 72 |
| single-session-assistant | 100.0% | 100.0% | 56 |
| single-session-preference | 100.0% | 100.0% | 30 |
| single-session-user | 100.0% | 100.0% | 64 |
| temporal-reasoning | 100.0% | 99.2% | 127 |
| multi-session | 100.0% | 98.3% | 121 |

### vs Retrieval Baselines

These are apples-to-apples — all retrieval recall numbers from the original LongMemEval paper.

| System | recall_all@10 | Method |
|--------|--------------|--------|
| **swafra** | **99.6%** | Leiden chunks + BM25 + n-gram + source-diverse BFS |
| flat-stella (1.5B) | ~85–90% | Dense vector retrieval |
| flat-bm25 | ~60–65% | BM25 only |

### vs Memory Systems (different metric — not directly comparable)

Supermemory and Mem0 report **end-to-end QA accuracy** (did the system answer the question correctly?), not retrieval recall. These are different tasks.

| System | Score | Metric |
|--------|-------|--------|
| Supermemory | 95.0% | end-to-end QA accuracy |
| Mem0 | 94.4% | end-to-end QA accuracy |
| **swafra** | **99.6%** | retrieval recall_all@10 |

swafra's 99.6% means the correct session is in the retrieved context in 496/500 cases. End-to-end QA accuracy (how often Claude answers correctly given that context) is a separate, unmeasured step — and what supermemory/mem0 report. Retrieval recall is a necessary precondition: you can't answer correctly from context you didn't retrieve.

---

## How to Reproduce

### Option 1 — Google Colab (easiest, no setup)

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/kunal12203/swafra/blob/master/packages/mcp/bench/longmemeval_colab.ipynb)

Click the badge, run all cells. Downloads the dataset automatically. ~4 min for 50 questions, ~30 min for full 500.

### Option 2 — Local

```bash
# 1. Clone and install
git clone https://github.com/kunal12203/swafra
cd swafra
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

## Dataset & Metric

- **Dataset**: [LongMemEval-S](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) — 500 questions, 53 sessions each (~45 filler + 1–4 answer sessions)
- **Metric**: Official session-level recall — did the retriever surface chunks from `answer_session_ids`?
- **Paper**: [LongMemEval (ICLR 2025)](https://arxiv.org/abs/2410.10813)
- **Eval script**: [`bench/run_eval.py`](bench/run_eval.py) — identical to the original paper's `evaluate_retrieval()` logic

## How swafra Retrieves

1. **Leiden chunking** — community detection on a hybrid graph (semantic + entity + position) for coherent chunk boundaries
2. **4-signal hybrid scoring** — BM25 (0.40) + vector cosine (0.15) + entity/date overlap (0.25) + character n-gram (0.20)
3. **Source-diverse BFS** — best chunk per session ranked by score, graph-walk expands via sequential + similarity + entity edges
4. **Fully local** — no API calls, no GPU, runs on CPU
