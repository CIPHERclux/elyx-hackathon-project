# engine/sentiment.py
# Module: Persona sentiment tracker (deterministic demo to save tokens).
# Returns a small snapshot used by Elyx prompts to adapt tone.

from typing import List, Dict

def track_persona_sentiment(day_messages: List[dict]) -> Dict[str, int]:
    trust, engagement, frustration = 55, 52, 22
    tups = {
        "pos": ("thanks","helpful","nice","works","good","👍","ok","great","love"),
        "neg": ("busy","later","can't","cant","skip","won't","wont","no","nah","too much","hard"),
        "stress": ("stressed","deadline","flight","jetlag","jet lag","travel","busy week","on the road")
    }
    for m in day_messages:
        t = (m.get("text") or "").lower()
        if any(p in t for p in tups["pos"]):
            trust += 2
            engagement += 1
        if any(n in t for n in tups["neg"]):
            engagement -= 2
            frustration += 2
        if any(s in t for s in tups["stress"]):
            frustration += 2
            engagement -= 1
    clamp = lambda x: max(0, min(100, x))
    snapshot = {"trust": clamp(trust), "engagement": clamp(engagement), "frustration": clamp(frustration)}
    return snapshot