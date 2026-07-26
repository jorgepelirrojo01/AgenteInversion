"""
Analisis fundamental y escaneo de mercado.
Universo: S&P 500 (EE.UU.) + grandes valores europeos.
Usa yfinance para datos reales: PER, dividendo, crecimiento, rango 52 semanas, etc.

IMPORTANTE: esto NO predice el mercado. Da al agente datos reales sobre los que
razonar, en vez de decidir "de memoria". Sigue siendo una simulacion educativa.
"""

from datetime import datetime, timezone
import pandas as pd


def _sp500_tickers():
    """Componentes del S&P 500 desde Wikipedia (se actualiza solo)."""
    try:
        tablas = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        df = tablas[0]
        return [str(s).replace(".", "-") for s in df["Symbol"].tolist()]
    except Exception:
        # Fallback minimo si Wikipedia falla: unas pocas grandes conocidas
        return ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "V", "JNJ", "WMT"]


# Grandes valores europeos (tickers de Yahoo, con su sufijo de mercado).
# Lista curada de blue chips liquidos; ampliable.
_EUROPE_TICKERS = [
    "ASML.AS", "MC.PA", "OR.PA", "SAP.DE", "SIE.DE", "AIR.PA", "SU.PA",
    "ALV.DE", "DTE.DE", "IBE.MC", "SAN.MC", "ITX.MC", "BBVA.MC", "NESN.SW",
    "NOVN.SW", "ROG.SW", "AZN.L", "SHEL.L", "HSBA.L", "ULVR.L", "RIO.L",
    "BP.L", "GSK.L", "DGE.L", "ENEL.MI", "ISP.MI", "ENI.MI", "STLAM.MI",
]


def universo_completo():
    """Devuelve la lista completa de tickers candidatos (S&P500 + Europa)."""
    return _sp500_tickers() + _EUROPE_TICKERS


def _safe(info, clave, default=None):
    v = info.get(clave, default)
    return v if v is not None else default


def fundamentales(ticker: str) -> dict:
    """
    Datos fundamentales de una empresa. Devuelve un dict con lo relevante
    para decidir, o {'error': ...} si no se pudo obtener.
    """
    import yfinance as yf
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not info or info.get("regularMarketPrice") is None:
            # A veces info viene vacio; intentamos fast_info como minimo
            price = None
            try:
                price = t.fast_info.get("lastPrice")
            except Exception:
                price = None
            if price is None:
                return {"ticker": ticker, "error": "sin datos"}
            return {"ticker": ticker, "precio": float(price), "parcial": True}

        precio = _safe(info, "currentPrice") or _safe(info, "regularMarketPrice")
        high52 = _safe(info, "fiftyTwoWeekHigh")
        low52 = _safe(info, "fiftyTwoWeekLow")
        dist_max = None
        if precio and high52:
            dist_max = (precio / high52 - 1) * 100  # % respecto a maximo de 52s

        return {
            "ticker": ticker,
            "nombre": _safe(info, "shortName", ticker),
            "sector": _safe(info, "sector", "?"),
            "pais": _safe(info, "country", "?"),
            "precio": precio,
            "moneda": _safe(info, "currency", "?"),
            "per": _safe(info, "trailingPE"),               # precio/beneficio
            "per_futuro": _safe(info, "forwardPE"),
            "peg": _safe(info, "pegRatio"),                 # PER ajustado a crecimiento
            "div_yield_pct": (_safe(info, "dividendYield") or 0) * 100,
            "crecimiento_ingresos_pct": (_safe(info, "revenueGrowth") or 0) * 100,
            "crecimiento_beneficios_pct": (_safe(info, "earningsGrowth") or 0) * 100,
            "margen_beneficio_pct": (_safe(info, "profitMargins") or 0) * 100,
            "capitalizacion": _safe(info, "marketCap"),
            "beta": _safe(info, "beta"),                    # volatilidad vs mercado
            "max_52s": high52,
            "min_52s": low52,
            "dist_a_max_pct": dist_max,                     # negativo = por debajo del maximo
            "recomendacion": _safe(info, "recommendationKey", "?"),
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def escanear(perfil: str = "mixto", max_candidatas: int = 25, universo_muestra: int = 120) -> list:
    """
    Cribado rapido del universo para encontrar candidatas segun un perfil.
    Para no tardar/costar demasiado, muestrea 'universo_muestra' tickers del
    universo completo y les aplica un filtro numerico barato.

    perfil:
      - 'crecimiento': prioriza crecimiento de ingresos/beneficios alto
      - 'valor': prioriza PER bajo y precio lejos de maximos
      - 'dividendo': prioriza dividendo alto
      - 'mixto': combina las tres cosas
    Devuelve lista de dicts fundamentales de las mejores candidatas.
    """
    import random
    universo = universo_completo()
    random.shuffle(universo)
    muestra = universo[:universo_muestra]

    resultados = []
    for tk in muestra:
        f = fundamentales(tk)
        if "error" in f or f.get("parcial"):
            continue
        if not f.get("precio"):
            continue
        resultados.append(f)

    def puntuar(f):
        per = f.get("per") or 999
        crec = f.get("crecimiento_beneficios_pct") or 0
        div = f.get("div_yield_pct") or 0
        dist = f.get("dist_a_max_pct") or 0  # mas negativo = mas "barato" respecto a su max

        if perfil == "crecimiento":
            return crec * 2 + max(0, 30 - per)  # premia crecimiento, penaliza PER altisimo
        if perfil == "valor":
            return max(0, 30 - per) * 2 - dist  # premia PER bajo y estar lejos del maximo
        if perfil == "dividendo":
            return div * 3 + max(0, 20 - per)
        # mixto
        return crec + div + max(0, 25 - per) - dist * 0.3

    resultados.sort(key=puntuar, reverse=True)
    return resultados[:max_candidatas]
