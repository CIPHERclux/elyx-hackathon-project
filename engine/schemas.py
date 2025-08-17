# Minimal JSON Schemas & validators for core structures (Modules 1–4, 14–19).
# We validate objects the engine emits/consumes to keep contracts honest.
from jsonschema import validate, Draft202012Validator

CHAT_MESSAGES_SCHEMA = {
    "type": "object",
    "required": ["turn_group", "persona_chosen", "messages", "proposed_actions", "expected_effects"],
    "properties": {
        "turn_group": {"type": "integer"},
        "persona_chosen": {"type": "string", "enum": ["Ruby","Dr. Warren","Advik","Carla","Rachel","Neel","Rohan"]},
        "messages": {
            "type": "array",
            "items": {
                "type":"object",
                "required":["client_temp_id","ts","speaker","text","style"],
                "properties":{
                    "client_temp_id":{"type":"string"},
                    "ts":{"type":"string"},
                    "speaker":{"type":"string"},
                    "text":{"type":"string"},
                    "style":{"type":"string"}
                }
            }
        },
        "proposed_actions": {
            "type": "object",
            "properties": {
                "schedule_test": {"type":["object","null"]},
                "schedule_exercise_update": {"type":["object","null"]},
                "nudges": {"type":"array","items":{"type":"string"}},
                "notes": {"type":["string","null"]}
            },
            "required": ["schedule_test","schedule_exercise_update","nudges"]
        },
        "expected_effects": {
            "type":"object",
            "properties": {
                "kpi_targets":{"type":"array","items":{"type":"string"}},
                "direction":{"type":"string","enum":["+","-","0"]},
                "magnitude_hint":{"type":"string"}
            },
            "required":["kpi_targets","direction"]
        }
    }
}

DAILY_DECISIONS_SCHEMA = {
    "type":"object",
    "required":["date_iso","decisions","notes"],
    "properties":{
        "date_iso":{"type":"string"},
        "decisions":{
            "type":"array",
            "items":{
                "type":"object",
                "required":["decision_type","title","trigger","rationale","affected_kpis","linked_message_ids","confidence"],
                "properties":{
                    "decision_type":{"type":"string"},
                    "title":{"type":"string"},
                    "trigger":{"type":"string"},
                    "rationale":{"type":"string"},
                    "affected_kpis":{"type":"array","items":{"type":"string"}},
                    "linked_message_ids":{"type":"array","items":{"type":"string"}},
                    "confidence":{"type":"number"}
                }
            }
        },
        "notes":{"type":["string","null"]}
    }
}

WEEKLY_SUMMARY_SCHEMA = {
    "type":"object",
    "required":["week_start","decisions","persona_state"],
    "properties":{
        "week_start":{"type":"string"},
        "decisions":{"type":"array","items":{"type":"object"}},
        "persona_state":{
            "type":"object",
            "required":["trust","engagement","frustration"],
            "properties":{
                "trust":{"type":"integer"},
                "engagement":{"type":"integer"},
                "frustration":{"type":"integer"}
            }
        }
    }
}

def ensure_valid(schema, obj, name="payload"):
    Draft202012Validator(schema).validate(obj)
    return obj
