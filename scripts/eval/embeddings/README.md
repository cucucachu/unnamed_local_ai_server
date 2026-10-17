# Embedding model comparison (M21-02, #389)

Which small embedding model should the platform's file search use
(docs/MEMORY.md §5)? Each model runs in the `embeddings` service's own image
(CPU llama.cpp) and embeds:

- `corpus.json`: 40 files a household member might have: Memory pages
  (people, places, things, interests), notes, recipes, plans, finances, a
  shared space's files, and a small app's sources (AGENT.md, schema.sql, a
  screen) and a Python script.
- `queries.json`: 50 queries, each with the files that answer it:
  - 20 **paraphrase**: plain questions ("what is my partner allergic to");
  - 20 **hard**: paraphrases with no words in common with the answer ("is it
    safe to order a seafood platter for everyone" → Sam's shellfish allergy);
  - 10 **name**: exact names and terms ("Trupanion", "waterPlant action").

Each model gets its own query and document prefixes (`run.py`'s `MODELS`).
Scores are cosine similarity; recall@k counts a query when any expected file
is in the top k.

```
services/embeddings/fetch-model.sh embeddinggemma   # and nomic, qwen3, bge-small
python3 scripts/eval/embeddings/run.py [model ...] [--threads N]
```

## Results (Oct 17, 2026; llama.cpp build 11706; 8 threads; Ryzen AI 9 HX 370)

| model | dims | R@1 | R@5 | MRR | hard R@1 | hard MRR | name R@5 | files/s | query ms | MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| EmbeddingGemma 300M Q8_0 | 768 | 0.88 | 0.98 | 0.92 | 0.95 | 0.97 | 0.90 | 35.6 | 7.7 | 394 |
| nomic-embed-text v1.5 Q8_0 | 768 | 0.76 | 0.98 | 0.84 | 0.75 | 0.84 | 1.00 | 42.4 | 5.4 | 189 |
| Qwen3-Embedding 0.6B Q8_0 | 1024 | 0.82 | 0.96 | 0.88 | 0.80 | 0.87 | 0.90 | 11.9 | 39.0 | 1594 |
| bge-small-en v1.5 Q8_0 | 384 | 0.72 | 0.96 | 0.83 | 0.70 | 0.82 | 0.90 | 138.7 | 2.4 | 64 |

All four found every plain paraphrase in the top 5. The hard paraphrases
separate them: EmbeddingGemma ranked the right file first for 19 of 20, the
others for 14–16. Exact names are where three of the four slip
(EmbeddingGemma put "Leadville" 17th), which full-text matching covers in the
hybrid ranking. Memory is the resident size with the weights loaded
(`--load-mode none`).

**Chosen: EmbeddingGemma 300M Q8_0.** Best on what embeddings add over
words, and small. It embeds ~7,800 characters/s here, so a first index of
10,000 chunks of 1,500 characters takes about half an hour in the
background; after that only changed files are embedded. It's under the
Gemma terms of use.
