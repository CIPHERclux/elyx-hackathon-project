# Elyx Simulation Engine — API-Agnostic (One-Key Run)

This repo runs an **AI-vs-AI** WhatsApp-style simulation (Rohan ↔ Elyx), enforces guardrails, applies KPI drift weekly, extracts decisions, and exports JSON—**with only one API key** in `.env`.  
No SDKs; pure HTTP with automatic provider detection (OpenAI-compatible, Anthropic, or Gemini).

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env
# Paste your key into LLM_API_KEY=... (only that)
python run.py