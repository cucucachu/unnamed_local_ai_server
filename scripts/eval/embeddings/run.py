"""Compare embedding models for file search (M21-02, #389).

Starts each model in the `embeddings` service's own image (CPU llama.cpp),
embeds `corpus.json` and `queries.json` with the model's own query and
document prefixes, and reports recall@1, recall@5 and MRR (overall, and for
paraphrased versus exact-name queries), embedding speed and memory.

    python3 scripts/eval/embeddings/run.py [model ...] [--threads N]

Needs the host's docker and the GGUFs from services/embeddings/fetch-model.sh.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MODELS_DIR = ROOT / "services" / "embeddings" / "models"
IMAGE = "homeai-embeddings-eval"
PORT = 18089

MODELS = {
    "embeddinggemma": {
        "file": "embeddinggemma-300M-Q8_0.gguf",
        "query": "task: search result | query: ",
        "doc": "title: none | text: ",
    },
    "nomic": {
        "file": "nomic-embed-text-v1.5.Q8_0.gguf",
        "query": "search_query: ",
        "doc": "search_document: ",
    },
    "qwen3": {
        "file": "Qwen3-Embedding-0.6B-Q8_0.gguf",
        "query": "Instruct: Given a search query, retrieve relevant passages from the user's "
        "notes and files\nQuery: ",
        "doc": "",
    },
    "bge-small": {
        "file": "bge-small-en-v1.5-q8_0.gguf",
        "query": "Represent this sentence for searching relevant passages: ",
        "doc": "",
    },
}


def _post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


def _wait_healthy(timeout_s: float = 120) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise TimeoutError("embeddings server didn't become healthy")


def _embed(texts: list[str]) -> list[list[float]]:
    data = _post("/v1/embeddings", {"input": texts})["data"]
    vectors = [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
    return [_unit(v) for v in vectors]


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _memory_mib(name: str) -> float:
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("/")[0].strip()
    value, unit = float(out[:-3]), out[-3:]
    return value * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[unit]


def evaluate(key: str, threads: int) -> dict:
    spec = MODELS[key]
    corpus = json.loads((HERE / "corpus.json").read_text())
    queries = json.loads((HERE / "queries.json").read_text())
    name = f"emb-eval-{key}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-p", f"127.0.0.1:{PORT}:8080",
            "-v", f"{MODELS_DIR}:/models:ro",
            IMAGE,
            "--model", f"/models/{spec['file']}",
            "--embedding", "--host", "0.0.0.0", "--port", "8080",
            "--ctx-size", "8192", "--batch-size", "2048", "--ubatch-size", "2048",
            "--threads", str(threads),
            # Load the weights into the process, so the memory figure includes them.
            "--load-mode", "none",
        ],
        check=True,
        capture_output=True,
    )
    try:
        _wait_healthy()
        docs = [spec["doc"] + d["text"] for d in corpus]
        _embed(docs[:2])
        started = time.perf_counter()
        doc_vectors = _embed(docs)
        doc_s = time.perf_counter() - started
        started = time.perf_counter()
        query_vectors = _embed([spec["query"] + q["q"] for q in queries])
        query_s = time.perf_counter() - started
        memory = _memory_mib(name)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    ids = [d["id"] for d in corpus]
    by_kind: dict[str, list[tuple[int | None, list[str]]]] = {}
    misses = []
    for q, qv in zip(queries, query_vectors, strict=True):
        scores = sorted(
            ((sum(a * b for a, b in zip(qv, dv)), i) for dv, i in zip(doc_vectors, ids)),
            reverse=True,
        )
        ranked = [i for _, i in scores]
        rank = next((n for n, i in enumerate(ranked, 1) if i in q["expect"]), None)
        by_kind.setdefault(q["kind"], []).append((rank, ranked[:3]))
        if rank is None or rank > 5:
            misses.append({"q": q["q"], "rank": rank, "top3": ranked[:3]})

    def stats(rows: list[tuple[int | None, list[str]]]) -> dict:
        n = len(rows)
        return {
            "n": n,
            "recall@1": sum(1 for r, _ in rows if r == 1) / n,
            "recall@5": sum(1 for r, _ in rows if r is not None and r <= 5) / n,
            "mrr": sum(1 / r for r, _ in rows if r is not None) / n,
        }

    all_rows = [row for rows in by_kind.values() for row in rows]
    chars = sum(len(d) for d in docs)
    return {
        "model": key,
        "file": spec["file"],
        "dims": len(doc_vectors[0]),
        "overall": stats(all_rows),
        **{kind: stats(rows) for kind, rows in by_kind.items()},
        "docs_per_s": len(docs) / doc_s,
        "chars_per_s": chars / doc_s,
        "query_ms": 1000 * query_s / len(queries),
        "memory_mib": memory,
        "misses": misses,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="*", default=list(MODELS))
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()
    subprocess.run(
        ["docker", "build", "-q", "-t", IMAGE, str(ROOT / "services" / "embeddings")],
        check=True,
        capture_output=True,
    )
    results = [evaluate(m, args.threads) for m in args.models]
    print(json.dumps(results, indent=2))
    print()
    print(f"{'model':16} {'dims':>5} {'R@1':>5} {'R@5':>5} {'MRR':>5} "
          f"{'para R@5':>8} {'hard R@1':>8} {'hard R@5':>8} {'hard MRR':>8} "
          f"{'name R@5':>8} {'docs/s':>7} {'q ms':>6} {'MiB':>6}")
    for r in results:
        print(
            f"{r['model']:16} {r['dims']:>5} {r['overall']['recall@1']:>5.2f} "
            f"{r['overall']['recall@5']:>5.2f} {r['overall']['mrr']:>5.2f} "
            f"{r['paraphrase']['recall@5']:>8.2f} {r['hard']['recall@1']:>8.2f} "
            f"{r['hard']['recall@5']:>8.2f} {r['hard']['mrr']:>8.2f} "
            f"{r['name']['recall@5']:>8.2f} "
            f"{r['docs_per_s']:>7.1f} {r['query_ms']:>6.1f} {r['memory_mib']:>6.0f}"
        )


if __name__ == "__main__":
    main()
