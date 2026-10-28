# Avelin model benchmark

Dataset: `benchmarks\avelin_v1.jsonl` (`006693e491cba7da2609eea9f53484951f0b7f24d69d9dc7db4deda5a6b22b74`)

| Model | Score | Passed | Errors | Median latency | tokens/s | Peak VRAM | Hardware fit |
|---|---:|---:|---:|---:|---:|---:|---|
| qwen3:1.7b | 0.708 | 24/36 | 0 | 417.26 ms | 95.385 | 2161 MiB | expected_fit |

Category scores:

- **qwen3:1.7b**: agent=0.167, context=0.750, json=0.875, russian=0.562, tool_call=1.000
