"""LongMemEval benchmark runner for scimap.

Measures session-level retrieval recall — the official LongMemEval metric.
For each question, all haystack sessions are ingested, scimap retrieves relevant
chunks, and we check whether chunks from the labeled answer sessions were retrieved.

Usage:
    python bench/run_eval.py 500 10    # 500 questions, k=10
    python bench/run_eval.py 50 10     # quick run (50 questions)

Dataset: longmemeval_s_cleaned.json
  Download: https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned

Paper: LongMemEval (ICLR 2025) — https://arxiv.org/abs/2410.10813
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

os.environ.setdefault("SCIMAP_EMBED_BACKEND", "local")
os.environ.setdefault("SCIMAP_DATA_DIR", str(Path("/tmp/scimap-bench")))
Path(os.environ["SCIMAP_DATA_DIR"]).mkdir(parents=True, exist_ok=True)

from scimap_engine import add_knowledge, get_context, _save_json, _CHUNKS_FILE, _EDGES_FILE, _SOURCES_FILE


def reset_store():
    _save_json(_CHUNKS_FILE, [])
    _save_json(_EDGES_FILE, [])
    _save_json(_SOURCES_FILE, [])


def ingest_sessions(sessions, session_ids, dates=None):
    for i, sess in enumerate(sessions):
        date_str = f" [{dates[i]}]" if dates and i < len(dates) else ""
        lines = [f"{t['role'].capitalize()}: {t['content']}" for t in sess]
        text = f"Session: {session_ids[i]}{date_str}\n\n" + "\n\n".join(lines)
        add_knowledge(text, session_ids[i])


def recall_at_k(retrieved_sids, answer_sids):
    retrieved_set = set(retrieved_sids)
    answer_set = set(answer_sids)
    if not answer_set:
        return {"recall_any": 0.0, "recall_all": 0.0, "recall_fraction": 0.0, "hits": [], "missed": []}
    hits = retrieved_set & answer_set
    return {
        "recall_any": 1.0 if hits else 0.0,
        "recall_all": 1.0 if hits == answer_set else 0.0,
        "recall_fraction": len(hits) / len(answer_set),
        "hits": list(hits),
        "missed": list(answer_set - hits),
    }


def evaluate_question(item, k=10):
    reset_store()

    if "_abs" in item["question_id"]:
        return {"question_id": item["question_id"], "question_type": item["question_type"],
                "question": item["question"], "answer": item["answer"], "skipped": True, "reason": "abstention"}

    ingest_sessions(item["haystack_sessions"], item["haystack_session_ids"], item.get("haystack_dates"))

    results = get_context(item["question"], k=k, hops=1, min_source_pct=0.25)
    retrieved_sids = [r.get("source_title", "") for r in results if r.get("source_title")]
    metrics = recall_at_k(retrieved_sids, item["answer_session_ids"])

    return {
        "question_id": item["question_id"],
        "question_type": item["question_type"],
        "question": item["question"],
        "answer": item["answer"],
        "skipped": False,
        "recall_any": metrics["recall_any"],
        "recall_all": metrics["recall_all"],
        "recall_fraction": metrics["recall_fraction"],
        "num_answer_sessions": len(item["answer_session_ids"]),
        "num_sessions_ingested": len(item["haystack_sessions"]),
        "hits": metrics["hits"],
        "missed": metrics["missed"],
        "top_score": results[0]["score"] if results else 0.0,
    }


def main():
    data_dir = Path(__file__).parent / "data"
    data_path = data_dir / "longmemeval_s_cleaned.json"
    if not data_path.exists():
        print("Dataset not found. Download it first:")
        print("  mkdir -p bench/data && cd bench/data")
        print("  curl -LO https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json")
        sys.exit(1)

    with open(data_path) as f:
        data = json.load(f)

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    print(f"LongMemEval-S | {n}/{len(data)} questions | k={k}")
    print("-" * 60)

    results, metrics_by_type, skipped = [], {}, 0
    start = time.time()

    for i, item in enumerate(data[:n]):
        r = evaluate_question(item, k=k)
        results.append(r)
        if r.get("skipped"):
            skipped += 1
            continue
        metrics_by_type.setdefault(r["question_type"], []).append(r)
        status = "HIT" if r["recall_any"] > 0 else "MISS"
        elapsed = time.time() - start
        print(f"  [{i+1:3d}/{n}] {status} | {r['question_type']:25s} | {r['question'][:45]}... | {(i+1)/elapsed:.1f} q/s")

    elapsed = time.time() - start
    evaluated = [r for r in results if not r.get("skipped")]
    if not evaluated:
        return

    recall_any  = sum(r["recall_any"]     for r in evaluated) / len(evaluated)
    recall_all  = sum(r["recall_all"]     for r in evaluated) / len(evaluated)
    recall_frac = sum(r["recall_fraction"] for r in evaluated) / len(evaluated)

    print("\n" + "=" * 60)
    print(f"RESULTS — LongMemEval-S (session-level recall@{k})")
    print(f"  Evaluated:      {len(evaluated)} questions ({skipped} abstention skipped)")
    print(f"  recall_any@{k}:  {recall_any*100:.1f}%")
    print(f"  recall_all@{k}:  {recall_all*100:.1f}%")
    print(f"  recall_frac@{k}: {recall_frac*100:.1f}%")
    print(f"  Time: {elapsed:.1f}s ({len(evaluated)/elapsed:.2f} q/s)")
    print(f"\nBy type:")
    for qtype, items in sorted(metrics_by_type.items()):
        r_any  = sum(r["recall_any"] for r in items) / len(items) * 100
        r_all  = sum(r["recall_all"] for r in items) / len(items) * 100
        print(f"  {qtype:30s}: any={r_any:5.1f}%  all={r_all:5.1f}%  (n={len(items)})")

    out_path = Path(__file__).parent / "results.json"
    with open(out_path, "w") as f:
        json.dump({
            "config": {"n": n, "k": k, "data": "longmemeval_s_cleaned.json"},
            "summary": {
                "evaluated": len(evaluated), "skipped_abstention": skipped,
                "recall_any": recall_any, "recall_all": recall_all, "recall_fraction": recall_frac,
                "elapsed_s": elapsed,
            },
            "by_type": {
                t: {"recall_any": sum(r["recall_any"] for r in items)/len(items),
                    "recall_all": sum(r["recall_all"] for r in items)/len(items),
                    "count": len(items)}
                for t, items in metrics_by_type.items()
            },
            "details": results,
        }, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
