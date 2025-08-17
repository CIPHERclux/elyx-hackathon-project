# engine/orchestrator.py
import os
import uuid
import json
import datetime
import signal
import sys
import random
import re
import traceback
from pathlib import Path
from typing import List, Tuple, Optional, Dict

from engine.state import load_state, save_state, advance_day
from engine.kpi_drift import apply_kpi_drift
from engine.validator import validate_message
from engine.summarizer import extract_daily_decisions, summarize_week
from engine.sentiment import track_persona_sentiment
from engine.prompts import (
    ELYX_SYSTEM, ELYX_DEV_TEMPLATE,
    ROHAN_SYSTEM, ROHAN_DEV_TEMPLATE,
    DISPLAY_NAME, ALLOWED_PERSONAS, PERSONA_ROTATION, QUARTERLY_SPECIALISTS,
    EXERCISE_TEMPLATES, DIAGNOSTIC_TEMPLATES, CADENCE_SYSTEM_TEMPLATES
)
from engine.clients.universal_client import call_llm
from engine.rate_limit import RateLimiter
from engine.tools import (
    propose_test, schedule_exercise_update, schedule_diet_update, schedule_behavior_update,
    maybe_share_due_test_report, create_weekly_exercise_plan, progress_last_plan,
    propose_comprehensive_panel, track_time_commitment
)

# === Exports & run numbering ===
EXPORT_DIR = Path("data/exports")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

def _next_run_id() -> int:
    existing = [f for f in os.listdir(EXPORT_DIR) if f.startswith("run") and f.endswith(".json")]
    nums = []
    for f in existing:
        try:
            nums.append(int(f.replace("run", "").replace(".json", "")))
        except ValueError:
            pass
    return max(nums) + 1 if nums else 1

RUN_ID = _next_run_id()
JSON_PATH = EXPORT_DIR / f"run{RUN_ID}.json"
DIARY_PATH = EXPORT_DIR / f"run{RUN_ID}_diary.txt"

# Open diary file once at start; we flush after every write (crash-safe)
diary_file = open(DIARY_PATH, "w", encoding="utf-8")


# -------------------
# Helpers & config
# -------------------
def _msg_id() -> str:
    return f"msg_{uuid.uuid4().hex[:8]}"

def _fmt_whatsapp_stamp(date_iso: str, hour: int, minute: int) -> str:
    """Formats like [6/4/25, 9:05 AM] using ONLY the simulation date."""
    dt = datetime.date.fromisoformat(date_iso)
    m = dt.month
    d = dt.day
    y = dt.year % 100
    am_pm = "AM" if hour < 12 else "PM"
    hr12 = hour % 12
    if hr12 == 0:
        hr12 = 12
    return f"[{m}/{d}/{y}, {hr12}:{minute:02d} {am_pm}]"

def _turn(date_iso: str, hour: int, minute: int, speaker_key: str, text: str, turn_group: int):
    ts = _fmt_whatsapp_stamp(date_iso, hour, minute)
    speaker_label = DISPLAY_NAME.get(speaker_key, speaker_key)
    # Make sure text is a single string
    if text is None:
        text = ""
    
    # FIXED: Ensure text doesn't contain developer/system debug info
    # Check for developer prompts or system instructions that might leak
    if "[SYSTEM]" in text or "[DEVELOPER]" in text:
        # Only keep text after the last occurrence of system/developer sections
        sections = re.split(r'\[(SYSTEM|DEVELOPER)\]', text)
        if len(sections) > 1:
            text = sections[-1].strip()
    
    # Also check for state JSON leakage
    if '"date_iso":' in text and '"member":' in text and '"kpis":' in text:
        # Likely a state JSON leak - return generic message instead
        text = "Sorry, I'm having a bit of technical difficulty. Let me try again."
    
    line = f"{ts} {speaker_label}: {text}\n"
    diary_file.write(line)
    diary_file.flush()
    return {"id": _msg_id(), "ts": ts, "speaker": speaker_label, "turn_group": turn_group, "text": text}

def _recent_messages(chat: List[dict], n: int = 6) -> List[dict]:
    return chat[-n:]

def _display(messages: List[dict]) -> str:
    return "\n".join([f'{m["speaker"]}: {m["text"]}' for m in messages])


# -------------------
# Persona rotation & cadence helpers (A, B, F)
# -------------------

def _forced_persona_for_week(week_idx: int) -> str:
    return PERSONA_ROTATION[week_idx % len(PERSONA_ROTATION)]

def _specialist_due(global_day_index: int) -> Optional[str]:
    # every 90 days (approx. quarterly) prefer a specialist
    if global_day_index > 0 and global_day_index % 90 == 0:
        return QUARTERLY_SPECIALISTS[(global_day_index // 90) % len(QUARTERLY_SPECIALISTS)]
    return None

def _plan_update_due(global_day_index: int) -> bool:
    return global_day_index > 0 and global_day_index % 14 == 0

def _choose_initiator(rng: random.Random) -> str:
    # 60% Rohan starts, 40% Elyx starts — preserves Rohan as primary but adds variety
    return "Rohan" if rng.random() < 0.60 else "Elyx"

def _elyx_starter_name(week_idx: int, global_day_index: int) -> str:
    sp = _specialist_due(global_day_index)
    return sp if sp else _forced_persona_for_week(week_idx)


# -------------------
# Timing helpers (E)
# -------------------
def _day_chat_times(turns_per_day: int, rng: random.Random) -> List[Tuple[int,int]]:
    """
    Generate 1-2 times per day with light jitter (no fixed seed).
    Returns list of (hour, minute) tuples.
    """
    times = []
    # morning slot
    h = rng.choice([7,8,9,10])
    m = rng.choice([0,3,5,8,10,12,15,18,20,25,30])
    times.append((h, m))
    # second morning bubble close to first
    times.append((h, min(59, m + rng.choice([2,3,4,5,6,7,8,9]))))
    if turns_per_day >= 2:
        h2 = rng.choice([18,19,20,21])
        m2 = rng.choice([0,5,10,15,20,25,30,35,40,45,50,55])
        times.extend([(h2, m2), (h2, min(59, m2 + rng.choice([2,3,4,5])) )])
    return times[: max(2, 2 * turns_per_day)]


# -------------------
# Robust parsing & persona helpers (fixes)
# -------------------
PERSONA_REGEX = re.compile(r'PERSONA\s*:\s*([A-Za-z0-9 .\'-]+)', re.IGNORECASE)
ACTION_REGEX = re.compile(r'ACTION\s*:\s*(\{.*\})', re.IGNORECASE | re.DOTALL)
LEADING_SPEAKER_PERSONA_LINE = re.compile(r'^[A-Za-z .()]+:\s*PERSONA\s*:\s*.*$', re.IGNORECASE | re.MULTILINE)

def _parse_persona_and_action(elyx_text: str) -> Tuple[Optional[str], Optional[dict]]:
    """
    More robust persona + action extractor:
    - Finds PERSONA anywhere in the text (not only at the start of a line).
    - Finds ACTION JSON anywhere and parses it.
    """
    persona = None
    action = None
    if not elyx_text:
        return None, None

    # Persona: find first occurrence
    m = PERSONA_REGEX.search(elyx_text)
    if m:
        persona = m.group(1).strip()

    # Action: try to find JSON blob after ACTION:
    m2 = ACTION_REGEX.search(elyx_text)
    if m2:
        raw = m2.group(1).strip()
        try:
            action = json.loads(raw)
        except Exception:
            # fallback: try to sanitize common weird quotes
            raw_clean = raw.replace("'", '"')
            try:
                action = json.loads(raw_clean)
            except Exception:
                action = None

    return persona, action

def _sanitize_persona(raw: Optional[str]) -> Optional[str]:
    """
    Convert varieties like 'ruby (concierge)' or 'RUBY' to exact allowed persona name,
    else return None.
    """
    if not raw:
        return None
    s = raw.strip()
    # try exact match (case-insensitive)
    for p in ALLOWED_PERSONAS:
        if s.lower() == p.lower():
            return p
    # try containment
    sl = s.lower()
    for p in ALLOWED_PERSONAS:
        if p.lower() in sl or sl in p.lower():
            return p
    # try first token match
    token = s.split()[0].capitalize()
    if token in ALLOWED_PERSONAS:
        return token
    return None

def _route_by_topic(prompt_text: str) -> Optional[str]:
    """
    Lightweight keyword router to force specialists when member asks about topic.
    """
    if not prompt_text:
        return None
    t = prompt_text.lower()
    # diagnostics / labs => Dr. Warren
    if any(k in t for k in ["crp", "lipid", "lipids", "lipid panel", "panel", "labs", "test report", "lab results", "test results", "report sent", "blood panel", "a1c", "hba1c"]):
        return "Dr. Warren"
    # wearables / HRV => Advik
    if any(k in t for k in ["hrv", "whoop", "garmin", "wearable", "heart rate variability", "hr zone", "zones", "hrv", "recovery"]):
        return "Advik"
    # diet / supplements => Carla
    if any(k in t for k in ["diet", "magnesium", "supplement", "omega", "nutrition", "food", "calories", "protein", "carb", "fats"]):
        return "Carla"
    # exercise / mobility => Rachel
    if any(k in t for k in ["exercise", "workout", "mobility", "strength", "pt", "training", "gym", "run", "zone 2"]):
        return "Rachel"
    # escalation / strategy => Neel
    if any(k in t for k in ["frustrat", "escalat", "strategy", "qbr", "value", "complaint", "lead"]):
        return "Neel"
    return None

def _choose_persona(parsed_persona: Optional[str], route_persona: Optional[str], week_idx: int, global_day_index: int, recent_all_chats: List[dict], rng: random.Random) -> str:
    """
    Decide which persona to use for this Elyx reply:
    - If route_persona (strong topic match): force that persona.
    - Else if parsed_persona present and allowed: usually use it but apply Ruby-overuse override.
    - Else fall back to weekly starter (rotation/specialist).
    """
    # routing has highest priority
    if route_persona:
        return route_persona

    persona = _sanitize_persona(parsed_persona) if parsed_persona else None
    default = _elyx_starter_name(week_idx, global_day_index)

    # if no parsed persona -> default scheduled persona
    if not persona:
        return default

    # if persona is Ruby, avoid Ruby monopoly: if last 3 Elyx replies were Ruby then 50% override
    if persona == "Ruby":
        # count recent Elyx speaker labels that are Ruby
        last_elyx = [m for m in recent_all_chats[::-1] if m.get("speaker") in ALLOWED_PERSONAS]
        ruby_count = sum(1 for m in last_elyx[:6] if m.get("speaker") == "Ruby")
        if ruby_count >= 2 and rng.random() < 0.5:
            # pick an alternative persona — prefer weekly rotation then specialists
            pool = [p for p in PERSONA_ROTATION + QUARTERLY_SPECIALISTS if p != "Ruby"]
            return rng.choice(pool)
    # otherwise accept the model's persona
    return persona

def _clean_elyx_text(raw_text: str) -> str:
    """
    Remove persona declaration lines and leading 'Name: PERSONA:' constructs
    so the visible message is clean WhatsApp bubbles.
    Keep ACTION lines (so they remain visible) because they are part of conversation content.
    """
    if not raw_text:
        return ""
    # remove leading 'Name: PERSONA: ...' lines (whole line)
    cleaned = LEADING_SPEAKER_PERSONA_LINE.sub('', raw_text)
    # remove standalone PERSONA: lines
    cleaned = PERSONA_REGEX.sub('', cleaned)
    # strip only leading/trailing whitespace and any leftover empty lines
    cleaned_lines = [l for l in cleaned.splitlines() if l.strip() != ""]
    return "\n".join(cleaned_lines).strip()

# -------------------
# Apply action (unchanged style but clearer)
# -------------------
def _apply_action_to_state(state, action, date_iso: str, tg: int):
    """
    Apply action with guardrails and simulate member adherence.
    Returns tuple (applied_bool, message, followed_bool).
    """
    if not action or "type" not in action:
        return False, None, False

    # FIXED: Seed the RNG with consistent values for more predictable adherence
    action_rng = random.Random(hash((date_iso, action.get("type", ""), tg)))
    
    typ = action.get("type")
    if typ == "propose_test":
        ok, msg = propose_test(state, action.get("test_type", ""), action.get("date_iso", date_iso))
        if not ok:
            return False, msg, False
    elif typ == "propose_comprehensive_panel":  # NEW: Added support for comprehensive panel
        ok, msg = propose_comprehensive_panel(state, action.get("date_iso", date_iso))
        if not ok:
            return False, msg, False
    elif typ == "schedule_exercise_update":
        ok, msg = schedule_exercise_update(state, action.get("date_iso", date_iso), action.get("reason", ""))
        if not ok:
            return False, msg, False
    elif typ == "schedule_diet_update":
        ok, msg = schedule_diet_update(state, action.get("date_iso", date_iso), action.get("reason", ""))
        if not ok:
            return False, msg, False
    elif typ == "schedule_behavior_update":
        ok, msg = schedule_behavior_update(state, action.get("date_iso", date_iso), action.get("reason", ""))
        if not ok:
            return False, msg, False
    elif typ == "track_time_commitment":  # NEW: Added support for time tracking
        hours = action.get("hours", 0)
        activity = action.get("activity", "exercise")
        ok, msg = track_time_commitment(state, hours, activity, date_iso)
        return True, msg, True
    else:
        return False, "UNKNOWN_ACTION_TYPE", False

    # Simulate whether member actually follows through with the plan
    adherence = float(state.get("member", {}).get("adherence_rate", 0.5))
    
    # FIXED: Make adherence more predictable by using a specific action-based seed
    follows = action_rng.random() < adherence
    
    if not follows:
        # increment state non-follow telemetry
        state["recent_non_follow_events"] = state.get("recent_non_follow_events", 0) + 1
        save_state(state)
        return False, "MEMBER_DID_NOT_FOLLOW_PLAN", False

    # If followed, apply immediate state updates where relevant
    if typ == "schedule_exercise_update":
        state.setdefault("last_events", {})["exercise_update"] = action.get("date_iso", date_iso)
    if typ == "schedule_diet_update":
        state.setdefault("last_events", {})["diet_update"] = action.get("date_iso", date_iso)
    if typ == "schedule_behavior_update":
        state.setdefault("last_events", {})["behavior_update"] = action.get("date_iso", date_iso)

    save_state(state)
    return True, "APPLIED", True

# -------------------
# Simulation runner (main)
# -------------------
def _week_start_iso(start_utc: datetime.datetime, w: int) -> str:
    return (start_utc.date() + datetime.timedelta(weeks=w)).isoformat()

# This is just the relevant section from orchestrator.py to fix the weekly member budget

def _weekly_member_budget(state) -> int:
    """
    Set a strict limit of maximum 5 conversations per week initiated by the member.
    Returns the number of allowed conversations this week.
    """
    # Enforce strict maximum of 5 conversations per week
    try:
        max_weekly = int(state.get("cadence", {}).get("max_curiosity_chats_per_week", 5))
    except Exception:
        max_weekly = 5
    
    # Hard cap at 5 regardless of what's in the state
    return min(5, max_weekly)

def _apply_action_to_state(state, action, date_iso: str, tg: int):
    """
    Apply action with guardrails and simulate member adherence.
    Returns tuple (applied_bool, message, followed_bool).
    """
    if not action or "type" not in action:
        return False, None, False

    # Seed the RNG with consistent values for more predictable adherence
    action_rng = random.Random(hash((date_iso, action.get("type", ""), tg)))
    
    typ = action.get("type")
    if typ == "propose_test":
        ok, msg = propose_test(state, action.get("test_type", ""), action.get("date_iso", date_iso))
        if not ok:
            return False, msg, False
    elif typ == "propose_comprehensive_panel":
        ok, msg = propose_comprehensive_panel(state, action.get("date_iso", date_iso))
        if not ok:
            return False, msg, False
    elif typ == "schedule_exercise_update":
        ok, msg = schedule_exercise_update(state, action.get("date_iso", date_iso), action.get("reason", ""))
        if not ok:
            return False, msg, False
    elif typ == "schedule_diet_update":
        ok, msg = schedule_diet_update(state, action.get("date_iso", date_iso), action.get("reason", ""))
        if not ok:
            return False, msg, False
    elif typ == "schedule_behavior_update":
        ok, msg = schedule_behavior_update(state, action.get("date_iso", date_iso), action.get("reason", ""))
        if not ok:
            return False, msg, False
    elif typ == "track_time_commitment":
        hours = action.get("hours", 0)
        activity = action.get("activity", "exercise")
        ok, msg = track_time_commitment(state, hours, activity, date_iso)
        return True, msg, True
    else:
        return False, "UNKNOWN_ACTION_TYPE", False

    # Simulate whether member actually follows through with the plan
    adherence = float(state.get("member", {}).get("adherence_rate", 0.5))
    
    # Make adherence more predictable by using a specific action-based seed
    follows = action_rng.random() < adherence
    
    if not follows:
        # increment state non-follow telemetry
        state["recent_non_follow_events"] = state.get("recent_non_follow_events", 0) + 1
        save_state(state)
        return False, "MEMBER_DID_NOT_FOLLOW_PLAN", False

    # If followed, apply immediate state updates where relevant
    if typ == "schedule_exercise_update":
        state.setdefault("last_events", {})["exercise_update"] = action.get("date_iso", date_iso)
    if typ == "schedule_diet_update":
        state.setdefault("last_events", {})["diet_update"] = action.get("date_iso", date_iso)
    if typ == "schedule_behavior_update":
        state.setdefault("last_events", {})["behavior_update"] = action.get("date_iso", date_iso)

    save_state(state)
    return True, "APPLIED", True

def _maybe_rohan_self_report(state) -> bool:
    # improved probabilities to add more varied self-reports
    adherence = float(state.get("member", {}).get("adherence_rate", 0.5))
    prob = 0.25 if adherence >= 0.5 else 0.35
    return random.random() < prob

def run_simulation():
    state = load_state()
    state["run_id"] = RUN_ID
    limiter = RateLimiter(rpm=int(os.getenv("LLM_RPM", "6")))
    max_weeks = int(os.getenv("MAX_WEEKS", "16"))
    turns_per_day = int(os.getenv("TURNS_PER_DAY", "1"))

    all_chats: List[dict] = []
    weekly_summaries: List[dict] = []
    weekly_effect_hints: List[dict] = []
    start_utc = datetime.datetime.utcnow()

    # track cadence/system notes we've already printed to avoid spammy repeats
    printed_cadence_notes = set()

    def export_partial():
        payload = {
            "run_id": RUN_ID,
            "diary_path": str(DIARY_PATH),
            "state": state,
            "chats": all_chats,
            "weekly_summaries": weekly_summaries,
        }
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def _on_signal(_sig, _frm):
        export_partial()
        diary_file.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    # track absolute day index across whole sim (for cadence)
    global_day_index = 0

    # NEW: Schedule initial diagnostic panel at simulation start
    if not state.get("last_events", {}).get("diagnostic_test"):
        start_date = state["date_iso"]
        start_date_obj = datetime.date.fromisoformat(start_date)
        first_panel_date = (start_date_obj + datetime.timedelta(days=14)).isoformat()
        propose_comprehensive_panel(state, first_panel_date)
        
    # Add crash protection wrapper
    try:
        # ---- weekly loop ----
        for w in range(max_weeks):
            # NEW: Reset member conversation budget for the week
            member_budget = _weekly_member_budget(state)
            member_initiated_this_week = 0
            
            week_log: List[dict] = []
            all_decisions: List[dict] = []
            persona_state_week = state.get("persona_snapshot", {"trust":55,"engagement":52,"frustration":22})

            # NEW: Check if quarterly diagnostic is due
            today = state["date_iso"]
            today_date = datetime.date.fromisoformat(today)
            last_diag = state.get("last_events", {}).get("diagnostic_test")
            if last_diag:
                last_diag_date = datetime.date.fromisoformat(last_diag)
                days_since = (today_date - last_diag_date).days
                diag_interval = state.get("cadence", {}).get("diagnostic_interval_days", 90)
                if days_since >= diag_interval - 7:  # Schedule a week before due
                    next_diag_date = (last_diag_date + datetime.timedelta(days=diag_interval)).isoformat()
                    if next_diag_date <= today:
                        # It's already due
                        propose_comprehensive_panel(state, today)
                    else:
                        # Schedule it for the future due date
                        propose_comprehensive_panel(state, next_diag_date)

            # Weekly "system refresh" marker (JSON only)
            all_chats.append({
                "id": _msg_id(),
                "ts": f"[{state['date_iso']}]",
                "speaker": "System",
                "turn_group": -1,
                "text": f"[SYSTEM_REFRESH] Week {w+1} context reset"
            })

            # NEW: Reference Singapore in weekly system message for JSON only
            all_chats.append({
                "id": _msg_id(),
                "ts": f"[{state['date_iso']}]",
                "speaker": "System",
                "turn_group": -1,
                "text": f"[SYSTEM_NOTE] Member Rohan Patel is based in Singapore, currently managing hypertension."
            })

            # create or progress a weekly plan at the start of the week
            week_start_iso = state["date_iso"]
            if not state.get("plan", {}).get("history"):
                plan = create_weekly_exercise_plan(state, week_start_iso)
            else:
                plan = create_weekly_exercise_plan(state, week_start_iso, focus=None)
                progress_last_plan(state)

            travel_week = state["date_iso"] in set(state.get("member", {}).get("travel_weeks", []))

            # ---- daily loop (7 days) ----
            for day in range(7):
                date_iso = state["date_iso"]
                tg = len(all_chats) + 1

                # use a day-specific RNG to keep variance reproducible if needed
                day_rng = random.Random(hash((state["date_iso"], RUN_ID, w, day)))

                times = _day_chat_times(turns_per_day, day_rng)

                # Decide who initiates today (C)
                # NEW: Respect member conversation budget
                if member_initiated_this_week >= member_budget:
                    initiator = "Elyx"  # Force Elyx to start if budget exceeded
                else:
                    initiator = _choose_initiator(day_rng)

                rohan_msg = None

                # If Rohan starts (most of the time)
                if initiator == "Rohan":
                    limiter.wait()
                    member_initiated_this_week += 1  # NEW: Count this conversation
                    
                    pick = day_rng.random()
                    if travel_week and day_rng.random() < 0.6:
                        rohan_prompt = "Open today's chat mentioning travel constraints."
                    elif pick < 0.45:
                        rohan_prompt = "Ask a health research question (diet, supplements, sleep, exercise optimization)."
                    elif pick < 0.7 and _maybe_rohan_self_report(state):
                        rohan_prompt = "Adherence self-report: I missed my sessions, explain why and ask for alternate plan."
                    else:
                        rohan_prompt = "Open today's chat."

                    # NEW: Add Singapore references to the developer context
                    rohan_dev = ROHAN_DEV_TEMPLATE.format(
                        mood=day_rng.choice(["motivated", "curious", "tired", "frustrated"]),
                        state_json=json.dumps(state, indent=2, ensure_ascii=False),
                        recent_messages=_display(_recent_messages(all_chats, 6)),
                        # New context with Singapore references
                        location_context="You are based in Singapore, and sometimes refer to local weather/time/places. Your hypertension management is affected by the local climate and frequent travel between time zones."
                    )

                    # inject mood for variety (D)
                    mood = day_rng.choice(["motivated", "curious", "tired", "frustrated"])
                    rohan_txt = call_llm(
                        [{"role": "user", "content": rohan_prompt}],
                        system=ROHAN_SYSTEM,
                        developer=rohan_dev,
                        max_tokens=160,
                    )
                    # Clean up Rohan output (strip accidental self-address variants)
                    rohan_txt_clean = re.sub(r'^\s*(hi|hello|hey)[, ]+rohan[,!:]?\s*', '', rohan_txt, flags=re.IGNORECASE | re.MULTILINE).strip()
                    rohan_txt_clean = re.sub(r'^\s*rohan[,!:]\s*', '', rohan_txt_clean, flags=re.IGNORECASE | re.MULTILINE).strip()
                    
                    # FIXED: Additional sanitization to prevent system prompts leaking
                    if "[SYSTEM]" in rohan_txt_clean or "[DEVELOPER]" in rohan_txt_clean:
                        sections = re.split(r'\[(SYSTEM|DEVELOPER)\]', rohan_txt_clean)
                        if len(sections) > 1:
                            rohan_txt_clean = sections[-1].strip()
                    
                    rohan_msg = _turn(date_iso, *times[0], "Rohan", rohan_txt_clean, tg)
                    week_log.append(rohan_msg); all_chats.append(rohan_msg)

                # If Elyx starts (proactive day) OR Elyx replies
                limiter.wait()
                elyx_persona_default = _elyx_starter_name(w, global_day_index)
                
                # NEW: Add time commitment info to context
                weekly_time = state.get("weekly_time_commitment", {})
                time_spent = sum(weekly_time.get("hours", {}).values())
                time_remaining = max(0, 5 - time_spent)
                
                # NEW: Updated dev template with Singapore and time commitment context
                elyx_dev = ELYX_DEV_TEMPLATE.format(
                    state_json=json.dumps(state, indent=2, ensure_ascii=False),
                    recent_messages=_display(_recent_messages(all_chats, 6)),
                    sentiment=f"trust={persona_state_week.get('trust')},engagement={persona_state_week.get('engagement')},frustration={persona_state_week.get('frustration')}",
                    travel_note = ("NOTE: member traveling this week" if travel_week else ""),
                    location_context = "Member is based in Singapore, managing hypertension. Consider local context in recommendations.",
                    time_note = f"Member commits ~5 hours/week to health plan. ~{time_spent:.1f}h used this week, ~{time_remaining:.1f}h remaining."
                )

                prompt_for_elyx = rohan_msg["text"] if rohan_msg else "Start today's proactive check-in focusing on plan progress and any due cadence items."

                elyx_txt = call_llm(
                    [{"role": "user", "content": prompt_for_elyx}],
                    system=ELYX_SYSTEM,
                    developer=elyx_dev,
                    max_tokens=280,
                )

                # Parse persona/action from raw LLM output
                parsed_persona, action = _parse_persona_and_action(elyx_txt)
                parsed_persona = _sanitize_persona(parsed_persona)
                # topic routing (strong preference)
                route_persona = _route_by_topic(prompt_for_elyx if rohan_msg else prompt_for_elyx)
                # choose final persona with diversity override
                elyx_speaker_key = _choose_persona(parsed_persona, route_persona, w, global_day_index, all_chats, day_rng) or elyx_persona_default

                # --- IMPORTANT: validate against the RAW LLM output (which still contains PERSONA/ACTION lines).
                # Keep a copy for validation, but create a cleaned version for diary display.
                elyx_raw_for_validation = {
                    "id": _msg_id(),
                    "ts": f"[{date_iso}]",
                    "speaker": elyx_speaker_key,
                    "turn_group": tg,
                    "text": elyx_txt  # raw LLM text (contains PERSONA: and ACTION:)
                }

                # Validate the raw text (so format/cadence checks see PERSONA/ACTION as PS requires)
                valid, why = validate_message(elyx_raw_for_validation, state)

                # Now create the diary-visible message by removing the PERSONA meta-lines
                elyx_clean_text = _clean_elyx_text(elyx_txt)
                
                # FIXED: Extra sanitization to catch any leaked debugging output
                if "[SYSTEM]" in elyx_clean_text or "[DEVELOPER]" in elyx_clean_text:
                    sections = re.split(r'\[(SYSTEM|DEVELOPER)\]', elyx_clean_text)
                    if len(sections) > 1:
                        elyx_clean_text = sections[-1].strip()
                
                elyx_msg = _turn(date_iso, *times[1], elyx_speaker_key, elyx_clean_text, tg)
                week_log.append(elyx_msg); all_chats.append(elyx_msg)

                # Continue handling validation result (friendly, not spammy)
                if not valid:
                    if why and str(why).upper().startswith("CADENCE"):
                        key = f"{date_iso}:{why}"
                        if key not in printed_cadence_notes:
                            printed_cadence_notes.add(key)
                            note = day_rng.choice(CADENCE_SYSTEM_TEMPLATES).format(date=date_iso)
                            sys_msg = _turn(date_iso, *times[1], "System", note, tg)
                            week_log.append(sys_msg); all_chats.append(sys_msg)
                        # suppress raw rejection spam
                    else:
                        sys_msg = _turn(date_iso, *times[1], "System", f"[Action rejected] {why}", tg)
                        week_log.append(sys_msg); all_chats.append(sys_msg)
                else:
                    if action:
                        ok, msg, followed = _apply_action_to_state(state, action, date_iso, tg)
                        if not ok and msg:
                            if msg == "MEMBER_DID_NOT_FOLLOW_PLAN":
                                sys_msg = _turn(date_iso, *times[1], "System", "[Action not followed] Member did not adhere to the proposed plan.", tg)
                                week_log.append(sys_msg); all_chats.append(sys_msg)
                            else:
                                if msg and str(msg).upper().startswith("CADENCE"):
                                    key = f"{date_iso}:{msg}"
                                    if key not in printed_cadence_notes:
                                        printed_cadence_notes.add(key)
                                        note = day_rng.choice(CADENCE_SYSTEM_TEMPLATES).format(date=date_iso)
                                        sys_msg = _turn(date_iso, *times[1], "System", note, tg)
                                        week_log.append(sys_msg); all_chats.append(sys_msg)
                                else:
                                    sys_msg = _turn(date_iso, *times[1], "System", f"[Action rejected] {msg}", tg)
                                    week_log.append(sys_msg); all_chats.append(sys_msg)
                        elif ok and msg:
                            sys_msg = _turn(date_iso, *times[1], "System", f"[Action applied] {msg}", tg)
                            week_log.append(sys_msg); all_chats.append(sys_msg)

                # Evening follow-up if configured (unchanged pattern, still uses elyx dev template with sentiment)
                if turns_per_day >= 2:
                    limiter.wait()
                    rohan_txt2 = call_llm(
                        [{"role": "user", "content": "Evening follow-up based on earlier chat."}],
                        system=ROHAN_SYSTEM,
                        developer=rohan_dev,  # NEW: Using updated dev template with Singapore references
                        max_tokens=120,
                    )
                    # clean possible self-address variants
                    rohan_txt2_clean = re.sub(r'^\s*(hi|hello|hey)[, ]+rohan[,!:]?\s*', '', rohan_txt2, flags=re.IGNORECASE | re.MULTILINE).strip()
                    rohan_txt2_clean = re.sub(r'^\s*rohan[,!:]\s*', '', rohan_txt2_clean, flags=re.IGNORECASE | re.MULTILINE).strip()
                    
                    # FIXED: Additional sanitization for system/developer prompts
                    if "[SYSTEM]" in rohan_txt2_clean or "[DEVELOPER]" in rohan_txt2_clean:
                        sections = re.split(r'\[(SYSTEM|DEVELOPER)\]', rohan_txt2_clean)
                        if len(sections) > 1:
                            rohan_txt2_clean = sections[-1].strip()
                    
                    rohan_eve = _turn(date_iso, *times[-2], "Rohan", rohan_txt2_clean, tg)
                    week_log.append(rohan_eve); all_chats.append(rohan_eve)

                    limiter.wait()
                    elyx_txt2 = call_llm(
                        [{"role": "user", "content": rohan_txt2_clean}],
                        system=ELYX_SYSTEM,
                        developer=elyx_dev,  # NEW: Using updated dev template
                        max_tokens=220,
                    )
                    parsed_persona2, action2 = _parse_persona_and_action(elyx_txt2)
                    parsed_persona2 = _sanitize_persona(parsed_persona2)
                    route_persona2 = _route_by_topic(rohan_txt2_clean)
                    elyx_speaker_key2 = _choose_persona(parsed_persona2, route_persona2, w, global_day_index, all_chats, day_rng) or _elyx_starter_name(w, global_day_index)
                    elyx_eve_text = _clean_elyx_text(elyx_txt2)
                    
                    # FIXED: Extra sanitization for system/developer prompts
                    if "[SYSTEM]" in elyx_eve_text or "[DEVELOPER]" in elyx_eve_text:
                        sections = re.split(r'\[(SYSTEM|DEVELOPER)\]', elyx_eve_text)
                        if len(sections) > 1:
                            elyx_eve_text = sections[-1].strip()
                    
                    elyx_eve = _turn(date_iso, *times[-1], elyx_speaker_key2, elyx_eve_text, tg)
                    week_log.append(elyx_eve); all_chats.append(elyx_eve)
                    valid2, why2 = validate_message({
                        "id": _msg_id(),
                        "ts": f"[{date_iso}]",
                        "speaker": elyx_speaker_key2,
                        "turn_group": tg,
                        "text": elyx_txt2
                    }, state)
                    if not valid2:
                        if why2 and str(why2).upper().startswith("CADENCE"):
                            key = f"{date_iso}:{why2}"
                            if key not in printed_cadence_notes:
                                printed_cadence_notes.add(key)
                                note = day_rng.choice(CADENCE_SYSTEM_TEMPLATES).format(date=date_iso)
                                sys_msg2 = _turn(date_iso, *times[-1], "System", note, tg)
                                week_log.append(sys_msg2); all_chats.append(sys_msg2)
                        else:
                            sys_msg2 = _turn(date_iso, *times[-1], "System", f"[Action rejected] {why2}", tg)
                            week_log.append(sys_msg2); all_chats.append(sys_msg2)
                    elif action2:
                        ok2, msg2, followed2 = _apply_action_to_state(state, action2, date_iso, tg)
                        if not ok2 and msg2:
                            if msg2 == "MEMBER_DID_NOT_FOLLOW_PLAN":
                                sys_msg2 = _turn(date_iso, *times[-1], "System", "[Action not followed] Member did not adhere to the proposed plan.", tg)
                                week_log.append(sys_msg2); all_chats.append(sys_msg2)
                            else:
                                if msg2 and str(msg2).upper().startswith("CADENCE"):
                                    key = f"{date_iso}:{msg2}"
                                    if key not in printed_cadence_notes:
                                        printed_cadence_notes.add(key)
                                        note = day_rng.choice(CADENCE_SYSTEM_TEMPLATES).format(date=date_iso)
                                        sys_msg2 = _turn(date_iso, *times[-1], "System", note, tg)
                                        week_log.append(sys_msg2); all_chats.append(sys_msg2)
                                else:
                                    sys_msg2 = _turn(date_iso, *times[-1], "System", f"[Action rejected] {msg2}", tg)
                                    week_log.append(sys_msg2); all_chats.append(sys_msg2)
                        elif ok2 and msg2:
                            sys_msg2 = _turn(date_iso, *times[-1], "System", f"[Action applied] {msg2}", tg)
                            week_log.append(sys_msg2); all_chats.append(sys_msg2)

                # Daily decisions + sentiment (G)
                daily = extract_daily_decisions(state["date_iso"], week_log[-8:])
                if daily.get("decisions"):
                    all_decisions.extend(daily["decisions"])
                persona_state_week = track_persona_sentiment(week_log[-8:])
                state["persona_snapshot"] = persona_state_week
                save_state(state)

                # If any diagnostic is due *today*, simulate Rohan sharing the report (message-only).
                shared = maybe_share_due_test_report(state, date_iso)
                if shared:
                    msg = _turn(date_iso, 10, 5, "Rohan", "Test report sent.", tg)
                    week_log.append(msg); all_chats.append(msg)

                # Collect daily hints for KPI drift (keeps previous semantics)
                weekly_effect_hints.append({
                    "date_iso": date_iso,
                    "travel": travel_week,
                    "adherence": state.get("member", {}).get("adherence_rate", 0.5),
                    "non_follow": state.get("recent_non_follow_events", 0)
                })

                # advance simulation date
                state = advance_day(state)
                global_day_index += 1

            # --- Weekly wrap ---
            state = apply_kpi_drift(state, weekly_effect_hints)
            week_summary = summarize_week(_week_start_iso(start_utc, w), [], persona_state_week, state)
            weekly_summaries.append(week_summary)

            # ENFORCE CADENCE: if any plan/test due at week boundary, schedule programmatically (B)
            today = state["date_iso"]
            
            # NEW: Reset weekly time commitment tracking
            state.setdefault("weekly_time_commitment", {})
            state["weekly_time_commitment"]["hours"] = {}
            
            # plan updates
            for key, fn in [
                ("next_exercise_update_due_iso", schedule_exercise_update),
                ("next_diet_update_due_iso", schedule_diet_update),
                ("next_behavior_update_due_iso", schedule_behavior_update),
            ]:
                if state.get(key) and state[key] <= today:
                    ok, msg = fn(state, today, reason="cadence due")
                    all_chats.append({"id": _msg_id(), "ts": f"[{today}]", "speaker": "System", "turn_group": -1, "text": f"[CADENCE_APPLIED] {msg}"})
                    
            # diagnostics
            if state.get("next_test_due_iso") and state["next_test_due_iso"] <= today:
                # NEW: Always propose comprehensive panel for quarterly diagnostics
                ok, msg = propose_comprehensive_panel(state, today)
                all_chats.append({"id": _msg_id(), "ts": f"[{today}]", "speaker": "System", "turn_group": -1, "text": f"[CADENCE_APPLIED] {msg}"})

    except Exception as e:
        # Save crash details to diary and JSON
        crash_msg = f"\n[SIMULATION CRASH] {str(e)}\n{traceback.format_exc()}\n"
        diary_file.write(crash_msg)
        diary_file.flush()
        
        # Also add crash info to the JSON data
        all_chats.append({
            "id": _msg_id(),
            "ts": f"[CRASH]",
            "speaker": "System",
            "turn_group": -1,
            "text": f"[SIMULATION_CRASH] {str(e)}"
        })
    
    finally:
        # Always save output files regardless of how we exit
        export_partial()
        diary_file.close()
    
    return JSON_PATH