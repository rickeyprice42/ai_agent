# Avelin model benchmark

Dataset: `benchmarks\avelin_v1.jsonl` (`006693e491cba7da2609eea9f53484951f0b7f24d69d9dc7db4deda5a6b22b74`)

| Model | Score | Passed | Errors | Median latency | tokens/s | Peak VRAM | Hardware fit |
|---|---:|---:|---:|---:|---:|---:|---|
| phi4-mini | 0.556 | 18/36 | 0 | 3343.82 ms | 19.531 | 2301 MiB | expected_fit_limited_context |

Category scores:

- **phi4-mini**: agent=0.333, context=1.000, json=1.000, russian=0.625, tool_call=0.100
