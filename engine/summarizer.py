# engine/summarizer.py
from typing import List, Dict, Any

def extract_daily_decisions(date_iso: str, day_messages: List[dict]) -> Dict[str, Any]:
    decisions = []
    for m in day_messages:
        txt = (m.get("text") or "").strip()
        if "ACTION:" in txt:
            lines = [l for l in txt.splitlines() if l.strip()]
            action_line = [l for l in lines if l.upper().startswith("ACTION:")]
            if action_line:
                decisions.append({
                    "decision_type": "ACTION",
                    "title": "Planned step",
                    "trigger": "Due/cadence or reported symptom",
                    "rationale": "Short action emitted by persona",
                    "affected_kpis": ["sleep_quality","stress_resilience","cholesterol_total"],
                    "linked_message_ids": [m.get("id")],
                    "confidence": 0.7
                })
    return {"date_iso": date_iso, "decisions": decisions, "notes": None}

def summarize_week(week_start_iso: str, all_decisions: List[dict], persona_state_week: Dict[str,int], state: dict) -> Dict[str, Any]:
    non_follow_events = state.get("recent_non_follow_events", 0)
    kpis = state.get("kpis", {})
    metrics = {
        "doctor_time_hours": 0.5 if any("test" in (d.get("title","").lower()) for d in all_decisions) else 0.0,
        "coach_time_hours": 1.0 if any("exercise" in (d.get("title","").lower()) for d in all_decisions) else 0.5,
        "diet_updates": sum(1 for d in all_decisions if "diet" in (d.get("title","").lower())),
        "behavior_updates": sum(1 for d in all_decisions if "behavior" in (d.get("title","").lower())),
        "non_follow_events": non_follow_events
    }
    return {
        "week_start": week_start_iso,
        "decisions": all_decisions,
        "persona_state": persona_state_week,
        "internal_metrics": metrics,
        "member_kpis_end": kpis
    }
