# run.py
from engine.orchestrator import run_simulation
from dotenv import load_dotenv
load_dotenv()
if __name__ == "__main__":
    out = run_simulation()
    print(f"✅ Export written to: {out}")