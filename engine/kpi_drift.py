# kpi_drift.py
# Deterministic-ish KPI drift using adherence, travel, and decision hints.
import random

CLAMPS = {
    "hrv": (20, 90),
    "vo2max": (25, 60),
    "cholesterol_total": (140, 260),  # align with state key
    "sleep_quality": (40, 85),
    "stress_resilience": (40, 85),
}

def _clamp(val, lo, hi):
    return max(lo, min(hi, val))

def _is_travel_week(state):
    today = state["date_iso"]
    return today in set(state.get("member", {}).get("travel_weeks", []))

def apply_kpi_drift(state, weekly_effect_hints=None):
    k = state["kpis"]
    adherence = float(state["member"].get("adherence_rate", 0.5))
    is_travel = _is_travel_week(state)
    delta = {"hrv": 0, "vo2max": 0, "cholesterol_total": 0, "sleep_quality": 0, "stress_resilience": 0}

    # Adherence
    if adherence < 0.5:
        delta["hrv"] -= 1 + (1 if random.random() < 0.5 else 0)
        delta["sleep_quality"] -= 1
        delta["cholesterol_total"] += 2 + (1 if random.random() < 0.5 else 0)
    elif adherence > 0.6:
        delta["hrv"] += 1 + (1 if random.random() < 0.5 else 0)
        if random.random() < 0.5:
            delta["vo2max"] += 1
        delta["cholesterol_total"] -= 2

    # Travel
    if is_travel:
        delta["sleep_quality"] -= 2
        delta["stress_resilience"] -= 1

    # Decisions nudges
    weekly_effect_hints = weekly_effect_hints or []
    for hint in weekly_effect_hints:
        for kpi in hint.get("kpi_targets", []):
            if hint.get("direction") == "+" and kpi in ["sleep_quality", "stress_resilience", "hrv"]:
                delta[kpi] += 1
            if hint.get("direction") == "-" and kpi == "cholesterol_total":
                delta["cholesterol_total"] -= 2

    # Apply + clamp
    for key, (lo, hi) in CLAMPS.items():
        k[key] = _clamp(k[key] + delta[key], lo, hi)
    return state