from __future__ import annotations

"""Standalone Cloudflare Workers AI validation.

Runs *before* the scanner in CI and checks, separately:

    A. CLOUDFLARE_ACCOUNT_ID present
    B. CLOUDFLARE_API_TOKEN present
    C. a real call is issued
    D. HTTP 200
    E. the envelope is valid JSON
    F. the model answer is usable
    G. the verdict is correctly interpreted

Never prints a secret. Exit code 0 when connected, 1 otherwise; the workflow
decides whether that is fatal (it is not — the engine runs without the AI).
"""

import sys

import forex_ai_judge as judge


def main() -> int:
    report = judge.check_connectivity()

    print("── VALIDATION CLOUDFLARE WORKERS AI ──")
    print(f"A. CLOUDFLARE_ACCOUNT_ID : {'présent' if report['account_id_present'] else 'ABSENT'}")
    print(f"B. CLOUDFLARE_API_TOKEN  : {'présent' if report['api_token_present'] else 'ABSENT'}")
    print(f"   Passerelle            : {report['gateway']}")
    print(f"   Modèle                : {report['model']}")
    print(f"C. Appel réel            : {'effectué' if report['http'] is not None else 'non effectué'}")
    print(f"D. HTTP                  : {report['http'] if report['http'] is not None else 'n/a'}")
    print(f"E. JSON valide           : {'oui' if report['json_ok'] else 'non'}")
    print(f"F. Réponse exploitable   : {'oui' if report['answer_ok'] else 'non'}")
    print(f"G. Verdict interprété    : {'oui — ' + report.get('verdict', '') if report['verdict_ok'] else 'non'}")
    print()

    if report["connected"]:
        print("Cloudflare AI : CONNECTÉ")
        print(f"Modèle : {report['model']}")
        print("Réponse : OK")
        return 0

    print("Cloudflare AI : ERREUR")
    print(f"HTTP : {report['http'] if report['http'] is not None else 'aucun'}")
    print(f"Cause : {report['error'] or 'inconnue'}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
