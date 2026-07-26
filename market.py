"""
Modulo comun de acceso a datos de mercado (yfinance).
Lo usan tanto tools.py (el agente) como telegram_bot.py, para que ambos
valoren la cartera exactamente con la misma logica.

IMPORTANTE (aprendido de bugs reales): cuando un precio no se puede obtener,
NUNCA se debe devolver un valor silencioso (como el coste de compra) sin dejar
constancia de que fue un fallback. Por eso get_price_con_diagnostico() devuelve
tambien un motivo legible, para que los mensajes al usuario puedan explicar
"por que" un valor es una estimacion en vez de un dato real.
"""

from datetime import datetime, timedelta, timezone


def _intentar_fast_info(ticker: str):
    import yfinance as yf
    try:
        fi = yf.Ticker(ticker).fast_info
        price = fi.get("lastPrice") if hasattr(fi, "get") else None
        if price is not None and price > 0:
            return price, None
        return None, "fast_info sin precio valido"
    except Exception as e:
        return None, f"fast_info fallo: {e}"


def _intentar_historial_5d(ticker: str):
    import yfinance as yf
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            if price > 0:
                return price, None
        return None, "historial de 5 dias vacio o invalido"
    except Exception as e:
        return None, f"historial 5d fallo: {e}"


def _intentar_info(ticker: str):
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
        if price is not None and price > 0:
            return price, None
        return None, ".info sin precio valido"
    except Exception as e:
        return None, f".info fallo: {e}"


def get_price_con_diagnostico(ticker: str):
    """
    Devuelve (precio, motivo_fallback). Si precio no es None, motivo_fallback
    es None (fue un exito real). Si precio es None, motivo_fallback explica
    por que fallaron los 3 metodos, para poder informar al usuario.
    """
    motivos = []
    for metodo in (_intentar_fast_info, _intentar_historial_5d, _intentar_info):
        price, motivo = metodo(ticker)
        if price is not None:
            return price, None
        motivos.append(motivo)
    return None, " | ".join(motivos)


def get_price(ticker: str) -> float:
    """Precio actual del ticker. Lanza ValueError con el motivo si no hay dato valido."""
    price, motivo = get_price_con_diagnostico(ticker)
    if price is None:
        raise ValueError(f"No se encontro un precio valido para {ticker}: {motivo}")
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

    try:
        hist = yf.Ticker(ticker).history(period=period)
    except Exception as e:
        raise ValueError(f"Sin historico para {ticker}: excepcion al consultar yfinance ({e})")

    if hist.empty:
        raise ValueError(f"Sin historico para {ticker}: yfinance devolvio historial vacio (period={period})")

    target = pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=days_ago))
    idx = hist.index
    idx_utc = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
    diffs = abs(idx_utc - target)
    closest = diffs.argmin()
    price = float(hist["Close"].iloc[closest])
    if price <= 0:
        raise ValueError(f"Precio historico invalido para {ticker}: {price}")
    return price
