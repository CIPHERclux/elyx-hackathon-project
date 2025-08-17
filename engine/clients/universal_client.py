# clients/universal_client.py
import os, json, requests
from engine.rate_limit import RateLimiter

DEFAULTS = {
    "openai_base": "https://openrouter.ai/api/v1/chat/completions",
    "anthropic_base": "https://api.anthropic.com/v1/messages",
    "gemini_base_root": "https://generativelanguage.googleapis.com/v1beta/models",
    "openai_model": "gpt-4o-mini",
    "anthropic_model": "claude-3-haiku-20240307",
    "gemini_model": "gemini-2.0-flash-lite"
}

rate_limiter = RateLimiter(rpm=5, daily_limit=480)

def _guess_provider_from_key(key: str) -> str:
    if not key: return "mock"
    if key.startswith("sk-ant-"): return "anthropic"
    if key.startswith("AIza"): return "gemini"
    if key.startswith("gsk_"): return "openai"
    if key.startswith("sk-or-"): return "openai"
    if key.startswith("sk-"): return "openai"
    if key.startswith("together_"): return "openai"
    if key.startswith("hf_"): return "openai"
    return "openai"

def _detect(provider_env=None):
    key = os.getenv("LLM_API_KEY", "")
    provider = provider_env or os.getenv("LLM_PROVIDER", "auto")
    if provider == "auto":
        provider = _guess_provider_from_key(key)
    return provider, key

def _headers_common(key, extra=None):
    h = {"Content-Type": "application/json"}
    if key and not key.startswith("AIza"):
        h["Authorization"] = f"Bearer {key}"
    if extra:
        h.update(extra)
    return h

def call_llm(messages, system=None, developer=None, temperature=0.7, max_tokens=220):
    provider, key = _detect()
    base_url = os.getenv("LLM_BASE_URL", "").strip()

    def _openai_payload():
        return {
            "model": os.getenv("OPENAI_MODEL", DEFAULTS["openai_model"]),
            "messages": (
                ([{"role":"system","content": system}] if system else []) +
                ([{"role":"system","content": developer}] if developer else []) +
                messages
            ),
            "temperature": temperature,
            "max_tokens": max_tokens
        }

    if provider == "gemini":
        # Basic Gemini REST; collapse to single string
        def messages_to_text(ms):
            return "\n".join([m.get("content","") for m in ms if m.get("content")])
        sms = []
        if system: sms.append(f"[SYSTEM]\n{system}")
        if developer: sms.append(f"[DEVELOPER]\n{developer}")
        sms.append(messages_to_text(messages))
        url = base_url or f"{DEFAULTS['gemini_base_root']}/{os.getenv('GEMINI_MODEL', DEFAULTS['gemini_model'])}:generateContent?key={key}"
        data = {"contents": [{"parts": [{"text": "\n\n".join(sms)}]}]}
        headers = _headers_common(None)
        r = requests.post(url, json=data, headers=headers, timeout=60)
        r.raise_for_status()
        js = r.json()
        return js.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()

    # Default: OpenAI-compatible
    url = base_url or DEFAULTS["openai_base"]
    data = _openai_payload()
    headers = _headers_common(key)
    r = requests.post(url, json=data, headers=headers, timeout=60)
    r.raise_for_status()
    js = r.json()
    # OpenAI-like
    try:
        return js["choices"][0]["message"]["content"].strip()
    except Exception:
        return (js.get("choices", [{}])[0].get("text", "") or "").strip()

