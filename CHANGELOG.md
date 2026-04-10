# 🚀 Release v2.0.0 - The Intelligent Funnel & Precision Scoring Update

This major release completely overhauls the paper evaluation architecture. By shifting from a single-pass fuzzy scoring system to a **Multi-Stage Intelligent Funnel**, this update significantly reduces LLM API costs, drastically improves the signal-to-noise ratio, and introduces robust anti-hallucination protocols.

## ✨ Key Features & Architecture
* **Multi-Stage Filtering Funnel**: Implemented a two-tier LLM evaluation system. 
  * *Level 2*: A fast, cost-effective `deepseek-chat` model now performs semantic title-based pre-filtering to extract the Top K candidates.
  * *Level 3*: The expensive `deepseek-reasoner` model is now strictly reserved for deep abstract analysis, saving massive amounts of token waste.
* **Dual-Key Tuple Sorting (The Tie-Breaker)**: Solved the "score collision" problem. The LLM now assigns a secondary `novelty` score (1-10). Papers are sorted via Python tuple sorting `(relevance, novelty)` to ensure the most innovative papers win tie-breakers.
* **Top Venue Bonus (Peer-Review Prioritization)**: Introduced a `top_venues` whitelist (CVPR, ICCV, TMI, Nature, etc.). The system now parses arXiv metadata (`arxiv_comment`) and automatically grants a `+1` score bonus to papers accepted by top-tier conferences/journals.

## 🧠 Prompt Engineering & LLM Logic
* **Formalized D+M+A Matrix**: Replaced fuzzy prompt instructions with rigid, programming-like logic operators. The LLM now calculates scores independently across Domain (D), Method (M), and Alignment (A).
* **Tool vs. Domain Exception**: Fixed an overly aggressive negative prompt. Papers that *use* LLMs/VLMs merely as methodological tools for Medical/Vision tasks are now intelligently retained, while pure NLP papers are still discarded.
* **Anti-Hallucination Protocol**: Enforced strict "Grounding Rules." The LLM is explicitly forbidden from fabricating datasets, metrics, or methods not present in the raw arXiv text.
* **English-First Reasoning**: Migrated internal LLM instructions to formal Academic English to maximize the logical deduction capabilities of the reasoning model, while preserving highly professional Academic Chinese for the final WeChat Markdown report.

## 🛠️ DevOps & Debugging
* **Transparent Thought Tracing**: The LLM's internal reasoning (`thought_trace`) and tie-breaker scores are now extracted from the JSON and printed directly to the console in real-time, making prompt tuning and debugging highly transparent.
* **Full Title Logging**: Truncation has been removed from the console logs. The system now prints the full, clean paper title during the evaluation loop for better monitoring.
* **ASCII Architecture Map**: Added a comprehensive ASCII data flow diagram inside `main.py` documenting the funnel architecture.

## ⚙️ Configuration Changes (`config.yaml`)
* Added `filter.pre_filter_k` parameter to control the bottleneck size of the funnel.
* Refactored `criteria.research_keywords` into sub-lists (`core_domains` and `methodologies`) for matrix scoring.
* Added `criteria.top_venues` array for the whitelist bonus.

---
**Upgrade Note:** Please completely replace your old `config.yaml` and `main.py` with the latest files, as the prompt variables and JSON parsing logic have fundamentally changed. Happy researching! 🎓
