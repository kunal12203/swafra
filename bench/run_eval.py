"""LongMemEval benchmark runner for scimap MCP engine.

Official retrieval evaluation: session-level recall@k.
Ground truth: each question has `answer_session_ids` identifying which sessions
contain the evidence. We measure whether scimap retrieves chunks from those sessions.

Uses longmemeval_s_cleaned.json (53 sessions per question, ~1-3 are answer sessions).
No heuristics, no bias — pure retrieval accuracy against labeled ground truth.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

# Add engine to path
sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

# Set env BEFORE importing the engine
os.environ["SCIMAP_EMBED_BACKEND"] = "local"
os.environ["SCIMAP_DATA_DIR"] = str(Path("/tmp/scimap-bench"))
Path("/tmp/scimap-bench").mkdir(parents=True, exist_ok=True)

from scimap_engine import add_knowledge, get_context, _save_json, _CHUNKS_FILE, _EDGES_FILE, _SOURCES_FILE


def reset_store():
    _save_json(_CHUNKS_FILE, [])
    _save_json(_EDGES_FILE, [])
    _save_json(_SOURCES_FILE, [])


def ingest_sessions(sessions: list[list[dict]], session_ids: list[str], dates: list[str] | None = None):
    """Ingest each session as a separate source, tagged with its session_id."""
    for i, sess in enumerate(sessions):
        date_str = f" [{dates[i]}]" if dates and i < len(dates) else ""
        lines = []
        for turn in sess:
            role = turn["role"].capitalize()
            lines.append(f"{role}: {turn['content']}")
        text = f"Session: {session_ids[i]}{date_str}\n\n" + "\n\n".join(lines)
        add_knowledge(text, session_ids[i])


def get_retrieved_session_ids(chunks: list[dict]) -> list[str]:
    """Map retrieved chunks back to their source session_ids via source_title."""
    retrieved = []
    for chunk in chunks:
        sid = chunk.get("source_title", "")
        if sid:
            retrieved.append(sid)
    return retrieved


def recall_at_k(retrieved_session_ids: list[str], answer_session_ids: list[str]) -> dict:
    """Compute recall_any and recall_all against ground truth answer sessions."""
    retrieved_set = set(retrieved_session_ids)
    answer_set = set(answer_session_ids)

    if not answer_set:
        return {"recall_any": 0.0, "recall_all": 0.0}

    hits = retrieved_set & answer_set
    recall_any = 1.0 if len(hits) > 0 else 0.0
    recall_all = 1.0 if hits == answer_set else 0.0
    recall_fraction = len(hits) / len(answer_set)

    return {
        "recall_any": recall_any,
        "recall_all": recall_all,
        "recall_fraction": recall_fraction,
        "hits": list(hits),
        "missed": list(answer_set - hits),
    }


def evaluate_question(item: dict, k: int = 10) -> dict:
    """Evaluate retrieval for a single question using official ground truth."""
    reset_store()

    sessions = item["haystack_sessions"]
    session_ids = item["haystack_session_ids"]
    dates = item.get("haystack_dates")
    question = item["question"]
    answer_sids = item["answer_session_ids"]

    # Skip abstention questions (no ground truth retrieval target)
    if "_abs" in item["question_id"]:
        return {
            "question_id": item["question_id"],
            "question_type": item["question_type"],
            "question": question,
            "answer": item["answer"],
            "skipped": True,
            "reason": "abstention",
        }

    # Ingest all haystack sessions
    ingest_sessions(sessions, session_ids, dates)

    # Retrieve
    results = get_context(question, k=k, hops=1)

    # Map chunks back to session_ids
    retrieved_sids = get_retrieved_session_ids(results)

    # Compute recall
    metrics = recall_at_k(retrieved_sids, answer_sids)

    return {
        "question_id": item["question_id"],
        "question_type": item["question_type"],
        "question": question,
        "answer": item["answer"],
        "skipped": False,
        "recall_any": metrics["recall_any"],
        "recall_all": metrics["recall_all"],
        "recall_fraction": metrics["recall_fraction"],
        "num_answer_sessions": len(answer_sids),
        "num_chunks_retrieved": len(results),
        "num_sessions_ingested": len(sessions),
        "retrieved_sessions": retrieved_sids[:10],
        "answer_sessions": answer_sids,
        "hits": metrics["hits"],
        "missed": metrics["missed"],
        "top_score": results[0]["score"] if results else 0.0,
    }


def main():
    # Prefer the full haystack (longmemeval_s_cleaned.json) for real testing
    data_dir = Path(__file__).parent / "data"
    data_path = data_dir / "longmemeval_s_cleaned.json"
    if not data_path.exists():
        # Fall back to oracle if S not available
        data_path = data_dir / "longmemeval_oracle.json"
        if not data_path.exists():
            print("Error: No data file found. Download longmemeval_s_cleaned.json:")
            print("  curl -LO https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json")
            sys.exit(1)

    print(f"Loading {data_path.name}...")
    with open(data_path) as f:
        data = json.load(f)

    # Parse CLI args
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    print(f"Running LongMemEval retrieval eval: {n}/{len(data)} questions (k={k})")
    print(f"Data: {data_path.name}")
    print(f"Metric: session-level recall@{k} (official)")
    print(f"Data dir: {os.environ['SCIMAP_DATA_DIR']}")
    print("-" * 70)

    results = []
    metrics_by_type: dict[str, list[dict]] = {}
    skipped = 0
    start = time.time()

    for i, item in enumerate(data[:n]):
        result = evaluate_question(item, k=k)
        results.append(result)

        if result.get("skipped"):
            skipped += 1
            print(f"  [{i+1:3d}/{n}] SKIP | {result['question_type']:25s} | {result['reason']}")
            continue

        qtype = result["question_type"]
        metrics_by_type.setdefault(qtype, []).append(result)

        status = "HIT" if result["recall_any"] > 0 else "MISS"
        elapsed = time.time() - start
        qps = (i + 1) / elapsed if elapsed > 0 else 0
        print(f"  [{i+1:3d}/{n}] {status} recall={result['recall_fraction']:.2f} | "
              f"{qtype:25s} | q={result['question'][:45]}... | {qps:.1f} q/s")

    # Summary
    elapsed = time.time() - start
    evaluated = [r for r in results if not r.get("skipped")]

    if not evaluated:
        print("\nNo questions evaluated.")
        return

    avg_recall_any = sum(r["recall_any"] for r in evaluated) / len(evaluated)
    avg_recall_all = sum(r["recall_all"] for r in evaluated) / len(evaluated)
    avg_recall_frac = sum(r["recall_fraction"] for r in evaluated) / len(evaluated)

    print("\n" + "=" * 70)
    print(f"RESULTS (official session-level recall@{k})")
    print(f"  Evaluated: {len(evaluated)} questions ({skipped} abstention skipped)")
    print(f"  recall_any@{k}:  {avg_recall_any*100:.1f}%  (at least 1 answer session retrieved)")
    print(f"  recall_all@{k}:  {avg_recall_all*100:.1f}%  (ALL answer sessions retrieved)")
    print(f"  recall_frac@{k}: {avg_recall_frac*100:.1f}%  (fraction of answer sessions retrieved)")
    print(f"  Time: {elapsed:.1f}s ({len(evaluated)/elapsed:.2f} q/s)")

    print(f"\nBy type:")
    for qtype, items in sorted(metrics_by_type.items()):
        r_any = sum(r["recall_any"] for r in items) / len(items) * 100
        r_all = sum(r["recall_all"] for r in items) / len(items) * 100
        r_frac = sum(r["recall_fraction"] for r in items) / len(items) * 100
        print(f"  {qtype:30s}: any={r_any:5.1f}%  all={r_all:5.1f}%  frac={r_frac:5.1f}%  (n={len(items)})")

    # Save detailed results
    out_path = Path(__file__).parent / "results.json"
    with open(out_path, "w") as f:
        json.dump({
            "config": {
                "n": n, "k": k,
                "data": data_path.name,
                "backend": os.getenv("SCIMAP_EMBED_BACKEND", "auto"),
                "metric": "session-level recall (official LongMemEval)",
            },
            "summary": {
                "evaluated": len(evaluated),
                "skipped_abstention": skipped,
                "recall_any": avg_recall_any,
                "recall_all": avg_recall_all,
                "recall_fraction": avg_recall_frac,
                "elapsed_s": elapsed,
            },
            "by_type": {
                t: {
                    "recall_any": sum(r["recall_any"] for r in items) / len(items),
                    "recall_all": sum(r["recall_all"] for r in items) / len(items),
                    "recall_fraction": sum(r["recall_fraction"] for r in items) / len(items),
                    "count": len(items),
                }
                for t, items in metrics_by_type.items()
            },
            "details": results,
        }, f, indent=2)
    print(f"\nDetailed results saved to {out_path}")


if __name__ == "__main__":
    main()
