"""
Bot de Telegram del agente de inversion.
No usa la API de Claude para el estado/comandos normales -> coste 0 tokens.
Solo /rebalancear + /confirmar disparan una ejecucion real del agente
(via GitHub Actions), que si consume creditos de la API.

Comandos disponibles:
    /actualiza             -> estado actual + rentabilidad 24h / 7d / 30d
    /composicion           -> desglose en % de cada posicion
    /historial             -> ultimos movimientos realizados
    /comparar              -> vs. benchmark de comprar-y-mantener
    /hora HH:MM            -> cambia la hora del aviso diario (hora de Madrid)
    /pausar                -> silencia avisos y revisiones automaticas
    /reanudar              -> reactiva avisos y revisiones automaticas
    /rebalancear <texto>   -> pide una propuesta de cambios al agente (no ejecuta nada aun)
    /confirmar             -> ejecuta la ultima propuesta pendiente de /rebalancear
    /cancelar              -> descarta la propuesta pendiente
    /help                  -> lista de comandos
"""

import json
import os
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    MADRID_TZ = ZoneInfo("Europe/Madrid")
except Exception:
    MADRID_TZ = None  # fallback si el sistema no tiene tzdata

import requests

from market import get_price, get_historical_price

REPO_DIR = os.path.dirname(__file__)
STATE_PATH = os.path.join(REPO_DIR, "portfolio_state.json")
CONFIG_PATH = os.path.join(REPO_DIR, "bot_config.json")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")  # owner/repo, lo pone Actions solo

BENCHMARK_TICKER = "VWCE.DE"   # usado por /comparar
UMBRAL_ALERTA_PCT = 5.0        # variacion en 24h que dispara una alerta automatica


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def capital_inicial() -> float:
    """Lee el capital inicial del propio estado (fuente unica de verdad)."""
    state = load_json(STATE_PATH, {})
    return float(state.get("capital_inicial", 10000.0))


def ahora_madrid() -> datetime:
    if MADRID_TZ is not None:
        return datetime.now(MADRID_TZ)
    # Fallback aproximado si no hay tzdata: UTC+2 (horario de verano)
    return datetime.now(timezone.utc) + timedelta(hours=2)


def current_total_value():
    """
    Devuelve (total, detalle, precios_actuales, state).
    'detalle' es una lista de (ticker, valor, es_estimado) -- es_estimado=True
    cuando no se pudo obtener el precio real y se uso el coste de compra como
    aproximacion (para no ocultar ese hecho en los mensajes).
    """
    state = load_json(STATE_PATH, {})
    total = state.get("cash", 0.0)
    detalle = []
    precios_actuales = {}
    for ticker, pos in state.get("positions", {}).items():
        es_estimado = False
        try:
            price = get_price(ticker)
        except Exception:
            price = pos.get("avg_price", 0.0)  # fallback: precio de compra
            es_estimado = True
        precios_actuales[ticker] = price
        valor = price * pos["shares"]
        total += valor
        detalle.append((ticker, valor, es_estimado))
    return total, detalle, precios_actuales, state


def pct_change(old, new):
    if old in (None, 0):
        return None
    return (new / old - 1) * 100


def historical_portfolio_value(state, days_ago: int, precios_actuales: dict):
    """
    Valor aproximado de la cartera hace N dias: posiciones ACTUALES valoradas
    al precio de aquel momento. Si falla el historico de un ticker, se usa su
    precio actual (se asume "sin cambio") en vez de excluirlo, que distorsionaria
    la comparacion.
    """
    total = state.get("cash", 0.0)
    for ticker, pos in state.get("positions", {}).items():
        try:
            price = get_historical_price(ticker, days_ago)
        except Exception:
            price = precios_actuales.get(ticker, pos.get("avg_price", 0.0))
        total += price * pos["shares"]
    return total


def dias_desde_creacion(state):
    created_at = state.get("created_at")
    if not created_at:
        return None
    try:
        return (datetime.now(timezone.utc).date() - datetime.strptime(created_at, "%Y-%m-%d").date()).days
    except ValueError:
        return None


def build_message() -> str:
    total, detalle, precios_actuales, state = current_total_value()
    cap = capital_inicial()
    rentabilidad_total = pct_change(cap, total)
    dias = dias_desde_creacion(state)

    def valor_hace(d):
        if dias is None or dias < d:
            return None
        return historical_portfolio_value(state, d, precios_actuales)

    v24h, v7d, v30d = valor_hace(1), valor_hace(7), valor_hace(30)

    def fmt(r):
        return f"{r:+.2f}%" if r is not None else "sin datos suficientes (cartera muy reciente)"

    lineas = [f"Cash: {state.get('cash', 0):.2f} EUR"]
    hay_estimados = False
    for ticker, valor, es_estimado in detalle:
        if es_estimado:
            hay_estimados = True
            lineas.append(f"{ticker}: ~{valor:.2f} EUR ⚠️ (precio no disponible, usando coste de compra)")
        else:
            lineas.append(f"{ticker}: {valor:.2f} EUR")

    mensaje = (
        f"*Estado de la cartera*\n\n"
        f"Valor total: *{total:.2f} EUR*\n"
        f"Rentabilidad total: *{fmt(rentabilidad_total)}*\n"
        f"Ultimas 24h: *{fmt(pct_change(v24h, total))}*\n"
        f"Ultima semana: *{fmt(pct_change(v7d, total))}*\n"
        f"Ultimo mes: *{fmt(pct_change(v30d, total))}*\n\n"
        + "\n".join(lineas)
    )
    if hay_estimados:
        mensaje += "\n\n⚠️ Algun precio no se pudo consultar en tiempo real; el valor total puede estar algo desactualizado para esas posiciones."
    return mensaje


def build_composicion_message() -> str:
    total, detalle, _, state = current_total_value()
    if total <= 0:
        return "Cartera vacia."
    cash = state.get("cash", 0.0)
    lineas = [f"Cash: {cash:.2f} EUR ({cash / total * 100:.1f}%)"]
    for ticker, valor, es_estimado in detalle:
        marca = " ⚠️ (estimado)" if es_estimado else ""
        lineas.append(f"{ticker}: {valor:.2f} EUR ({valor / total * 100:.1f}%){marca}")
    return f"*Composicion de la cartera*\n\nValor total: *{total:.2f} EUR*\n\n" + "\n".join(lineas)


def build_historial_message(n: int = 8) -> str:
    state = load_json(STATE_PATH, {})
    transacciones = state.get("transactions", [])
    if not transacciones:
        return "Todavia no hay ningun movimiento registrado."
    ultimas = transacciones[-n:][::-1]
    lineas = []
    for tx in ultimas:
        emoji = "🟢" if tx.get("type") == "buy" else "🔴"
        lineas.append(
            f"{emoji} {tx.get('date','?')} - {tx.get('type','?').upper()} {tx.get('ticker','?')} "
            f"({tx.get('amount_eur',0):.2f} EUR)\n   {tx.get('reason', '')}"
        )
    return f"*Ultimos {len(ultimas)} movimientos*\n\n" + "\n\n".join(lineas)


def build_comparar_message() -> str:
    total, _, _, state = current_total_value()
    cap = capital_inicial()
    dias = dias_desde_creacion(state)
    if dias is None:
        return "No hay fecha de inicio registrada para comparar."
    dias = max(dias, 1)

    try:
        precio_inicio = get_historical_price(BENCHMARK_TICKER, dias)
        precio_ahora = get_price(BENCHMARK_TICKER)
    except Exception:
        return f"No se pudo obtener el precio de {BENCHMARK_TICKER} para comparar."

    valor_benchmark = (cap / precio_inicio) * precio_ahora
    r_cartera = pct_change(cap, total)
    r_benchmark = pct_change(cap, valor_benchmark)
    diferencia = r_cartera - r_benchmark

    veredicto = (
        "El agente le esta ganando al mercado" if diferencia > 0
        else "El mercado (comprar y mantener) le esta ganando al agente" if diferencia < 0
        else "Empate tecnico con el mercado"
    )
    return (
        f"*Tu cartera vs. comprar-y-mantener {BENCHMARK_TICKER}*\n\n"
        f"Tu cartera: *{total:.2f} EUR* ({r_cartera:+.2f}%)\n"
        f"Solo {BENCHMARK_TICKER} desde el dia 1: *{valor_benchmark:.2f} EUR* ({r_benchmark:+.2f}%)\n\n"
        f"Diferencia: *{diferencia:+.2f} puntos*\n{veredicto}"
    )


def send_telegram(text: str):
    """Envia un mensaje. Si el Markdown falla (caracteres raros), reintenta en texto plano.
    Nunca lanza excepcion hacia arriba: un fallo de envio no debe romper el ciclo."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=20)
        if resp.status_code == 200:
            return
    except Exception:
        pass
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=20)
    except Exception as e:
        print(f"No se pudo enviar mensaje a Telegram: {e}")


def check_alerts(config):
    state = load_json(STATE_PATH, {})
    hoy_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    alertados_hoy = config.get("alertados_hoy", {})
    alertados_hoy = {t: d for t, d in alertados_hoy.items() if d == hoy_str}

    for ticker, pos in state.get("positions", {}).items():
        if alertados_hoy.get(ticker) == hoy_str:
            continue
        try:
            precio_ahora = get_price(ticker)
            precio_ayer = get_historical_price(ticker, 1)
        except Exception:
            continue
        variacion = pct_change(precio_ayer, precio_ahora)
        if variacion is not None and abs(variacion) >= UMBRAL_ALERTA_PCT:
            direccion = "subido" if variacion > 0 else "caido"
            send_telegram(f"⚠️ *Alerta*: {ticker} ha {direccion} un *{variacion:+.2f}%* en las ultimas 24h.")
            alertados_hoy[ticker] = hoy_str

    config["alertados_hoy"] = alertados_hoy
    return config


def _dispatch_agente(mensaje: str, modo: str) -> int:
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return 0
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/workflows/agente-semanal.yml/dispatches"
    try:
        resp = requests.post(url, headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }, json={"ref": "main", "inputs": {"mensaje": mensaje[:500], "modo": modo}}, timeout=20)
        return resp.status_code
    except Exception:
        return -1


def start_proposal(instrucciones: str):
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        send_telegram("No se pudo pedir la propuesta: falta configuracion de GitHub.")
        return
    code = _dispatch_agente(instrucciones, "proponer")
    if code == 204:
        config = load_json(CONFIG_PATH, {})
        config["propuesta_pendiente"] = {"instrucciones": instrucciones[:500], "fecha": datetime.now(timezone.utc).isoformat()}
        save_json(CONFIG_PATH, config)
        send_telegram("Pidiendo al agente una propuesta (sin ejecutar nada aun). Te mando el plan en 1-2 minutos. Luego responde /confirmar o /cancelar.")
    else:
        send_telegram(f"No se pudo pedir la propuesta (HTTP {code}).")


def confirm_proposal():
    config = load_json(CONFIG_PATH, {})
    propuesta = config.get("propuesta_pendiente")
    if not propuesta:
        send_telegram("No hay ninguna propuesta pendiente de confirmar.")
        return
    mensaje = propuesta["instrucciones"] + " (Ya propusiste un plan para esto antes: ejecutalo ahora de verdad.)"
    code = _dispatch_agente(mensaje, "ejecutar")
    if code == 204:
        config["propuesta_pendiente"] = None
        save_json(CONFIG_PATH, config)
        send_telegram("Ejecutando el plan confirmado. Te aviso con el resumen en 1-2 minutos.")
    else:
        send_telegram(f"No se pudo ejecutar (HTTP {code}). La propuesta sigue pendiente, prueba /confirmar de nuevo.")


def cancel_proposal():
    config = load_json(CONFIG_PATH, {})
    if config.get("propuesta_pendiente"):
        config["propuesta_pendiente"] = None
        save_json(CONFIG_PATH, config)
        send_telegram("Propuesta descartada. No se ha ejecutado nada.")
    else:
        send_telegram("No habia ninguna propuesta pendiente.")


def _set_config(clave, valor):
    config = load_json(CONFIG_PATH, {})
    config[clave] = valor
    save_json(CONFIG_PATH, config)


def handle_command(text: str):
    text = text.strip()
    if text.startswith("/hora"):
        partes = text.split(maxsplit=1)
        if len(partes) == 2:
            candidata = partes[1].strip()
            try:
                datetime.strptime(candidata, "%H:%M")
                _set_config("hora_aviso", candidata)
                send_telegram(f"Hora del aviso diario actualizada a las {candidata} (hora de Madrid).")
            except ValueError:
                send_telegram("Formato invalido. Uso: /hora HH:MM (ejemplo: /hora 09:30)")
        else:
            send_telegram("Uso: /hora HH:MM (ejemplo: /hora 11:00)")
    elif text.startswith("/actualiza") or text.startswith("/estado"):
        send_telegram(build_message())
    elif text.startswith("/composicion"):
        send_telegram(build_composicion_message())
    elif text.startswith("/historial"):
        send_telegram(build_historial_message())
    elif text.startswith("/comparar"):
        send_telegram(build_comparar_message())
    elif text.startswith("/pausar"):
        _set_config("pausado", True)
        send_telegram("Pausado. No recibiras avisos ni revisiones automaticas hasta que mandes /reanudar.")
    elif text.startswith("/reanudar"):
        _set_config("pausado", False)
        send_telegram("Reanudado. Avisos y revisiones automaticas activos de nuevo.")
    elif text.startswith("/rebalancear"):
        instrucciones = text[len("/rebalancear"):].strip()
        if not instrucciones:
            send_telegram("Uso: /rebalancear <lo que quieras pedirle al agente>")
        else:
            start_proposal(instrucciones)
    elif text.startswith("/confirmar"):
        confirm_proposal()
    elif text.startswith("/cancelar"):
        cancel_proposal()
    elif text.startswith("/help") or text.startswith("/start"):
        send_telegram(
            "*Comandos disponibles*\n\n"
            "/actualiza - estado + rentabilidad 24h/7d/30d\n"
            "/composicion - desglose en % de cada posicion\n"
            "/historial - ultimos movimientos\n"
            "/comparar - tu cartera vs comprar-y-mantener\n"
            "/hora HH:MM - cambia la hora del aviso diario\n"
            "/pausar - silencia avisos y revisiones\n"
            "/reanudar - reactiva avisos y revisiones\n"
            "/rebalancear <texto> - pide una propuesta (no ejecuta)\n"
            "/confirmar - ejecuta la propuesta pendiente\n"
            "/cancelar - descarta la propuesta pendiente\n"
        )


def poll_updates():
    config = load_json(CONFIG_PATH, {"hora_aviso": "11:00", "offset": 0, "ultimo_envio": None})
    offset = config.get("offset", 0)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        resp = requests.get(url, params={"offset": offset, "timeout": 0}, timeout=25)
        data = resp.json()
    except Exception as e:
        print(f"No se pudo consultar Telegram: {e}")
        return config

    for update in data.get("result", []):
        offset = update["update_id"] + 1
        message = update.get("message", {})
        text = message.get("text", "")
        chat_id = str(message.get("chat", {}).get("id", ""))
        if text and chat_id == TELEGRAM_CHAT_ID:
            try:
                handle_command(text)
            except Exception as e:
                print(f"Error procesando '{text[:40]}': {e}")
                send_telegram("Hubo un error procesando ese comando, pero sigo funcionando.")

    config = load_json(CONFIG_PATH, config)
    config["offset"] = offset
    save_json(CONFIG_PATH, config)
    return config


def check_scheduled_send(config):
    if config.get("pausado"):
        return
    hora_aviso = config.get("hora_aviso", "11:00")
    ultimo_envio = config.get("ultimo_envio")

    ahora = ahora_madrid()
    hoy_str = ahora.strftime("%Y-%m-%d")
    try:
        hora_objetivo = datetime.strptime(hora_aviso, "%H:%M").time()
    except ValueError:
        hora_objetivo = datetime.strptime("11:00", "%H:%M").time()

    objetivo_dt = ahora.replace(hour=hora_objetivo.hour, minute=hora_objetivo.minute, second=0, microsecond=0)
    ya_paso_la_hora = ahora >= objetivo_dt
    no_enviado_hoy = ultimo_envio != hoy_str

    if ya_paso_la_hora and no_enviado_hoy:
        send_telegram(build_message())
        config["ultimo_envio"] = hoy_str
        save_json(CONFIG_PATH, config)


if __name__ == "__main__":
    cfg = poll_updates()
    if not cfg.get("pausado"):
        cfg = check_alerts(cfg)
        save_json(CONFIG_PATH, cfg)
    check_scheduled_send(cfg)
