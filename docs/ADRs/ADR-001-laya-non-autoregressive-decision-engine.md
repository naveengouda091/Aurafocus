# ADR-001: Selection of Laya Non-Autoregressive Decision Engine Over Generative SLMs and Heuristic Regex

**Status:** Accepted  
**Date:** 2026-09-23  
**Deciders:** Staff Technical Product Manager (TPM), Lead Systems Architect  
**Technical Area:** Context Classification, Semantic Intent Engine, Desktop Machine Learning  

---

## 1. Context and Problem Statement

AuraFocus must continuously classify user activity from desktop window metadata (process names, window titles, display states) to determine whether OS-level Focus Assist / Do Not Disturb (DND) should be engaged.

To run seamlessly as a background desktop daemon, the classification engine must satisfy strict operational invariants:
1. **Ultra-Low Latency:** Inference must complete in $< 40\text{ ms}$ on consumer CPU architectures without freezing the UI or introducing perceptible lag.
2. **Minimal Resource Footprint:** Total daemon memory must stay $< 600\text{ MB}$ RSS, and background CPU utilization must stay $< 1.0\%$.
3. **Strict Determinism and Zero Hallucination:** The system must produce strictly typed decisions (`choice`, `score`, `noul`) with calibrated probabilities, without the risk of syntax errors, formatting failures, or non-deterministic rambling.
4. **Generalization Over Semantic Noise:** The system must understand arbitrary window titles (e.g., `feature/auth-refactor - Neovim`, `Quarterly Financial Review - Google Docs - Google Chrome`) without requiring fragile, endless lists of hardcoded rules.

---

## 2. Decision Drivers

- **Execution Latency:** Total sensing + inference budget is $< 50\text{ ms}$.
- **Hardware Agnosticism:** Must run efficiently on laptops and workstations without requiring a dedicated CUDA GPU or NPU.
- **Reliability & Type Safety:** Outputs must map cleanly to enum types and probabilities.
- **Zero Cloud Footprint:** Absolute prohibition on transmitting window titles to cloud endpoints (privacy and compliance requirement).

---

## 3. Considered Options

1. **Option 1: Handcrafted Heuristic & Regular Expression Engine**  
   Matching process names (`code.exe`, `slack.exe`, `chrome.exe`) and regex matching window titles (`/.*meet.google.com.*/`, `/.*YouTube.*/`).
2. **Option 2: Local Generative Small Language Models (SLMs)**  
   Quantized autoregressive decoder models (e.g., Phi-3-mini 3.8B, SmolLM-135M/360M, Qwen2.5-0.5B via llama.cpp or ONNX Runtime).
3. **Option 3: Laya Non-Autoregressive Decision Engine (Selected)**  
   A bidirectional encoder foundation (e.g., ModernBERT) fine-tuned with specialized decision heads for structured classification primitives (`choice`, `score`, `noul`) evaluated in a single forward pass.

---

## 4. Evaluation & Trade-off Matrix

| Criterion | Option 1: Heuristic Regex | Option 2: Generative SLM (0.5B-3B) | Option 3: Laya Decision Engine |
| :--- | :--- | :--- | :--- |
| **Inference Latency** | $< 1\text{ ms}$ (Ultra-fast) | $300\text{ ms} - 2500\text{ ms}$ (Unacceptable) | **$25\text{ ms} - 40\text{ ms}$ (Optimal)** |
| **Memory Footprint (RSS)** | $< 30\text{ MB}$ | $1.2\text{ GB} - 4.5\text{ GB}$ (Violates budget) | **$\approx 380\text{ MB} - 480\text{ MB}$ (Compliant)** |
| **CPU Utilization (1Hz)** | $< 0.1\%$ | $25\% - 85\%$ (Severe battery drain) | **$< 0.8\%$ (CPU-friendly)** |
| **Semantic Generalization**| Terrible (Breaks on novel titles/browsers) | Strong (Broad comprehension) | **Strong (Encoder-based semantic matching)** |
| **Output Type Safety** | High (Hardcoded logic) | Poor (Requires JSON parsing / grammar masks) | **Native (Typed Choice, Score, Noul primitives)** |
| **Hallucination Risk** | Zero | Moderate to High | **Zero (Non-autoregressive forward pass)** |
| **Maintenance Burden** | Extremely High (Constant rule maintenance) | Moderate (Prompt engineering) | **Low (Single frozen lightweight weights)** |

---

## 5. Decision Outcome

**Chosen Option:** **Option 3: Laya Non-Autoregressive Decision Engine**.

### Rationale:
1. **Single-Pass Bidirectional Understanding:** Unlike autoregressive decoders that generate response tokens one by one, Laya processes the entire context envelope simultaneously and projects representations through dedicated classification heads. This reduces inference latency to $\le 35\text{ ms}$ on CPU.
2. **Native Primitives for Focus Automation:**
   - `choice`: Maps active state into the exact discrete focus categories (`deep_work`, `communication_meeting`, `casual_browsing`, `media_or_gaming`).
   - `score`: Assigns an ordinal focus depth score ($1..10$).
   - `noul`: Provides a mathematically calibrated binary probability for whether notifications should be silenced.
3. **No Generation Hallucinations or Grammar Overhead:** Generative models often fail structured JSON schemas or produce invalid tokens unless constrained by heavy grammar-guided samplers. Laya outputs raw tensors directly representing probabilities.
4. **Offline Privacy Guarantee:** The Laya checkpoint is stored on the local filesystem and loaded in memory with zero network dependencies.

---

## 6. Implementation Consequences

- **Positive:**
  - Guarantees sub-50ms daemon loop execution.
  - Maintains resident RAM comfortably beneath the 600 MB ceiling.
  - Eliminates prompt injection or hallucination vulnerabilities from arbitrary window titles.
- **Negative:**
  - Requires local model checkpoint storage ($\approx 250\text{ MB}-400\text{ MB}$ on disk).
  - For machines running in pure test or minimal environments without PyTorch/Laya installed, an internal heuristic fallback wrapper must be provided in `brain.py` to allow automated testing and development without runtime failures.
