# validator.py
# Post-turn validator: forbidden actions, off-panel tests, cadence, and format/style constraints.
from datetime import datetime, timedelta
import re

FORBIDDEN_ACTIONS = {
    "surgery","inpatient_procedures","hospital_admission","chemotherapy","biopsy","organ_transplant"
}
ALLOWED_TESTS_DEFAULT = {"Lipid panel","HbA1c","CRP","Vitamin D","CBC","Comprehensive Metabolic Panel","Thyroid panel"}

def _has_persona_line(txt: str) -> bool:
    first = (txt.splitlines() or [""])[0]
    return bool(re.match(r"^\s*PERSONA:\s*(Ruby|Dr\.?\s*Warren|Advik|Carla|Rachel|Neel)\s*$", first))

def _bubble_count_ok(txt: str) -> bool:
    # Ignore PERSONA/ACTION lines; count remaining text bubbles (1–2), ~<=60 words each
    lines = [l for l in txt.splitlines() if l.strip()]
    content = [l for l in lines if not l.upper().startswith(("PERSONA:", "ACTION:"))]
    return 1 <= len(content) <= 2 and all(len(c.split()) <= 60 for c in content)

def _is_test_order_request(low_text: str) -> bool:
    """Only treat as 'ordering/scheduling a test' when there is an explicit proposal."""
    keywords = ("order", "schedule", "book", "propose", "arrange", "set up", "plan")
    has_test_word = ("test" in low_text) or ("panel" in low_text) or ("labs" in low_text)
    proposes = any(k in low_text for k in keywords) or ("action:" in low_text)
    return has_test_word and proposes

def validate_message(message: dict, state: dict) -> tuple[bool, str]:
    txt = (message.get("text") or "").strip()
    low = txt.lower()

    # Elyx format/style checks
    if message.get("speaker", "").lower().startswith("elyx") or message.get("speaker") in {"Ruby", "Dr. Warren", "Advik", "Carla", "Rachel", "Neel"}:
        if not _has_persona_line(txt):
            return False, "FORMAT: Missing or invalid PERSONA line"
        if not _bubble_count_ok(txt):
            return False, "STYLE: Too many bubbles or bubble too long"

    # Forbidden actions
    for bad in FORBIDDEN_ACTIONS:
        if bad.replace("_", " ") in low:
            return False, f"FORBIDDEN_ACTION:{bad}"

    # Panel + cadence checks ONLY when proposing/scheduling a test
    if _is_test_order_request(low):
        allowed = set(state.get("elyx_rules", {}).get("allowed_test_panel", [])) or ALLOWED_TESTS_DEFAULT
        # off-panel
        if not any(t.lower() in low for t in (x.lower() for x in allowed)):
            return False, "OFF_PANEL_TEST"
        
        # Enforce stricter quarterly cadence for diagnostics
        last_diag = state.get("last_events", {}).get("diagnostic_test")
        if last_diag:
            last_dt = datetime.fromisoformat(last_diag)
            now = datetime.fromisoformat(state["date_iso"])
            diag_interval = int(state.get("cadence", {}).get("diagnostic_interval_days", 90))
            days_since = (now - last_dt).days
            
            # Check for comprehensive panel exceptions - allow if it's a quarterly scheduled test
            is_quarterly = abs(days_since - diag_interval) < 10
            if "comprehensive" in low or "full panel" in low or "complete panel" in low:
                if is_quarterly:
                    # Allow comprehensive panel if it's around the quarterly schedule
                    pass
                elif days_since < diag_interval - 10:
                    return False, f"CADENCE_DIAGNOSTIC: Comprehensive panel due in {diag_interval}-day cycle"
            else:
                # Regular individual test cadence check
                if days_since < diag_interval - 10:
                    return False, f"CADENCE_DIAGNOSTIC: Next test due in {diag_interval}-day cycle"

    # Exercise cadence (simple heuristic on scheduling/plan)
    if ("exercise" in low) and (("update" in low) or ("plan" in low)) and ("action:" in low):
        last_ex = state.get("last_events", {}).get("exercise_update")
        if last_ex:
            last_dt = datetime.fromisoformat(last_ex)
            now = datetime.fromisoformat(state["date_iso"])
            ex_interval = int(state.get("cadence", {}).get("exercise_update_days", 14))
            days_since = (now - last_dt).days
            
            # Enforce stricter biweekly (14-day) exercise cadence
            if days_since < ex_interval - 2:  # Allow slight flexibility (2 days)
                return False, f"CADENCE_EXERCISE: Updates due every {ex_interval} days"

    # Diet update cadence check
    if ("diet" in low) and (("update" in low) or ("plan" in low)) and ("action:" in low):
        last_diet = state.get("last_events", {}).get("diet_update")
        if last_diet:
            last_dt = datetime.fromisoformat(last_diet)
            now = datetime.fromisoformat(state["date_iso"])
            diet_interval = int(state.get("cadence", {}).get("diet_update_days", 14))
            days_since = (now - last_dt).days
            
            if days_since < diet_interval - 2:  # Allow slight flexibility (2 days)
                return False, f"CADENCE_DIET: Updates due every {diet_interval} days"

    # Check for mismatched action types that often cause errors
    if "action:" in low:
        # Common error: diet_update instead of schedule_diet_update
        if ("diet_update" in low) and not ("schedule_diet_update" in low):
            return False, "ACTION_TYPE: Use schedule_diet_update instead of diet_update"
            
        # Check for behavior_update instead of schedule_behavior_update
        if ("behavior_update" in low) and not ("schedule_behavior_update" in low):
            return False, "ACTION_TYPE: Use schedule_behavior_update instead of behavior_update"

    return True, "OK"