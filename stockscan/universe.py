from __future__ import annotations

"""Univers de scan : ~600 valeurs, 8 pays.

Chaque entrée porte son symbole Yahoo, son marché, sa devise et l'indice de
référence auquel comparer sa force relative. L'ajout d'une place se fait en
ajoutant une entrée à ``MARKETS`` puis sa liste de composants — rien d'autre
dans le scanner ne connaît la géographie.

Les listes sont des composants d'indices majeurs : c'est le seul moyen fiable
d'obtenir un univers propre sans source payante, et cela garantit d'emblée la
liquidité exigée au §3. Elles se périment lentement (quelques entrées/sorties
par an) — un symbole disparu est simplement ignoré par le moteur de données.
"""

from dataclasses import dataclass

__all__ = ["Market", "Stock", "MARKETS", "universe", "by_market", "benchmarks"]


@dataclass(frozen=True)
class Market:
    code: str
    label: str
    country: str
    currency: str
    suffix: str          # suffixe Yahoo, "" pour les États-Unis
    index_symbol: str    # indice local, pour la force relative
    index_label: str


MARKETS: dict[str, Market] = {
    "US":  Market("US",  "NYSE / Nasdaq", "États-Unis",     "USD", "",     "^GSPC",    "S&P 500"),
    "FR":  Market("FR",  "Euronext Paris", "France",        "EUR", ".PA",  "^FCHI",    "CAC 40"),
    "DE":  Market("DE",  "Xetra",          "Allemagne",     "EUR", ".DE",  "^GDAXI",   "DAX 40"),
    "GB":  Market("GB",  "LSE",            "Royaume-Uni",   "GBP", ".L",   "^FTSE",    "FTSE 100"),
    "NL":  Market("NL",  "Euronext Amsterdam", "Pays-Bas",  "EUR", ".AS",  "^AEX",     "AEX"),
    "IT":  Market("IT",  "Borsa Italiana", "Italie",        "EUR", ".MI",  "FTSEMIB.MI", "FTSE MIB"),
    "ES":  Market("ES",  "BME",            "Espagne",       "EUR", ".MC",  "^IBEX",    "IBEX 35"),
    "CH":  Market("CH",  "SIX",            "Suisse",        "CHF", ".SW",  "^SSMI",    "SMI"),
}

# Indices de contexte global (§28) — régime de marché, pas force relative.
GLOBAL_INDICES = {
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq Composite", "^RUT": "Russell 2000",
    "^FCHI": "CAC 40", "^GDAXI": "DAX 40", "^STOXX50E": "Euro Stoxx 50",
    "^VIX": "VIX",
}

# --------------------------------------------------------------------------- #
# Composants. Symboles bruts : le suffixe Yahoo est ajouté par `universe()`.
# --------------------------------------------------------------------------- #
_US = """
AAPL MSFT NVDA AMZN GOOGL GOOG META AVGO TSLA BRK-B JPM LLY V UNH XOM MA COST
HD PG JNJ WMT NFLX ABBV BAC CRM ORCL CVX MRK KO AMD PEP TMO LIN ADBE CSCO ACN
MCD ABT WFC PM DHR IBM GE TXN QCOM NOW CAT VZ INTU DIS AMGN CMCSA PFE UNP RTX
SPGI AXP LOW NEU ISRG T HON UBER GS BKNG SYK PGR ELV LRCX BLK NKE TJX MU VRTX
C BSX SCHW ADI MDT ADP REGN PLD CB DE MMC LMT CI SBUX AMAT BMY MO SO KLAC ZTS
PANW ANET DUK ICE SHW CME EQIX APH MCO TT GEV WM MSI PH CVS PYPL AON CDNS SNPS
CTAS ORLY MDLZ MCK CL NOC ITW EMR PNC USB APD ECL FDX GD COF MAR ROP RSG CSX
NSC SLB AJG TDG TFC AFL SPG AIG PSA NXPI OKE HLT CARR TRV DLR ALL AMP MET AZO
PCAR SRE BK PSX CPRT WELL MPC O TEL FICO KMB PAYX ROST GM F DAL UAL LUV CCL
RCL ABNB DASH COIN HOOD PLTR SNOW DDOG NET CRWD ZS OKTA MDB TEAM WDAY SQ SHOP
RIVN LCID NIO XPEV ON MRVL SMCI ARM ASML TSM STM SWKS QRVO TER ENPH FSLR RUN
MRNA BNTX VRTX ALNY BIIB ILMN DXCM IDXX A WST HOLX BAX ZBH EW ISRG SYK BDX
ETN ROK AME FTV IR XYL PNR DOV EMR CMI PCAR WAB URI FAST GWW POOL SITE
NUE STLD X CLF FCX NEM AA MOS CF LIN APD SHW PPG DD DOW LYB EMN CE ALB
NEE DUK SO D AEP EXC XEL ED WEC ES PEG FE AEE CMS DTE PPL CNP NI LNT
"""

_FR = """
AI AIR ALO BN BNP CA CAP CS DG DSY EL EN ENGI ERF GLE HO KER LR MC ML OR ORA
PUB RI RMS RNO SAF SAN SGO STLAP STMPA SU SW TE TTE URW VIE VIV WLN ACA EDEN
"""

_DE = """
ADS AIR ALV BAS BAYN BEI BMW BNR CBK CON 1COV DBK DHL DTE DTG EOAN FME FRE
HEI HEN3 HNR1 IFX MBG MRK MTX MUV2 P911 PAH3 QIA RHM RWE SAP SHL SIE SRT3
SY1 VNA VOW3 ZAL
"""

_GB = """
AAL ABF ADM AHT ANTO AUTO AV AZN BA BARC BATS BDEV BEZ BKG BNZL BP BT-A CCH
CNA CPG CRDA CTEC DCC DGE ENT EXPN FCIT FRAS FRES GLEN GSK HIK HLMA HLN HSBA
IAG ICG IHG III IMB INF ITRK JD KGF LAND LGEN LLOY LSEG MNDI MNG MRO NG NWG
NXT OCDO PHNX PRU PSN PSON REL RIO RKT RMV RR RS1 RTO SBRY SDR SGE SGRO SHEL
SMDS SMIN SMT SN SPX SSE STAN STJ SVT TSCO TW ULVR UTG UU VOD WEIR WPP WTB
"""

_NL = """
ADYEN AD AGN AKZA ASM ASML ASRNL BESI DSFIR HEIA IMCD INGA KPN NN PHIA PRX
RAND REN SHELL UNA URW WKL
"""

_IT = """
A2A AMP AZM BAMI BGN BMED BMPS BPE BZU CPR DIA ENEL ENI ERG G HER INW IP ISP
IVG LDO MB MONC NEXI PIRC PST REC SPM SRG STLAM STMMI TEN TIT TRN UCG UNI
"""

_ES = """
ACS ACX AENA AMS ANA ANE BBVA BKT CABK CLNX COL ELE ENG FDR FER GRF IAG IBE
IDR ITX LOG MAP MRL MTS NTGY PUIG RED REP ROVI SAB SAN SCYR SLR TEF UNI VIS
"""

_CH = """
ABBN ADEN ALC BAER CFR GEBN GIVN HOLN KNIN LOGN LONN NESN NOVN PGHN ROG SCMN
SGSN SIKA SLHN SOON SREN UBSG UHR ZURN
"""

_COMPONENTS = {"US": _US, "FR": _FR, "DE": _DE, "GB": _GB,
               "NL": _NL, "IT": _IT, "ES": _ES, "CH": _CH}


@dataclass(frozen=True)
class Stock:
    symbol: str          # symbole Yahoo complet, ex. "AIR.PA"
    ticker: str          # symbole brut, ex. "AIR"
    market: Market

    @property
    def label(self) -> str:
        return self.ticker

    @property
    def is_us(self) -> bool:
        return self.market.code == "US"


def _clean(block: str) -> list[str]:
    seen, out = set(), []
    for token in block.split():
        t = token.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def universe(markets: tuple[str, ...] | None = None) -> list[Stock]:
    """Univers complet, ou restreint aux codes marché demandés."""
    codes = markets or tuple(MARKETS)
    out: list[Stock] = []
    for code in codes:
        market = MARKETS.get(code)
        if market is None:
            continue
        for ticker in _clean(_COMPONENTS.get(code, "")):
            out.append(Stock(f"{ticker}{market.suffix}", ticker, market))
    return out


def by_market(markets: tuple[str, ...] | None = None) -> dict[str, list[Stock]]:
    grouped: dict[str, list[Stock]] = {}
    for stock in universe(markets):
        grouped.setdefault(stock.market.code, []).append(stock)
    return grouped


def benchmarks(markets: tuple[str, ...] | None = None) -> dict[str, str]:
    """Indice local par code marché, pour la force relative."""
    codes = markets or tuple(MARKETS)
    return {c: MARKETS[c].index_symbol for c in codes if c in MARKETS}
