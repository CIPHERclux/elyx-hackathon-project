Elyx Hackathon Project 🚀

Elyx is an AI-vs-AI Simulation Engine designed for the hackathon. It models two agents in a WhatsApp-style conversation:
	•	Elyx multi-agent team (system side)
	•	Member (Rohan persona) (user side)

The simulation enforces strict guardrails (diagnostic every 90 days, exercise every 14 days, 50% adherence, travel 1 week/4, chronic condition, etc.) and runs timeline-driven scenarios with state tracking, validator checks, and weekly summarization.

The system outputs:
	•	Full chat log (raw simulation turns)
	•	Condensed timeline (decisions, KPIs, persona state)
	•	Structured JSON (summaries + rationale with message IDs for visualizer integration)

⸻

🔧 Prerequisites
	•	Python 3.10+
	•	pip (latest version recommended)
	•	Recommended: Use a virtual environment

⸻
▶️ Running the Simulation
	•	Add your API key in the .env variable.
	•	Simulate a multi-turn WhatsApp-style chat by running the run.py file.
	•	Visualiser is based on streamlit (streamlit run visualizer.py then u can upload the diary file which is generated after simulation)
	•	View output on the page

 📑 Outputs

After running, check outputs/ for:
	•	runxxdiary.txt → full raw chat simulation
	•	runxx.json → condensed timeline of decisions & state changes
