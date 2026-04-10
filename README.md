# Arxiv-Daily-Insight 🚀

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![OpenAI Compatible](https://img.shields.io/badge/LLM-OpenAI--Compatible-orange.svg)](https://platform.openai.com/)

**Arxiv-Daily-Insight** is a professional automated pipeline designed for researchers to stay ahead of the curve. It intelligently tracks, filters, and analyzes daily arXiv submissions using advanced LLM reasoning (like DeepSeek-R1 or GPT-4o).

Stop drowning in hundreds of new papers every day. Let AI find the "Core Readings" for you.

---

## 🌟 Key Features

-   **Multi-Stage Filtering Funnel**: 
    -   **L1 (Hard Filter)**: Keyword-based filtering across the entire 24h arXiv window.
    -   **L2 (Pre-Filter)**: Fast LLM screening based on titles to save tokens.
    -   **L3 (Deep Analysis)**: Advanced Reasoning LLM scoring based on full abstracts.
-   **Intelligent Relevance Scoring**: Prioritizes papers that align perfectly with your *current research focus*.
-   **Academic Visuals**: Delivers beautifully formatted Markdown reports with color-coded analysis (Background, Pain Points, Method, Conclusion).
-   **Instant Delivery**: Seamless integration with **PushPlus** for WeChat notifications.
-   **Robust Error Handling**: Built-in smart retry mechanism for server environments.

---

## 🏗️ Technical Architecture

1.  **Crawler**: Exhausts the 24-hour arXiv CS domain via Open API.
2.  **Funnel**: Implements a local-to-global filtering strategy to minimize LLM costs while maximizing recall.
3.  **Reasoner**: Extracts core insights from abstracts and maps relevance to semantic emojis.
4.  **Dispatcher**: Renders a mobile-optimized HTML card and pushes it to your device.

---

## 🚀 Quick Start

### 1. Installation
```bash
git clone [https://github.com/YourUsername/Arxiv-Daily-Insight.git](https://github.com/YourUsername/Arxiv-Daily-Insight.git)
cd Arxiv-Daily-Insight
pip install -r requirements.txt

## 🖥️ Server Deployment (Automation)

To ensure you receive paper updates every day without maintaining an active SSH session, it is recommended to deploy the pipeline as a **Cron Job** on your Linux server.

### 1. Identify Your Python Path
Since most researchers use Conda or virtual environments, you must use the **absolute path** of your Python interpreter. Find it by running:
```bash
# Activate your environment first
conda activate your_env_name
which python
