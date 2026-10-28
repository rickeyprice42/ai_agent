# Avelin model benchmark

Dataset: `benchmarks\avelin_v1.jsonl` (`006693e491cba7da2609eea9f53484951f0b7f24d69d9dc7db4deda5a6b22b74`)

| Model | Score | Passed | Errors | Median latency | tokens/s | Peak VRAM | Hardware fit |
|---|---:|---:|---:|---:|---:|---:|---|
| qwen3:4b-instruct | 0.806 | 28/36 | 0 | 2464.44 ms | 16.619 | 2281 MiB | expected_fit_limited_context |

Category scores:

- **qwen3:4b-instruct**: agent=0.417, context=1.000, json=1.000, russian=0.688, tool_call=0.900
