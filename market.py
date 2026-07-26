"""
Modulo comun de acceso a datos de mercado (yfinance).
Lo usan tanto tools.py (el agente) como telegram_bot.py, para que ambos
valoren la cartera exactamente con la misma logica.
"""

from datetime import datetime, timedelta, timezone


def get_price(ticker: str) -> float:
    """
    Precio actual del ticker. Prueba 3 metodos en orden (el mas rapido primero):
    fast_info -> historico de 5 dias -> .info (mas lento, pero a veces tiene
    datos cuando los otros dos fallan, especialmente en ETFs poco liquidos).
    Lanza ValueError si ninguno da un precio valido.
    """
    import yfinance as yf
    t = yf.Ticker(ticker)

    price = None
    try:
        fi = t.fast_info
        price = fi.get("lastPrice") if hasattr(fi, "get") else None
    except Exception:
        price = None

    if price is None or price <= 0:
        try:
            hist = t.history(period="5d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
        except Exception:
            price = None

    if price is None or price <= 0:
        try:
            info = t.info
            price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
        except Exception:
            price = None

    if price is None or price <= 0:
        raise ValueError(f"No se encontro un precio valido para {ticker}")
    return float(price)


def get_historical_price(ticker: str, days_ago: int) -> float:
    """
    Precio de cierre mas cercano a 'days_ago' dias atras.
    Ajusta la ventana pedida a yfinance segun cuan atras haya que mirar,
    para que funcione tambien a 3 y 6 meses vista (no solo semanas).
    """
    import yfinance as yf
    import pandas as pd

    if days_ago <= 25:
        period = "1mo"
    elif days_ago <= 80:
        period = "3mo"
    elif days_ago <= 170:
        period = "6mo"
    elif days_ago <= 350:
        period = "1y"
    else:
        period = "2y"

    hist = yf.Ticker(ticker).history(period=period)
    if hist.empty:
        raise ValueError(f"Sin historico para {ticker}")

    target = pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=days_ago))
    idx = hist.index
    idx_utc = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
    diffs = abs(idx_utc - target)
    closest = diffs.argmin()
    price = float(hist["Close"].iloc[closest])
    if price <= 0:
        raise ValueError(f"Precio historico invalido para {ticker}")
    return price
