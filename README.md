# Scanner actions — pré-cassure

Détecter **l'action forte, dans un marché porteur, qui accumule, dont la force
relative augmente, dont le volume devient constructif, dont la volatilité se
comprime et qui se rapproche d'une résistance importante — avant la cassure.**

Le système est un outil d'analyse. Il ne transmet aucun ordre à un courtier et
n'affirme jamais qu'une action va monter : il mesure une configuration et en
donne la probabilité implicite, avec ses raisons.

## Ce qu'il fait

| Étape | Module | Rôle |
|---|---|---|
| Univers | `stockscan/universe.py` | 553 valeurs, 8 places (US, FR, DE, GB, NL, IT, ES, CH) |
| Données | `stockscan/market_data.py` | Yahoo chart API, une série journalière de 5 ans par valeur |
| Structure | `stockscan/structure.py` | tendance, bases, résistances, volume, accumulation (OBV), compression, extension |
| Force relative | `stockscan/strength.py` | ligne RS vs indice local, régime de marché (respiration, VIX) |
| Score | `stockscan/scoring.py` | note sur 100 **et** score pré-cassure séparé, chaque point justifié |
| Phase | `stockscan/phases.py` | EARLY → PRÉ-CASSURE → CASSURE → RETEST → ACCÉLÉRATION, plus le plan de risque |
| Fondamentaux | `stockscan/fundamentals.py` | SEC EDGAR XBRL, valeurs américaines uniquement |
| 2ᵉ avis | `stockscan/ai_judge.py` | Cloudflare Workers AI — peut objecter, jamais créer un signal |
| Alerte | `stockscan/telegram.py` | message détaillé, ou message « aucun signal » |
| Backtest | `stockscan/backtest.py` | par phase et par période, sans regard vers l'avenir |

## Pourquoi deux scores

Le score global dit *« cette action est-elle en bonne santé technique ? »*.
Le score pré-cassure dit *« est-elle sur le point de casser, maintenant ? »*.
Une action peut valoir 85/100 et être déjà partie : dans ce cas son score
pré-cassure tombe à zéro, parce qu'entrer après le mouvement est un autre métier.

## Utilisation

```bash
pip install -r requirements.txt

python run_scan.py preflight            # données, IA, Telegram, EDGAR
python run_scan.py scan --dry-run       # scanne, affiche, n'envoie rien
python run_scan.py scan                 # scanne et envoie sur Telegram
python run_backtest.py --markets US --limit 80
python -m unittest discover -t . -s tests
```

La suite de tests est entièrement hors ligne : aucun secret, aucun réseau.

## Secrets (GitHub Actions uniquement)

`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_GATEWAY_ID`
(optionnel), `CLOUDFLARE_MODEL` (optionnel), `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `SEC_USER_AGENT` (optionnel).

Aucun secret n'est écrit dans un fichier, affiché dans un log ni transmis dans
un message. Les messages d'erreur passent par une fonction de masquage avant
d'être affichés.

## Limites connues

- **Fondamentaux** : EDGAR ne couvre que les sociétés cotées aux États-Unis.
  Pour l'Europe le score fondamental n'est pas appliqué, il n'est pas inventé.
- **Secteurs** : Yahoo renvoie 401 sur les profils. La force sectorielle n'est
  disponible que si un indice sectoriel est fourni explicitement.
- **Backtest journalier** : une bougie qui touche le stop et l'objectif est
  comptée comme une perte. Les résultats en sont volontairement pessimistes.
- **Aucune donnée intraséance** n'est utilisée pour décider : la décision est
  prise sur clôture journalière.
