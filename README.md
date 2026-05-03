# 🤖 Vera: The Next-Gen AI Merchant Assistant

<div align="center">
  <img src="vera_ai_hero_1777821554543.png" alt="Vera AI Banner" width="100%">
  <br />
  <p><b>Empowering 100,000+ local merchants with context-aware, hyper-personalized AI engagement.</b></p>
</div>

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Architecture: Multi-Context](https://img.shields.io/badge/Architecture-Multi--Context-teal.svg)](#architecture)
[![Status: Open Source](https://img.shields.io/badge/Status-Open--Source-vibrant.svg)](#)

</div>

---

## 🌟 Overview

**Vera** is an advanced AI-driven merchant assistant designed for the **magicpin AI Challenge**. Unlike traditional chatbots, Vera is built to bridge the gap between local commerce and digital growth. She talks to merchants over WhatsApp, helping them optimize their Google Business Profiles (GBP), launch high-impact marketing campaigns, and handle customer inquiries with clinical precision.

This repository contains a high-performance implementation of Vera that prioritizes **specificity**, **category-fit**, and **engagement compulsion** over generic AI dialogue.

---

## 🏛️ The 4-Context Framework

Vera doesn't just guess; she reasons based on a rich multi-layered context system. Every message is composed using four distinct data streams:

| Context Layer | Description |
| :--- | :--- |
| **🏢 Category Context** | Vertical-specific knowledge (e.g., Dentists, Salons) including clinical voices, offer catalogs, and peer benchmarks. |
| **🏬 Merchant Context** | Real-time business data: identity, subscription status, performance metrics, and historical engagement. |
| **⚡ Trigger Context** | The "Why Now". External events (festivals, weather) or internal shifts (performance spikes, dormant users). |
| **👥 Customer Context** | Deep insights into the end-customer when Vera acts on behalf of the merchant. |

---

## 🚀 Key Highlights

- **🎯 Precision Anchoring**: Vera avoids fluff. Every nudge is anchored in a verifiable fact—be it a peer statistic, a local news event, or a specific performance delta.
- **🇮🇳 Culturally Aware**: Seamlessly handles **Hinglish (Hindi-English mix)**, matching the natural communication style of Indian merchants.
- **🧬 Deterministic Intelligence**: Built for reliability. Using a temperature-zero reasoning engine, Vera ensures consistent, high-quality responses every single time.
- **🛡️ Auto-Reply Detection**: Intelligent filtering to identify and gracefully exit from merchant auto-replies, saving tokens and maintaining conversation sanity.

---

### [Next: 🛠️ Technical Implementation & Setup (Coming in Part 2/3)]

---

## 🛠️ Technical Architecture

Vera is designed as a **Hybrid Intelligence System**. While most AI bots rely purely on LLM completions (which can be slow, expensive, and hallucination-prone), Vera uses a **Rule-First Reasoning Engine** with an optional **LLM Polishing Layer**.

### 1. Rule-First Composition Engine
The core logic resides in `bot.py`. Every trigger kind has a dedicated composer function (e.g., `compose_perf_dip`, `compose_research_digest`). These functions:
- **Anchoring**: Pull specific metrics (CTR, views, peer benchmarks) directly from the context.
- **Framing**: Apply psychological levers like loss aversion or social proof based on the merchant's current state.
- **Drafting**: Generate a deterministic, high-quality draft message.

### 2. LLM Polishing (Optional)
When enabled via `LLM_POLISH_ENABLED`, Vera uses **Ollama** (locally hosted Llama 3.1) to refine the draft. The LLM is constrained to:
- Adjusting tone to match the category voice (e.g., clinical for doctors, energetic for gyms).
- Enhancing Hinglish flow for natural engagement.
- *Strict Constraint*: The LLM is forbidden from inventing data or changing the factual "hook" of the message.

---

## 📂 Project Structure

```bash
magicpin/
├── bot.py                  # Core Engine: FastAPI app + Composition logic
├── generate_submission.py   # Script to generate the final challenge JSONL
├── judge_simulator.py       # Local simulator to test multi-turn conversations
├── dataset/                # Synthetic but realistic context data
│   ├── categories/         # Vertical-specific knowledge
│   ├── merchants/          # Business-level snapshots
│   ├── triggers/           # Event-driven prompts
│   └── customers/          # End-customer profiles
├── requirements.txt        # Production dependencies (FastAPI, Pydantic, etc.)
└── README.md               # You are here!
```

---

## ⚙️ Installation & Setup

### 1. Clone & Environment
```bash
git clone https://github.com/your-username/magicpin-vera.git
cd magicpin-vera
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. (Optional) Setup LLM Polishing
If you want to use the LLM polishing layer, install [Ollama](https://ollama.com/) and pull the model:
```bash
ollama pull llama3.1
export LLM_POLISH_ENABLED=1
```

---

## 🖥️ Usage & Execution

### Running the Bot (API Mode)
Vera is built as a FastAPI service, allowing for real-time integration.
```bash
uvicorn bot:app --reload --port 8000
```

### Generating Submission
To generate the `submission.jsonl` required for the magicpin challenge:
```bash
python generate_submission.py
```
This script runs the composition logic across the 30 canonical test pairs and outputs the final file.

---

## 🧪 Testing & Validation

Vera includes a **Judge Simulator** to stress-test multi-turn engagement.

```bash
python judge_simulator.py --test-id T01
```
The simulator will:
1. Trigger the bot's initial message.
2. Simulate a merchant's response (including auto-replies or hostile exits).
3. Evaluate the bot's multi-turn handling and adaptability.

---

---

## 📊 Evaluation Philosophy

Vera is designed to be judged by the **magicpin AI Judge** across five critical dimensions. Our implementation optimizes for a high aggregate score (50/50) by focusing on:

| Dimension | Our Implementation Strategy |
| :--- | :--- |
| **🎯 Specificity** | Every message is anchored in verifiable data (numbers, dates, peer stats) from the contexts. |
| **🏢 Category Fit** | Vertical-specific voice profiles ensure a clinical tone for doctors and an engaging tone for gyms. |
| **🏬 Merchant Fit** | Personalized to the merchant's performance, language preference (Hinglish), and history. |
| **⚡ Trigger Relevance** | The "Why Now" is always the hero of the message, explicitly stating the event that prompted it. |
| **📈 Engagement** | Leverages psychological compulsion: social proof, loss aversion, and effort externalization. |

---

## 🔄 Multi-Turn & Replay Strategy

Winning the **Replay Test** requires more than just good first messages. Vera's `respond(...)` logic handles complex conversational shifts:

1.  **Auto-Reply Detection**: If a merchant's message matches known auto-reply patterns, Vera acknowledges the team but gracefully exits to avoid wasting tokens.
2.  **Intent Handoff**: When a merchant says *"Yes, let's do it"* or *"How do I join?"*, Vera immediately switches from "Pitch Mode" to "Action Mode," providing concrete next steps.
3.  **Hostility Management**: Vera identifies hostile or negative sentiment and provides a polite, professional exit to protect brand reputation.
4.  **Context Injection**: Vera is built to adapt mid-conversation if new performance data or digest items are injected into the context.

---

## 🤝 Contributing

This is an open-source project aimed at pushing the boundaries of local commerce AI. We welcome contributions!

1.  **Fork** the repository.
2.  **Create a Feature Branch** (`git checkout -b feature/AmazingFeature`).
3.  **Commit** your changes (`git commit -m 'Add some AmazingFeature'`).
4.  **Push** to the branch (`git push origin feature/AmazingFeature`).
5.  **Open a Pull Request**.

---

## 📜 License & Credits

Distributed under the **MIT License**. See `LICENSE` for more information.

### 👤 Author
**Yash Yadav**
*   BML Munjal University
*   [GitHub Profile](https://github.com/yash-yadav)

### 🏆 Acknowledgments
*   **magicpin AI Team** for the challenge brief and dataset.
*   **Ollama Community** for providing local LLM inference capabilities.

---

<div align="center">
  <p>Built with ❤️ for the future of local commerce.</p>
  <a href="#-vera-the-next-gen-ai-merchant-assistant">Back to Top</a>
</div>


