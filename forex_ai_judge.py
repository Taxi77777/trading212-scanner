from __future__ import annotations

"""Cloudflare Workers AI second opinion for the Forex scanner.

Architecture (the AI is a reviewer, never a generator)::

    QUANT ENGINE -> SETUP/ENTRY -> CLOUDFLARE QWEN -> CONFIRME/PRUDENCE/CONTRADICTION
                                -> FILTERING -> MEDAL -> TELEGRAM

Hard rules implemented here:

* The model can only ``CONFIRME``, ``PRUDENCE`` or ``CONTRADICTION`` an
  existing signal. It cannot create one.
* Only a real, parsed ``CONTRADICTION`` blocks a signal.
* Transport failure, HTTP error, malformed JSON or an out-of-vocabulary verdict
  all map to ``INDISPONIBLE`` with ``available=False`` — never a contradiction.
* No secret is ever returned, logged or forwarded to Telegram.
"""

import json
import os
import re
from typing import Any

import requests

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
CLOUDFLARE_GATEWAY_ID = os.getenv("CLOUDFLARE_GATEWAY_ID", "").strip()
CLOUDFLARE_MODEL = os.getenv("CLOUDFLARE_MODEL", "@cf/qwen/qwen3-30b-a3b-fp8").strip()
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT", "25"))
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "700"))

SOURCE_NAME = f"Cloudflare Workers AI / {CLOUDFLARE_MODEL}"

VERDICTS = ("CONFIRME", "PRUDENCE", "CONTRADICTION")

SYSTEM_PROMPT = (
    "You are a conservative FX risk-review assistant. Review only the supplied quantitative signal. "
    "Do not create trades. Do not invent missing data or current prices. "
    "Check the proposed direction against D1/H4/H1/M15, DXY, macro, currency strength, volatility, "
    "liquidity, correlation, news and R:R. "
    "Answer with a single JSON object and nothing else, with exactly these keys: "
    '{"verdict": "CONFIRME|PRUDENCE|CONTRADICTION", "confidence": 0-100, '
    '"contradiction": true|false, "reason": "one short sentence"}. '
    "Use CONTRADICTION only when the supplied evidence directly conflicts with the proposed direction. "
    "Use PRUDENCE when the trade remains defensible but carries identified risks. "
    "/no_think"
)


# --------------------------------------------------------------------------- #
# Secret hygiene
# --------------------------------------------------------------------------- #
def redact(text: object) -> str:
    """Strip anything secret-like from a diagnostic string."""
    out = str(text or "")
    if CLOUDFLARE_API_TOKEN:
        out = out.replace(CLOUDFLARE_API_TOKEN, "***")
    if CLOUDFLARE_ACCOUNT_ID:
        out = out.replace(CLOUDFLARE_ACCOUNT_ID, "***")
    # Defensive: account ids and bearer tokens embedded in URLs / headers.
    out = re.sub(r"/accounts/[A-Za-z0-9_-]{8,}", "/accounts/***", out)
    out = re.sub(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{8,}", "Bearer ***", out)
    out = re.sub(r"(?i)(api[_-]?token|authorization)\s*[:=]\s*\S+", r"\1=***", out)
    return out


def _fail(reason: object, http: int | None = None) -> dict[str, Any]:
    return {
        "available": False,
        "verdict": "INDISPONIBLE",
        "confidence": 0,
        "contradiction": False,
        "reason": redact(reason)[:300],
        "http": http,
    }


# --------------------------------------------------------------------------- #
# Response parsing
# --------------------------------------------------------------------------- #
def strip_reasoning(text: str) -> str:
    """Remove Qwen3 <think> blocks and markdown fences."""
    out = re.sub(r"<think>.*?</think>", " ", text or "", flags=re.S | re.I)
    out = re.sub(r"<think>.*", " ", out, flags=re.S | re.I)  # unterminated block
    out = re.sub(r"```(?:json)?", " ", out, flags=re.I)
    return out.strip()


def _json_candidates(text: str):
    """Yield balanced ``{...}`` substrings, outermost first."""
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start:i + 1]


def json_from_text(text: str) -> dict[str, Any] | None:
    cleaned = strip_reasoning(text)
    if not cleaned:
        return None
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except (json.JSONDecodeError, TypeError):
        pass
    for candidate in _json_candidates(cleaned):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "verdict" in value:
            return value
    return None


def normalise_result(result: dict[str, Any] | None) -> dict[str, Any]:
    """Turn a parsed model answer into the scanner's verdict contract.

    A malformed or out-of-vocabulary answer is *not* a contradiction: it is an
    unusable answer, so the AI is reported as unavailable.
    """
    if not result:
        return _fail("Réponse IA non structurée")

    verdict = str(result.get("verdict", "")).strip().upper()
    verdict = verdict.replace("CONFIRMÉ", "CONFIRME").replace("CONFIRMED", "CONFIRME")
    if verdict not in VERDICTS:
        return _fail(f"Verdict IA inexploitable: {verdict[:40] or 'vide'}")

    try:
        confidence = max(0, min(100, int(float(result.get("confidence", 0) or 0))))
    except (TypeError, ValueError):
        confidence = 0

    raw_contradiction = result.get("contradiction")
    if isinstance(raw_contradiction, str):
        raw_contradiction = raw_contradiction.strip().lower() in {"true", "1", "yes", "oui"}
    contradiction = bool(raw_contradiction) if raw_contradiction is not None else False
    # The verdict is authoritative: the two fields must never disagree.
    contradiction = contradiction or verdict == "CONTRADICTION"
    if contradiction:
        verdict = "CONTRADICTION"

    return {
        "available": True,
        "verdict": verdict,
        "confidence": confidence,
        "contradiction": contradiction,
        "reason": redact(result.get("reason", ""))[:300],
        "http": 200,
    }


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def configured() -> bool:
    return bool(CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN)


def _endpoint() -> str:
    return (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{CLOUDFLARE_ACCOUNT_ID}/ai/v1/chat/completions"
    )


def _headers() -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    # Only route through AI Gateway when explicitly asked to. Sending the header
    # unconditionally requires AI Gateway token permissions the account may not
    # have, which would turn a working Workers AI setup into a hard failure.
    if CLOUDFLARE_GATEWAY_ID:
        headers["cf-aig-gateway-id"] = CLOUDFLARE_GATEWAY_ID
    return headers


def call_model(messages: list[dict[str, str]], max_tokens: int | None = None) -> dict[str, Any]:
    """Low-level call. Returns ``{"ok", "http", "content", "error"}``."""
    if not configured():
        return {"ok": False, "http": None, "content": "", "error": "Secrets Cloudflare AI absents"}

    body = {
        "model": CLOUDFLARE_MODEL,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens or AI_MAX_TOKENS,
    }
    response = None
    try:
        response = requests.post(_endpoint(), headers=_headers(), json=body, timeout=AI_TIMEOUT)
    except Exception as exc:  # network / DNS / timeout
        return {"ok": False, "http": None, "content": "", "error": redact(exc)}

    http = response.status_code
    if http != 200:
        return {"ok": False, "http": http, "content": "", "error": redact(response.text)[:200]}

    try:
        data = response.json()
    except ValueError:
        return {"ok": False, "http": http, "content": "", "error": "Réponse Cloudflare non JSON"}

    choices = data.get("choices") or []
    if not choices:
        detail = data.get("errors") or data.get("error") or "réponse sans choices"
        return {"ok": False, "http": http, "content": "", "error": redact(detail)[:200]}

    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if not content and message.get("reasoning_content"):
        content = message["reasoning_content"]
    return {"ok": True, "http": http, "content": content, "error": ""}


def check_connectivity() -> dict[str, Any]:
    """Full Cloudflare validation used by the workflow self-test.

    Checks, separately: account id secret, api token secret, real call,
    HTTP 200, valid JSON envelope, usable model answer.
    """
    report: dict[str, Any] = {
        "account_id_present": bool(CLOUDFLARE_ACCOUNT_ID),
        "api_token_present": bool(CLOUDFLARE_API_TOKEN),
        "gateway": CLOUDFLARE_GATEWAY_ID or "direct (aucun AI Gateway)",
        "model": CLOUDFLARE_MODEL,
        "http": None,
        "json_ok": False,
        "answer_ok": False,
        "verdict_ok": False,
        "connected": False,
        "error": "",
    }
    if not report["account_id_present"]:
        report["error"] = "CLOUDFLARE_ACCOUNT_ID absent"
        return report
    if not report["api_token_present"]:
        report["error"] = "CLOUDFLARE_API_TOKEN absent"
        return report

    probe = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "pair": "EUR/USD", "symbol": "EURUSD=X", "side": "BUY", "state": "SETUP",
                    "score": 70, "d1": "BULLISH", "h4": "BULLISH", "h1": "BULLISH",
                    "m15": "CONFIRME", "dxy": "BEAR", "macro": "RISK-ON",
                    "note": "connectivity self-test",
                },
                ensure_ascii=False,
            ),
        },
    ]
    result = call_model(probe)
    report["http"] = result["http"]
    if not result["ok"]:
        report["error"] = result["error"]
        return report

    report["json_ok"] = True
    parsed = json_from_text(result["content"])
    report["answer_ok"] = bool(result["content"].strip())
    if parsed is None:
        report["error"] = "Le modèle a répondu mais sans JSON exploitable"
        return report

    verdict = normalise_result(parsed)
    report["verdict_ok"] = verdict["available"]
    report["connected"] = verdict["available"]
    report["verdict"] = verdict["verdict"]
    if not verdict["available"]:
        report["error"] = verdict["reason"]
    return report


# --------------------------------------------------------------------------- #
# Signal review
# --------------------------------------------------------------------------- #
def signal_payload(sig: Any) -> dict[str, Any]:
    return {
        "pair": getattr(sig, "pair", ""),
        "symbol": getattr(sig, "symbol", ""),
        "side": getattr(sig, "side", ""),
        "state": getattr(sig, "state", ""),
        "score": getattr(sig, "score", 0),
        "entry": getattr(sig, "price", None),
        "sl": getattr(sig, "sl", None),
        "tp1": getattr(sig, "tp1", None),
        "tp2": getattr(sig, "tp2", None),
        "rr_tp1": getattr(sig, "rr", None),
        "d1": getattr(sig, "d1", ""),
        "h4": getattr(sig, "h4", ""),
        "h1": getattr(sig, "h1", ""),
        "m15": getattr(sig, "m15", ""),
        "dxy": getattr(sig, "dxy", ""),
        "macro": getattr(sig, "macro", ""),
        "currency_strength": getattr(sig, "strength", ""),
        "volatility": getattr(sig, "vol_regime", ""),
        "liquidity": getattr(sig, "liquidity", ""),
        "correlation": getattr(sig, "correlation", ""),
        "news": getattr(sig, "news", ""),
        "session": getattr(sig, "session", ""),
        "confluence": list(getattr(sig, "reasons", []) or [])[:10],
    }


def judge_signal(sig: Any) -> dict[str, Any]:
    """Second opinion on an already-formed quantitative signal."""
    if not configured():
        return _fail("Secrets Cloudflare AI absents")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(signal_payload(sig), ensure_ascii=False, separators=(",", ":")),
        },
    ]
    result = call_model(messages)
    if not result["ok"]:
        return _fail(result["error"], result["http"])
    return normalise_result(json_from_text(result["content"]))
