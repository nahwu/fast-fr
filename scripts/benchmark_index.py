from __future__ import annotations

import argparse
import statistics
import time

import numpy as np

from fastfr.recognition import IndexItem, RecognitionIndex


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the exact SFace cosine index")
    parser.add_argument("--identities", type=int, default=20_000)
    parser.add_argument("--queries", type=int, default=500)
    args = parser.parse_args()

    rng = np.random.default_rng(2026)
    matrix = rng.normal(size=(args.identities, 128)).astype(np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    items = [
        IndexItem(
            identity_id=index + 1,
            display_name=f"Identity {index + 1}",
            candidate_no=str(index + 1),
            category="blacklist" if index % 1000 == 0 else "whitelist",
        )
        for index in range(args.identities)
    ]
    index = RecognitionIndex()
    index.replace(list(matrix), items)

    queries = rng.normal(size=(args.queries, 128)).astype(np.float32)
    queries /= np.linalg.norm(queries, axis=1, keepdims=True)
    for query in queries[:10]:
        index.search(query, 0.45)

    timings: list[float] = []
    for query in queries:
        started = time.perf_counter()
        index.search(query, 0.45)
        timings.append((time.perf_counter() - started) * 1000)

    print(f"identities={args.identities:,} queries={args.queries:,}")
    print(f"matrix_memory_mb={matrix.nbytes / 1024 / 1024:.2f}")
    print(f"mean_ms={statistics.mean(timings):.3f}")
    print(f"p50_ms={percentile(timings, 0.50):.3f}")
    print(f"p95_ms={percentile(timings, 0.95):.3f}")
    print(f"max_ms={max(timings):.3f}")


if __name__ == "__main__":
    main()
