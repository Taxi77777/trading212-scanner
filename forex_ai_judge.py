from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
CLOUDFLARE_GATEWAY_ID = os.getenv("CLOUDFLARE_GATEWAY_ID", "default").strip() or "default"
CLOUDFLARE_MODEL = os.getenv("CLOUDFLARE_MODEL", "@cf/qwen/qwen3-30b-a3b-fp8").strip()
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT", "20"))

SOURCE_NAME = f"Cloudflare Workers AI / {CLOUDFLARE_MODEL}"


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


def _normalise_result(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {"available": True, "verdict": "PRUDENCE", "confidence": 0, "contradiction": False, "reason": "Réponse IA non structurée"}
    verdict = str(result.get("verdict", "PRUDENCE")).upper()
    if verdict not in {"CONFIRME", "PRUDENCE", "CONTRADICTION"}:
        verdict = "PRUDENCE"
    try:
        confidence = max(0, min(100, int(float(result.get("confidence", 0) or 0))))
    except (TypeError, ValueError):
        confidence = 0
    contradiction = bool(result.get("contradiction", verdict == "CONTRADICTION"))
    reason = str(result.get("reason", ""))[:300]
    return {"available": True, "verdict": verdict, "confidence": confidence, "contradiction": contradiction, "reason": reason}


def judge_signal(sig: Any) -> dict[str, Any]:
    """Cloudflare AI second opinion for an already-formed quantitative signal.

    The model can confirm, warn, or reject. It never creates a trade signal.
    If Cloudflare is unavailable or the daily free allocation is exhausted,
    the quantitative scanner continues and explicitly reports the AI as offline.
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
        "You are a conservative FX risk-review assistant. Review only the supplied quantitative signal. "
        "Do not create trades. Do not invent missing data or current prices. "
        "Check direction against D1/H4/H1/M15, DXY, macro, currency strength, volatility, liquidity, correlation, news, and R:R. "
        "Return JSON only with keys verdict, confidence, contradiction, reason. "
        "verdict must be CONFIRME, PRUDENCE or CONTRADICTION. "
        "Use CONTRADICTION only when supplied evidence directly conflicts with the proposed direction."
    )

    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        return {"available": False, "verdict": "INDISPONIBLE", "confidence": 0, "contradiction": False, "reason": "Secrets Cloudflare AI absents"}

    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
        "cf-aig-gateway-id": CLOUDFLARE_GATEWAY_ID,
    }
    body = {
        "model": CLOUDFLARE_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        ],
        "temperature": 0,
        "max_tokens": 180,
    }

    try:
        response = requests.post(url, headers=headers, json=body, timeout=AI_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return _normalise_result(_json_from_text(content))
    except Exception as exc:
        reason = str(exc)
        try:
            detail = response.text[:160]  # type: ignore[name-defined]
            if detail:
                reason = f"{reason} | {detail}"
        except Exception:
            pass
        return {"available": False, "verdict": "INDISPONIBLE", "confidence": 0, "contradiction": False, "reason": reason[:300]}
