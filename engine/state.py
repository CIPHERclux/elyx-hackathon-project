# engine/state.py
import json
from datetime import datetime, timedelta
from pathlib import Path

STATE_FILE = Path("data/seed_state.json")

def _ensure_plan_defaults(state: dict) -> dict:
    # Ensure a place for plan memory and simple telemetry
    state.setdefault("plan", {})
    # plan.history is a list of weekly plans with keys: week_start_iso, exercise (list), diet_focus, note
    state["plan"].setdefault("history", [])
    # recent non-follow events count (rolling)
    state.setdefault("recent_non_follow_events", 0)
    # persona snapshot persisted
    state.setdefault("persona_snapshot", {"trust": 55, "engagement": 52, "frustration": 22})
    # cadence defaults
    state.setdefault("cadence", {})
    state["cadence"].setdefault("exercise_update_days", 14)
    state["cadence"].setdefault("diagnostic_interval_days", 90)
    state["cadence"].setdefault("diet_update_days", 14)
    state["cadence"].setdefault("behavior_update_days", 14)
    # curiosity cap
    state["cadence"].setdefault("max_curiosity_chats_per_week", 5)
    # pending lists
    state.setdefault("pending_tests", [])
    state.setdefault("pending_exercise_updates", [])
    state.setdefault("pending_diet_updates", [])
    state.setdefault("pending_behavior_updates", [])
    return state

def load_state():
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    # inject run_id when present (used for file naming)
    if "run_id" not in state:
        state["run_id"] = None
    state = _ensure_plan_defaults(state)
    return state

def save_state(state):
    # keep run_id if present
    state = _ensure_plan_defaults(state)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def advance_day(state):
    """Advance the simulation by 1 day and maintain ISO date fields."""
    current = datetime.fromisoformat(state["date_iso"])
    new_date = current + timedelta(days=1)
    state["date_iso"] = new_date.date().isoformat()
    # NB: do not clear next_due fields here; they are schedule-driven
    return state
