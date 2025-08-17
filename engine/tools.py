# engine/tools.py
# Emulated tools parsed from ACTION lines. Also handles member-shared test reports.
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from engine.state import save_state
import random

def _is_travel_date(state, date_iso: str) -> bool:
    return date_iso in set(state.get("member", {}).get("travel_weeks", []))

def _bump_past_travel(state, date_iso: str) -> str:
    d = datetime.fromisoformat(date_iso).date()
    while _is_travel_date(state, d.isoformat()):
        d = d + timedelta(days=7)
    return d.isoformat()

# NEW: Implement comprehensive panel function for quarterly diagnostics
def propose_comprehensive_panel(state: dict, date_iso: str):
    """Schedule a full diagnostic panel (multiple tests) for quarterly check-ins"""
    last = state.get("last_events", {}).get("diagnostic_test")
    cadence_days = int(state.get("cadence", {}).get("diagnostic_interval_days", 90))
    
    # Check if it's been long enough since the last test
    if last:
        last_dt = datetime.fromisoformat(last).date()
        when = datetime.fromisoformat(date_iso).date()
        days_since = (when - last_dt).days
        
        # Enforce strict 90-day cadence (quarterly)
        if days_since < cadence_days - 5:
            return False, f"CADENCE_DIAGNOSTIC: Quarterly diagnostics only due every {cadence_days} days"
    
    # Ensure we're not scheduling during travel
    date_iso = _bump_past_travel(state, date_iso)
    
    # Create a comprehensive panel
    panel = [
        {"test_type": "Lipid panel", "date_iso": date_iso},
        {"test_type": "HbA1c", "date_iso": date_iso},
        {"test_type": "CRP", "date_iso": date_iso},
        {"test_type": "Vitamin D", "date_iso": date_iso},
        {"test_type": "Comprehensive Metabolic Panel", "date_iso": date_iso}
    ]
    
    # Update the pending tests list
    pending = state.setdefault("pending_tests", [])
    
    # Remove any tests already scheduled for this date
    pending = [t for t in pending if t.get("date_iso") != date_iso]
    
    # Add the new panel
    pending.extend(panel)
    state["pending_tests"] = pending
    
    # Update next due date
    state["next_test_due_iso"] = date_iso
    state["next_due"] = state.get("next_due", {})
    state["next_due"]["diagnostic_test"] = date_iso
    
    # Track time for comprehensive panel
    track_time_commitment(state, 1.5, "diagnostic testing", date_iso)
    
    # Save changes
    save_state(state)
    return True, f"Scheduled comprehensive diagnostic panel for {date_iso}"

def propose_test(state: dict, test_type: str, date_iso: str):
    allowed = set(state.get("elyx_rules", {}).get("allowed_test_panel", []))
    if test_type not in allowed:
        return False, f"{test_type} not allowed"

    last = state.get("last_events", {}).get("diagnostic_test")
    cadence_days = int(state.get("cadence", {}).get("diagnostic_interval_days", 90))
    if last:
        last_dt = datetime.fromisoformat(last).date()
        when = datetime.fromisoformat(date_iso).date()
        days_since = (when - last_dt).days
        if days_since < max(80, cadence_days - 10):
            return False, f"CADENCE_DIAGNOSTIC: Next test due in {cadence_days} day cycle"

    date_iso = _bump_past_travel(state, date_iso)

    # First check if this test is already pending for the same or earlier date
    pending: List[dict] = state.setdefault("pending_tests", [])
    
    # Clean up any duplicate test types with later dates
    pending = [t for t in pending if not (t["test_type"] == test_type and 
                                         datetime.fromisoformat(t["date_iso"]) > datetime.fromisoformat(date_iso))]
    
    # Add the new test if it doesn't already exist
    if not any(t["test_type"] == test_type and t["date_iso"] == date_iso for t in pending):
        pending.append({"test_type": test_type, "date_iso": date_iso})
    
    # Update the state
    state["pending_tests"] = pending
    state["next_test_due_iso"] = date_iso
    state["next_due"] = state.get("next_due", {})
    state["next_due"]["diagnostic_test"] = date_iso
    
    # Track time for test
    track_time_commitment(state, 1.0, "diagnostic testing", date_iso)
    
    save_state(state)
    return True, f"Scheduled {test_type} for {date_iso}"

# Track time commitment function
def track_time_commitment(state: dict, hours: float, activity: str, date_iso: str):
    """Track time committed to health activities (exercise, diet, etc.)"""
    # Initialize weekly time commitment tracking if not present
    weekly = state.setdefault("weekly_time_commitment", {})
    weekly_hours = weekly.setdefault("hours", {})
    
    # Add this activity's hours
    activity_key = activity.lower().replace(" ", "_")
    weekly_hours[activity_key] = weekly_hours.get(activity_key, 0) + float(hours)
    
    # Add to total time log
    time_log = state.setdefault("time_commitment_log", [])
    time_log.append({
        "date_iso": date_iso,
        "hours": float(hours),
        "activity": activity
    })
    
    # Limit log size
    if len(time_log) > 100:
        time_log = time_log[-100:]
    
    state["time_commitment_log"] = time_log
    save_state(state)
    
    # Calculate remaining hours this week (out of 5)
    total_hours = sum(weekly_hours.values())
    remaining = max(0, 5.0 - total_hours)
    
    return True, f"Tracked {hours}h for {activity}. {total_hours:.1f}h used this week, {remaining:.1f}h remaining."

def schedule_exercise_update(state: dict, date_iso: str, reason: str = ""):
    last = state.get("last_events", {}).get("exercise_update")
    cadence_days = int(state.get("cadence", {}).get("exercise_update_days", 14))
    
    # Ensure strict 14-day exercise update cadence
    if last:
        last_dt = datetime.fromisoformat(last).date()
        when = datetime.fromisoformat(date_iso).date()
        days_since = (when - last_dt).days
        
        # Enforce biweekly cadence
        if days_since < cadence_days - 2:  # Allow slight flexibility (2 days)
            return False, f"CADENCE_EXERCISE: Updates due every {cadence_days} days"
    
    state["next_due"] = state.get("next_due", {})
    state["next_due"]["exercise_update"] = date_iso
    
    # Maintain a reasonable number of pending updates
    pe: List[dict] = state.setdefault("pending_exercise_updates", [])
    
    # Keep only the most recent 3 pending updates
    if len(pe) >= 3:
        # Sort by date, newest first
        pe.sort(key=lambda x: x.get("date_iso", ""), reverse=True)
        # Keep only the newest 2 updates
        pe = pe[:2]
    
    # Add new update if not a duplicate
    if not any(p.get("date_iso") == date_iso and p.get("reason") == reason for p in pe):
        pe.append({"date_iso": date_iso, "reason": reason})
    
    state["pending_exercise_updates"] = pe
    save_state(state)
    
    # Track time for exercise update (estimate)
    track_time_commitment(state, 0.5, "exercise planning", date_iso)
    
    return True, f"Exercise update planned for {date_iso} ({reason})"

def schedule_diet_update(state: dict, date_iso: str, reason: str = ""):
    last = state.get("last_events", {}).get("diet_update")
    cadence_days = int(state.get("cadence", {}).get("diet_update_days", 14))
    if last:
        last_dt = datetime.fromisoformat(last).date()
        when = datetime.fromisoformat(date_iso).date()
        days_since = (when - last_dt).days
        
        # Enforce biweekly cadence
        if days_since < cadence_days - 2:  # Allow slight flexibility
            return False, f"CADENCE_DIET: Updates due every {cadence_days} days"
    
    state["next_due"] = state.get("next_due", {})
    state["next_due"]["diet_update"] = date_iso
    
    # Maintain a reasonable number of pending updates
    pd: List[dict] = state.setdefault("pending_diet_updates", [])
    
    # Keep only the most recent 3 pending updates
    if len(pd) >= 3:
        # Sort by date, newest first
        pd.sort(key=lambda x: x.get("date_iso", ""), reverse=True)
        # Keep only the newest 2 updates
        pd = pd[:2]
    
    # Add new update if not a duplicate
    if not any(p.get("date_iso") == date_iso and p.get("reason") == reason for p in pd):
        pd.append({"date_iso": date_iso, "reason": reason})
    
    state["pending_diet_updates"] = pd
    save_state(state)
    
    # Track time for diet planning
    track_time_commitment(state, 0.5, "diet planning", date_iso)
    
    return True, f"Diet update planned for {date_iso} ({reason})"

def schedule_behavior_update(state: dict, date_iso: str, reason: str = ""):
    last = state.get("last_events", {}).get("behavior_update")
    cadence_days = int(state.get("cadence", {}).get("behavior_update_days", 14))
    if last:
        last_dt = datetime.fromisoformat(last).date()
        when = datetime.fromisoformat(date_iso).date()
        days_since = (when - last_dt).days
        
        # Enforce biweekly cadence
        if days_since < cadence_days - 2:
            return False, f"CADENCE_BEHAVIOR: Updates due every {cadence_days} days"
    
    state["next_due"] = state.get("next_due", {})
    state["next_due"]["behavior_update"] = date_iso
    
    # Maintain a reasonable number of pending updates
    pb: List[dict] = state.setdefault("pending_behavior_updates", [])
    
    # Keep only the most recent 3 pending updates
    if len(pb) >= 3:
        # Sort by date, newest first
        pb.sort(key=lambda x: x.get("date_iso", ""), reverse=True)
        # Keep only the newest 2 updates
        pb = pb[:2]
    
    # Add new update if not a duplicate
    if not any(p.get("date_iso") == date_iso and p.get("reason") == reason for p in pb):
        pb.append({"date_iso": date_iso, "reason": reason})
    
    state["pending_behavior_updates"] = pb
    save_state(state)
    
    # Track time for behavior changes
    track_time_commitment(state, 0.5, "behavior planning", date_iso)
    
    return True, f"Behavior update planned for {date_iso} ({reason})"

# --- Test Report auto-share (NO FILES; message-only flow) ---
def maybe_share_due_test_report(state: dict, today_iso: str) -> Optional[dict]:
    """
    If a pending test is due today, simulate Rohan sharing the report by returning a signal
    used by the orchestrator to log a 'Test report sent.' message. No PDF is created.
    """
    pending = state.get("pending_tests", [])
    if not pending:
        return None
    due = [t for t in pending if t.get("date_iso") == today_iso]
    if not due:
        return None

    # Check if this is a comprehensive panel (multiple tests on same day)
    due_date_tests = [t for t in pending if t.get("date_iso") == today_iso]
    is_comprehensive = len(due_date_tests) >= 3
    
    test = due.pop(0)
    # remove shared item(s)
    if is_comprehensive:
        # If comprehensive panel, remove all tests for this date
        state["pending_tests"] = [t for t in pending if t.get("date_iso") != today_iso]
    else:
        # Otherwise just remove the single test
        state["pending_tests"] = [t for t in pending if t is not test]

    # mark last diagnostic date
    state.setdefault("last_events", {})["diagnostic_test"] = today_iso
    
    # Track time for test-taking
    track_time_commitment(state, 1.0, "diagnostic testing", today_iso)
    
    save_state(state)

    return {"test_type": "Comprehensive Panel" if is_comprehensive else test["test_type"], "date_iso": today_iso}

# --- Exercise plan helpers (new) ---

def create_weekly_exercise_plan(state: dict, week_start_iso: str, focus: Optional[str] = None) -> Dict[str, Any]:
    """
    Create a simple weekly exercise plan with progressable elements.
    Plan structure:
      {
        "week_start": "YYYY-MM-DD",
        "exercises": [...],
        "diet_focus": "increase fiber",
        "note": "suitable for travel" (optional)
      }
    """
    f = focus or random.choice(["cardio", "strength", "mobility", "sleep", "stress"])
    plan = {"week_start": week_start_iso, "exercises": [], "diet_focus": "", "note": ""}

    if f == "cardio":
        plan["exercises"].append({"name":"Brisk walk","type":"cardio","duration_min":30,"sessions_per_week":4})
        plan["exercises"].append({"name":"Short run intervals","type":"cardio","duration_min":20,"sessions_per_week":1})
        plan["diet_focus"] = "increase whole grains and fruit"
    elif f == "strength":
        plan["exercises"].append({"name":"Bodyweight circuit","type":"strength","duration_min":20,"sessions_per_week":3})
        plan["exercises"].append({"name":"Core routine","type":"strength","duration_min":10,"sessions_per_week":2})
        plan["diet_focus"] = "increase protein at breakfast"
    elif f == "mobility":
        plan["exercises"].append({"name":"Yoga / mobility mix","type":"mobility","duration_min":25,"sessions_per_week":4})
        plan["diet_focus"] = "hydrate and monitor sodium"
    elif f == "sleep":
        plan["exercises"].append({"name":"Evening wind-down","type":"habit","duration_min":15,"sessions_per_week":7})
        plan["diet_focus"] = "sleep-supporting meals; avoid late caffeine"
    else:  # stress
        plan["exercises"].append({"name":"Mindful breathing","type":"stress","duration_min":10,"sessions_per_week":7})
        plan["diet_focus"] = "reduce stimulants; increase magnesium-rich foods"

    # travel note if travel week
    if _is_travel_date(state, week_start_iso):
        plan["note"] = "Travel-friendly: reduce duration, replace gym with hotel room bodyweight moves"
        for ex in plan["exercises"]:
            ex["duration_min"] = max(10, ex["duration_min"] // 2)
            ex["sessions_per_week"] = max(1, ex["sessions_per_week"] // 1)

    # Calculate total weekly time commitment (sessions * duration)
    total_time = sum(ex["duration_min"] * ex["sessions_per_week"] for ex in plan["exercises"]) / 60.0  # convert to hours
    
    # Track estimated weekly time
    track_time_commitment(state, total_time, "exercise plan", week_start_iso)
    
    # Add Singapore reference
    plan["note"] += " Adapted for Singapore's climate." if plan["note"] else "Plan adapted for Singapore's climate."
    
    # persist to plan history
    history = state.setdefault("plan", {}).setdefault("history", [])
    history.append(plan)
    save_state(state)
    return plan

def progress_last_plan(state: dict) -> Optional[Dict[str, Any]]:
    """
    Slightly progress the most recent plan (increase duration or sessions if adherence good).
    Returns the updated plan or None.
    """
    history = state.get("plan", {}).get("history", [])
    if not history:
        return None
    last = history[-1]
    adherence = float(state.get("member", {}).get("adherence_rate", 0.5))
    # if adherence high, modest progression
    if adherence >= 0.55:
        for ex in last.get("exercises", []):
            ex["duration_min"] = int(ex["duration_min"] * 1.1 + 0.5)
            ex["sessions_per_week"] = min(7, ex["sessions_per_week"] + (1 if random.random() < 0.3 else 0))
    elif adherence < 0.45:
        # regress slightly if poor adherence
        for ex in last.get("exercises", []):
            ex["duration_min"] = max(5, int(ex["duration_min"] * 0.9))
            ex["sessions_per_week"] = max(1, ex["sessions_per_week"] - (1 if random.random() < 0.3 else 0))
    save_state(state)
    return last