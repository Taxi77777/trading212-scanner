from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.6:27b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "12"))

SOURCE_NAME = f"Ollama / {OLLAMA_MODEL} (optionnel, open-weight)"


def _json_from_text(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def judge_signal(sig: Any) -> dict[str, Any]:
    """Ask local Qwen to cross-check an already-formed quantitative signal.

    Second opinion only. It never creates a signal and never raises into the
    production scanner when Ollama is unavailable.
    """
    payload = {
        "pair": getattr(sig, "pair", ""),
        "symbol": getattr(sig, "symbol", ""),
        "side": getattr(sig, "side", ""),
        "state": getattr(sig, "state", ""),
        "score": getattr(sig, "score", 0),
        "price": getattr(sig, "price", None),
        "entry": getattr(sig, "price", None),
        "sl": getattr(sig, "sl", None),
        "tp1": getattr(sig, "tp1", None),
        "tp2": getattr(sig, "tp2", None),
        "rr": getattr(sig, "rr", None),
        "d1": getattr(sig, "d1", ""),
        "h4": getattr(sig, "h4", ""),
        "h1": getattr(sig, "h1", ""),
        "m15": getattr(sig, "m15", ""),
        "dxy": getattr(sig, "dxy", ""),
        "macro": getattr(sig, "macro", ""),
        "strength": getattr(sig, "strength", ""),
        "volatility": getattr(sig, "vol_regime", ""),
        "liquidity": getattr(sig, "liquidity", ""),
        "correlation": getattr(sig, "correlation", ""),
        "news": getattr(sig, "news", ""),
        "session": getattr(sig, "session", ""),
        "reasons": getattr(sig, "reasons", []),
    }

    system = (
        "You are a conservative FX risk-review assistant. Review only the supplied "
        "quantitative signal. Do not create trades and do not invent missing data. "
        "Return JSON only with keys: verdict, confidence, contradiction, reason. "
        "verdict must be CONFIRME, PRUDENCE or CONTRADICTION. "
        "Use CONTRADICTION only when supplied evidence directly conflicts with the trade direction."
    )

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            },
            timeout=OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("message", {}).get("content", "")
        result = _json_from_text(content)
        if not result:
            return {"available": True, "verdict": "PRUDENCE", "confidence": 0, "contradiction": False, "reason": "Réponse IA non structurée"}

        verdict = str(result.get("verdict", "PRUDENCE")).upper()
        if verdict not in {"CONFIRME", "PRUDENCE", "CONTRADICTION"}:
            verdict = "PRUDENCE"
        confidence = max(0, min(100, int(float(result.get("confidence", 0) or 0))))
        contradiction = bool(result.get("contradiction", verdict == "CONTRADICTION"))
        reason = str(result.get("reason", ""))[:300]
        return {
            "available": True,
            "verdict": verdict,
            "confidence": confidence,
            "contradiction": contradiction,
            "reason": reason,
        }
    except Exception as exc:
        return {"available": False, "verdict": "INDISPONIBLE", "confidence": 0, "contradiction": False, "reason": str(exc)[:160]}
