# Multi-sweep study report

Backend: ollama  
Tiers: [4]  
Runs per model: 3  
Generated: 2026-08-10 22:40 UTC

### qwen2.5:latest — 3 runs

| Case | Run 1 | Run 2 | Run 3 | Fail rate |
|---|---|---|---|---|
| t4_capital_city | PASS | PASS | PASS | 0/3 |
| t4_simple_math | PASS | PASS | PASS | 0/3 |
| t4_historical_fact | PASS | PASS | PASS | 0/3 |
| t4_definition | PASS | PASS | **FAIL** | 1/3 |
| t4_code_question | PASS | PASS | PASS | 0/3 |
| t4_conversational | PASS | PASS | PASS | 0/3 |

**Total: 1/18 failed** (17 passed, 0 xfailed, 0 other)
