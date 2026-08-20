from __future__ import annotations

"""Cloudflare Workers AI second opinion for the equity pre-breakout scanner.

Architecture (the AI is a reviewer, never a generator)::

    MOTEUR QUANT -> PHASE + PLAN -> CLOUDFLARE QWEN -> CONFIRME/PRUDENCE/CONTRADICTION
                                 -> FILTRAGE -> CLASSEMENT -> TELEGRAM

The engine decides. The model may only object to what the engine already found.

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
    "You are a conservative equity risk-review assistant. Review only the supplied "
    "quantitative pre-breakout candidate. Do not create trades. Do not predict prices. "
    "Do not invent missing data, news, earnings dates or fundamentals. "
    "Judge whether the stated phase (EARLY, PRE_BREAKOUT, BREAKOUT, RETEST) is consistent "
    "with the supplied trend, base, volume, accumulation, volatility compression, relative "
    "strength, extension, market regime and reward-to-risk. "
    "Answer with a single JSON object and nothing else, with exactly these keys: "
    '{"verdict": "CONFIRME|PRUDENCE|CONTRADICTION", "confidence": 0-100, '
    '"contradiction": true|false, "reason": "one short sentence"}. '
    "Use CONTRADICTION only when the supplied evidence directly conflicts with the stated "
    "phase - for example an extended stock presented as a pre-breakout, or a downtrend "
    "presented as accumulation. Use PRUDENCE when the setup remains defensible but carries "
    "identified risks. Never state that a stock will rise. "
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
                    "ticker": "AAPL", "market": "US", "phase": "PRE_BREAKOUT",
                    "score": 71, "prebreakout_score": 68, "trend": "HAUSSIERE",
                    "base": "Flat Base 42 seances", "volume": "assechement",
                    "accumulation": "OBV proche de ses sommets",
                    "compression_ratio": 0.62, "relative_strength_3m_pts": 8.1,
                    "regime": "RISK_ON", "rr": 2.4,
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
def candidate_payload(c: Any) -> dict[str, Any]:
    """Ce que le modele voit. Volontairement factuel : aucune conclusion soufflee."""
    plan = getattr(c, "plan", None)
    score = getattr(c, "score", None)
    return {
        "ticker": getattr(c, "ticker", ""),
        "market": getattr(c, "market", ""),
        "phase": getattr(getattr(c, "phase", None), "name", ""),
        "score": round(getattr(score, "total", 0.0), 1) if score else 0.0,
        "prebreakout_score": round(getattr(score, "prebreakout", 0.0), 1) if score else 0.0,
        "grade": getattr(score, "grade", "") if score else "",
        "trend": getattr(getattr(c, "trend", None), "direction", ""),
        "base": getattr(getattr(c, "base", None), "label", ""),
        "base_length": getattr(getattr(c, "base", None), "length", 0),
        "resistance": getattr(getattr(c, "resistance", None), "level", None),
        "distance_to_resistance_pct": round(getattr(c, "distance_pct", 0.0), 2),
        "volume": list(getattr(getattr(c, "volume", None), "notes", []) or [])[:3],
        "accumulation": list(getattr(getattr(c, "accum", None), "notes", []) or [])[:3],
        "compression_ratio": round(getattr(getattr(c, "comp", None), "ratio", 0.0), 3),
        "relative_strength_3m_pts": round(getattr(getattr(c, "rs", None), "rs_3m", 0.0), 2),
        "extension_20d_pct": round(getattr(getattr(c, "ext", None), "change_20d", 0.0), 2),
        "regime": getattr(getattr(c, "regime", None), "label", ""),
        "entry": getattr(plan, "entry", None),
        "stop": getattr(plan, "stop", None),
        "risk_pct": getattr(plan, "risk_pct", None),
        "rr": getattr(plan, "rr", None),
        "fundamental_score": getattr(getattr(c, "fundamental", None), "score", None),
        "reasons": list(getattr(getattr(c, "phase", None), "reasons", []) or [])[:6],
    }


def judge_candidate(c: Any) -> dict[str, Any]:
    """Second opinion on an already-formed quantitative candidate."""
    if not configured():
        return _fail("Secrets Cloudflare AI absents")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(candidate_payload(c), ensure_ascii=False, separators=(",", ":")),
        },
    ]
    result = call_model(messages)
    if not result["ok"]:
        return _fail(result["error"], result["http"])
    return normalise_result(json_from_text(result["content"]))
