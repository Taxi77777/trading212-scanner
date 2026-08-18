COMPANY_NAMES = {
    'NVDA':'NVIDIA Corporation','AMD':'Advanced Micro Devices','AVGO':'Broadcom','QCOM':'Qualcomm','MU':'Micron Technology','MRVL':'Marvell Technology','ARM':'Arm Holdings','INTC':'Intel','TSM':'Taiwan Semiconductor Manufacturing','ASML':'ASML Holding',
    'MSFT':'Microsoft','GOOGL':'Alphabet','META':'Meta Platforms','AMZN':'Amazon','AAPL':'Apple','ORCL':'Oracle','CRM':'Salesforce','ADBE':'Adobe','NOW':'ServiceNow','SNOW':'Snowflake',
    'PLTR':'Palantir Technologies','AI':'C3.ai','BBAI':'BigBear.ai','SOUN':'SoundHound AI','IONQ':'IonQ','CRWD':'CrowdStrike','PANW':'Palo Alto Networks','NET':'Cloudflare','DDOG':'Datadog','MDB':'MongoDB',
    'COIN':'Coinbase Global','HOOD':'Robinhood Markets','PYPL':'PayPal','SQ':'Block','MSTR':'Strategy','RIOT':'Riot Platforms','MARA':'MARA Holdings','SOFI':'SoFi Technologies','NU':'Nu Holdings','AFRM':'Affirm Holdings',
    'TSLA':'Tesla','RIVN':'Rivian Automotive','LCID':'Lucid Group','UBER':'Uber Technologies','LYFT':'Lyft','NIO':'NIO','XPEV':'XPeng','GM':'General Motors','F':'Ford Motor','ABNB':'Airbnb',
    'LMT':'Lockheed Martin','RTX':'RTX Corporation','NOC':'Northrop Grumman','GD':'General Dynamics','BA':'Boeing','HWM':'Howmet Aerospace','GE':'GE Aerospace','CAT':'Caterpillar','DE':'Deere & Company','ETN':'Eaton',
    'LLY':'Eli Lilly','NVO':'Novo Nordisk','MRNA':'Moderna','PFE':'Pfizer','ABBV':'AbbVie','JNJ':'Johnson & Johnson','ISRG':'Intuitive Surgical','UNH':'UnitedHealth Group','AMGN':'Amgen','GILD':'Gilead Sciences',
    'JPM':'JPMorgan Chase','BAC':'Bank of America','GS':'Goldman Sachs','MS':'Morgan Stanley','V':'Visa','MA':'Mastercard','WMT':'Walmart','COST':'Costco Wholesale','HD':'Home Depot','LOW':'Lowe’s',
    'XOM':'Exxon Mobil','CVX':'Chevron','COP':'ConocoPhillips','SLB':'SLB','NFLX':'Netflix','DIS':'Walt Disney','PEP':'PepsiCo','KO':'Coca-Cola'
}

def company_name(symbol: str) -> str:
    return COMPANY_NAMES.get(symbol, symbol)
