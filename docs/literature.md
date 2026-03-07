# Related Literature

Curated for: **mcp-relay** — empirical SSRF/injection behavioral study of LLM tool use over MCP  
Last updated: 2026-03-07  
Target venues: IEEE S&P, USENIX Security, ACM CCS

---

## Positioning Statement

Existing work evaluates model behavior using simulated or sandboxed tool environments. mcp-relay introduces a transparent proxy at the MCP protocol layer that intercepts real tool calls in production-equivalent deployments, enabling empirical measurement of SSRF compliance across model families — and demonstrating that relay-layer policy enforcement is the necessary and sufficient mitigation, independent of model alignment.

---

## 1. MCP Security (direct prior art)

### 1.1 SSRF in the Wild

**BlueRock / MarkItDown SSRF (Jan 2026)**  
Source: Dark Reading — https://www.darkreading.com/application-security/microsoft-anthropic-mcp-servers-risk-takeovers  
- Researchers fed Microsoft's MarkItDown MCP server the AWS EC2 metadata IP (`169.254.169.254`) and retrieved IAM credentials (access keys, secret keys, session tokens).
- Found the same latent SSRF exposure in ~36.7% of 7,000+ MCP servers analyzed.
- **Relationship to our work:** Their finding is a *server implementation bug* — MarkItDown fetches without URL validation. Our finding is orthogonal: SSRF is exploitable even when the server is correctly implemented, because the *model* will unconditionally route any attacker-supplied URL through a legitimate fetch tool. The vulnerability is the intended functionality, not insecure code.

**Endor Labs MCP Vulnerability Survey (Jan 2026)**  
arXiv/blog: https://www.endorlabs.com/learn/classic-vulnerabilities-meet-ai-infrastructure-why-mcp-needs-appsec  
- Among 2,614 MCP implementations: 82% prone to path traversal, 67% code injection, 34% command injection, SSRF explicitly enumerated.
- CVE-2025-53967 (Framelink Figma MCP), CVE-2025-68143/44/45 (Anthropic Git MCP), CVE-2025-6514 (mcp-remote) — all classic injection bugs in server code.
- **Relationship:** Confirms SSRF is a known MCP attack class. Our contribution is measuring the *model-layer* dimension: alignment provides zero marginal protection against SSRF regardless of family or reasoning architecture. No prior work has empirically established this claim.

**Snyk Labs — CVE-2025-5276 (SSRF in markdownify-mcp)**  
https://labs.snyk.io/resources/prompt-injection-mcp/  
- Direct SSRF: `toMarkdown()` blindly fetches any URL with no filtering or blocklist.
- **Relationship:** Again a server-code vulnerability. We complement this by showing that even a correctly-coded MCP server with a legitimate fetch tool becomes an SSRF vector when the model layer doesn't refuse malicious URLs.

### 1.2 MCP Ecosystem Security Overviews

**Red Hat MCP Security Controls (Nov 2025)**  
https://www.redhat.com/en/blog/model-context-protocol-mcp-understanding-security-risks-and-controls  
- Covers command injection, sandboxing, SAST/SCA for MCP server pipelines.
- Recommends sandboxing but does not address model-layer compliance behavior.

**Checkmarx — 11 MCP Security Risks (Nov 2025)**  
https://checkmarx.com/zero-post/11-emerging-ai-security-risks-with-mcp-model-context-protocol/  
- SSRF listed among classical vulnerabilities inherited by MCP.
- Notes: "OWASP ranks prompt injection as #1 LLM security risk, and within MCP ecosystems these can trigger automated actions."
- **Relationship:** Conceptual framing only — no empirical model-behavior measurement.

**JFrog CVE-2025-6514 / authzed MCP breach timeline**  
https://authzed.com/blog/timeline-mcp-breaches  
- Comprehensive timeline of CVEs in MCP ecosystem through mid-2025.
- Confirms supply chain and injection attack patterns are well-documented.

**MCP Official Security Best Practices**  
https://modelcontextprotocol.io/specification/draft/basic/security_best_practices  
- Recommends sandboxing, restricted network access, explicit permission grants.
- Notably silent on model-layer URL validation — assumes security is enforced at the server or infrastructure layer.
- **Relationship:** Our policy engine fills exactly this gap: relay-layer enforcement that the spec assumes but doesn't specify.

---

## 2. LLM Agent Behavioral Evaluation

### 2.1 MCP-Specific Benchmarks

**MCP-Bench: Benchmarking Tool-Using LLM Agents (Wang et al., 2025)**  
arXiv: 2508.20453  
- Evaluates schema compliance, multi-step tool coordination across real MCP servers.
- Tests models including gpt-oss-20b, qwen3, glm-4.5, claude-sonnet-4 — overlapping with our model set.
- **Relationship:** MCP-Bench measures *capability* (can the model correctly use tools?). Our Tier 4 and Tier 5 test the orthogonal dimension: *discipline* (will the model refrain from using tools it shouldn't?). These are complementary. Cite as closest prior work; differentiate clearly.

**MCPAgentBench: Real-World Task Benchmark for LLM Agent MCP Tool Use (Liu et al., Dec 2025)**  
arXiv: 2512.24565  
- Very recent (Dec 31, 2025). Real-world task evaluation for MCP tool use.
- **Action required:** Read in full — need to verify differentiation before submission.

### 2.2 Foundational Agent Benchmarks

**AgentBench: Evaluating LLMs as Agents (Liu et al., 2023)**  
arXiv: 2308.03688  
- Foundational benchmark; identifies poor decision-making as a primary obstacle to usable LLM agents.
- **Relationship:** Our Tier 4 finding (llama3.2 fetches Wikipedia for factual questions it should answer directly) is a concrete instantiation of the decision-making failure mode AgentBench identifies. Provides historical anchor for our behavioral taxonomy.

**KDD '25 Survey: Evaluation and Benchmarking of LLM Agents (Mohammadi et al., Jul 2025)**  
arXiv: 2507.21504  
- Two-dimensional taxonomy: evaluation objectives (behavior, capability, reliability, safety) × evaluation process (interaction modes, datasets, metrics).
- Explicitly notes that "safety and compliance is a notable shortcoming in current benchmarks" and that enterprise requirements are "rarely addressed."
- **Relationship:** Use to motivate the measurement gap our relay fills. Our relay-as-infrastructure addresses the "reliability for audit and compliance" gap they identify.

**AgentNoiseBench (2025)**  
arXiv: 2602.11348  
- Evaluates agent robustness under noisy/adversarial tool outputs.
- Tests many overlapping models (qwen3-max, glm-4.5/4.6, gpt-oss-20b, claude-4-sonnet).
- **Relationship:** Peripheral — focuses on robustness to tool *output* noise, not model *compliance* with unsafe inputs.

---

## 3. Agent Harm and Safety

### 3.1 Primary Citation

**AgentHarm: A Benchmark for Measuring Harmfulness of LLM Agents (Andriushchenko et al., ICLR 2025)**  
arXiv: 2410.09024  
Authors: Andriushchenko, Souly, Dziemian, Duenas, Lin, Wang, Hendrycks, Zou, Kolter, Fredrikson, Winsor, Wynne, Gal, Davies (EPFL, CMU, Oxford, Center for AI Safety; in collaboration with UK AI Safety Institute)  
Key findings:
1. Leading LLMs are surprisingly compliant with malicious agent requests **without jailbreaking**.
2. Simple universal jailbreak templates can be adapted to effectively jailbreak agents.
3. Jailbreaks enable coherent, malicious multi-step agent behavior.
- **Relationship:** This is our strongest citation for the SSRF compliance finding. AgentHarm establishes that model alignment does not prevent harmful agentic behavior in general. We establish the specific case for SSRF over MCP with a live-protocol measurement methodology, across 5 models from 4 organizations, and demonstrate that the finding is alignment-invariant (holds even for models with explicit safety fine-tuning like gpt-oss:20b). Our relay provides ground-truth interception at the protocol layer vs. AgentHarm's simulated tool environments.

### 3.2 Related Safety Work

**Learning When to Act or Refuse (2026)**  
arXiv: 2603.03205 (Mar 3, 2026 — very recent)  
- Pairwise preference-based RL fine-tuning to teach models when to refuse multi-step tool use.
- Evaluates on AgentHarm, Agent Security Bench, PrivacyLens.
- **Relationship:** Represents the model-layer mitigation approach. Our claim is that model-layer mitigations are insufficient for SSRF in MCP because alignment-trained models still comply. This paper is concurrent work — check carefully for anything that contradicts our finding.

---

## 4. Prompt Injection (adjacent attack surface)

### 4.1 Foundational

**Prompt Injection Attack Against LLM-Integrated Applications (Liu et al., 2023)**  
arXiv: 2306.05499  
- HouYi: black-box injection technique drawing from traditional web injection attacks.
- Establishes direct vs. indirect injection taxonomy.
- **Relationship:** Our Tier 5 adversarial cases test injection resistance. HouYi provides the foundational taxonomy. Cite for historical context.

### 4.2 Agent-Specific Injection Benchmarks

**AgentDojo: Dynamic Environment for Prompt Injection (Debenedetti et al., NeurIPS 2024)**  
- Standard benchmark for indirect prompt injection in agents. Widely cited as the reference evaluation environment.
- **Relationship:** AgentDojo uses simulated environments with sandboxed tools. Our relay intercepts real MCP protocol traffic. Different methodology; complementary coverage.

**InjecAgent (Zhan et al., 2024)**  
- Indirect prompt injection benchmark.

**Agent Security Bench (Zhang et al., 2025)**  
- Evaluates robustness to direct and indirect prompt injection across diverse real-world scenarios.

**Adaptive Attacks Break Defenses (Zhan et al., arXiv:2503.00061, Feb 2025)**  
- Evaluates 8 defenses against indirect prompt injection; bypasses all of them with adaptive attacks achieving >50% success rate.
- **Relationship:** Corroborates our architectural argument: model-layer and prompt-layer defenses are unreliable. Relay-layer policy enforcement (URL allowlisting) is structurally different — it does not depend on the model making a safety judgment.

### 4.3 Defense Approaches

**PromptArmor (Shi et al., arXiv:2507.15219, Jul 2025)**  
- Guardrail LLM placed at input/output boundary; evaluated on AgentDojo.
- **Relationship:** Represents a detection-based defense. Our policy engine is rule-based (allowlist), which avoids the adversarial transferability problem that guardrail LLMs face.

**StruQ: Defending Against Prompt Injection with Structured Queries (Chen et al., USENIX Security 2025)**  
- Fine-tuning approach using structured query formats to separate instructions from data.
- **Relationship:** Training-based defense; requires model access. Our relay is model-agnostic.

**Spotlighting: Defending Against Indirect Prompt Injection (Hines et al., 2024)**  
arXiv: 2403.14720  
- Prompt augmentation defense (delimiting external content).
- **Relationship:** Prompt-level, model-dependent. Our relay-layer policy is model-agnostic and structurally enforced.

**Indirect Prompt Injections: Are Firewalls All You Need? (2025)**  
arXiv: 2510.05244  
- Proposes lightweight "minimize & sanitize" tool-boundary firewalls (Tool-Input Firewall + Tool-Output Firewall).
- Argues that many existing benchmarks use skewed metrics making weak defenses appear effective.
- **Relationship:** Closest in spirit to our policy engine architecture. Key difference: their firewalls are LLM-based (require another model call); our policy engine is deterministic (URL allowlist). For SSRF specifically, a deterministic allowlist is both simpler and provably stronger.

---

## 5. Key Gaps Our Work Addresses

| Gap | Prior Work | Our Contribution |
|-----|-----------|-----------------|
| SSRF compliance is model-layer invariant | Not demonstrated empirically | 5 models, 4 organizations, same result |
| Live MCP protocol interception as methodology | Simulated/sandboxed tools (AgentHarm, AgentDojo, MCP-Bench) | Transparent relay at MCP protocol layer |
| Tool-use *discipline* taxonomy (Tier 4: should NOT call) | All benchmarks focus on capability, not restraint | Tier 1–5 taxonomy explicitly separates these |
| Policy engine as structural mitigation | Prompt-level or training-based defenses | Relay-layer URL allowlist — model-agnostic, deterministic |
| Cross-family comparison at Ollama-scale models | Typically frontier API models only | 5 local models: Alibaba, Meta, OpenAI, ZHIPU AI |

---

## 6. Reading Queue (Priority Order)

1. **AgentHarm** (arXiv:2410.09024) — primary citation, read PDF in full
2. **MCP-Bench** (arXiv:2508.20453) — closest related work, must differentiate clearly
3. **MCPAgentBench** (arXiv:2512.24565) — very recent, read before submission
4. **"Are Firewalls All You Need?"** (arXiv:2510.05244) — most architecturally similar to our policy engine
5. **"Adaptive Attacks Break Defenses"** (arXiv:2503.00061) — supports our relay-layer argument
6. **Learning When to Act or Refuse** (arXiv:2603.03205) — concurrent model-layer mitigation work; check for contradictions
7. **BlueRock MarkItDown SSRF** — Dark Reading piece + original research disclosure

---

## 7. Suggested Related Work Paragraph (Draft)

> Prior work on MCP security has focused on implementation vulnerabilities in server code — command injection, path traversal, and SSRF arising from absent URL validation [Endor Labs 2026; Snyk 2025; BlueRock 2026]. Our finding is orthogonal: we demonstrate that SSRF is exploitable through correctly-implemented MCP fetch servers because model alignment provides no protection against attacker-supplied URLs being routed through legitimate tools. This finding extends the result of AgentHarm [Andriushchenko et al., ICLR 2025], which showed that leading LLMs comply with malicious multi-step agentic requests without jailbreaking, to the specific case of SSRF via MCP tool calls. Agent benchmarks such as MCP-Bench [Wang et al., 2025] and AgentBench [Liu et al., 2023] evaluate whether models use tools *correctly*; our 5-tier behavioral corpus is the first to explicitly test whether models exercise *restraint* in tool invocation (Tier 4) and resist adversarial URL injection (Tier 5). Prompt injection defenses relying on prompt augmentation [Hines et al., 2024], guardrail models [Shi et al., 2025], or fine-tuning [Chen et al., 2025] are model-dependent and subject to adaptive attack [Zhan et al., 2025]; our relay-layer URL allowlist is model-agnostic and deterministic, providing structural enforcement independent of model alignment state.

---

*This document is a living reference. Add entries as new papers are found. Flag papers that contradict our claims for priority review.*
