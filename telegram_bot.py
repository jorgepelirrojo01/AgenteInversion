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
    /diagnostico           -> estado interno del sistema (tokens, secrets, workflow)
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
    MADRID_TZ = None

import requests

from market import get_price_con_diagnostico, get_historical_price

REPO_DIR = os.path.dirname(__file__)
STATE_PATH = os.path.join(REPO_DIR, "portfolio_state.json")
CONFIG_PATH = os.path.join(REPO_DIR, "bot_config.json")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")

BENCHMARK_TICKER = "VWCE.DE"
UMBRAL_ALERTA_PCT = 5.0
TELEGRAM_MAX_CHARS = 3900


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
    state = load_json(STATE_PATH, {})
    return float(state.get("capital_inicial", 10000.0))


def ahora_madrid() -> datetime:
    if MADRID_TZ is not None:
        return datetime.now(MADRID_TZ)
    return datetime.now(timezone.utc) + timedelta(hours=2)


def get_price(ticker: str) -> float:
    """Wrapper simple para codigo que solo necesita el precio, sin diagnostico."""
    price, motivo = get_price_con_diagnostico(ticker)
    if price is None:
        raise ValueError(motivo)
    return price


def current_total_value():
    """
    Devuelve (total, detalle, precios_actuales, state).
    'detalle' es una lista de (ticker, valor, es_estimado, motivo) -- motivo
    es None si el precio es real, o el texto explicando por que se uso un
    fallback si no se pudo consultar el precio de mercado.
    """
    state = load_json(STATE_PATH, {})
    total = state.get("cash", 0.0)
    detalle = []
    precios_actuales = {}
    for ticker, pos in state.get("positions", {}).items():
        price, motivo = get_price_con_diagnostico(ticker)
        es_estimado = price is None
        if es_estimado:
            price = pos.get("avg_price", 0.0)
        precios_actuales[ticker] = price
        valor = price * pos["shares"]
        total += valor
        detalle.append((ticker, valor, es_estimado, motivo))
    return total, detalle, precios_actuales, state


def pct_change(old, new):
    if old in (None, 0):
        return None
    return (new / old - 1) * 100


def historical_portfolio_value(state, days_ago: int, precios_actuales: dict):
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
    for ticker, valor, es_estimado, motivo in detalle:
        if es_estimado:
            hay_estimados = True
            lineas.append(f"{ticker}: ~{valor:.2f} EUR ⚠️ (precio no disponible; motivo: {motivo})")
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
        mensaje += "\n\n⚠️ Uno o mas precios no se pudieron consultar en tiempo real (ver motivo arriba). El valor total puede estar algo desactualizado para esas posiciones."
    return mensaje


def build_composicion_message() -> str:
    total, detalle, _, state = current_total_value()
    if total <= 0:
        return "Cartera vacia."
    cash = state.get("cash", 0.0)
    lineas = [f"Cash: {cash:.2f} EUR ({cash / total * 100:.1f}%)"]
    for ticker, valor, es_estimado, motivo in detalle:
        marca = f" ⚠️ (estimado: {motivo})" if es_estimado else ""
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
    except Exception as e:
        return f"No se pudo obtener el precio de {BENCHMARK_TICKER} para comparar. Motivo: {e}"

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


def build_diagnostico_message() -> str:
    """
    Comando /diagnostico: informa del estado interno del sistema, para poder
    depurar sin tener que ir a mirar logs de GitHub Actions.
    Nunca revela valores de tokens/secrets, solo si existen y su longitud.
    """
    lineas = ["*Diagnostico del sistema*\n"]

    # Secrets / tokens (solo longitud, nunca el valor)
    lineas.append("*Credenciales:*")
    lineas.append(f"TELEGRAM_BOT_TOKEN: {'presente' if TELEGRAM_BOT_TOKEN else 'FALTA'} ({len(TELEGRAM_BOT_TOKEN or '')} caracteres)")
    lineas.append(f"TELEGRAM_CHAT_ID: {'presente' if TELEGRAM_CHAT_ID else 'FALTA'}")
    if GITHUB_TOKEN:
        prefijo = GITHUB_TOKEN[:8] if len(GITHUB_TOKEN) >= 8 else "?"
        aviso = ""
        if len(GITHUB_TOKEN) > 150:
            aviso = " ⚠️ LONGITUD SOSPECHOSA (deberia ser ~93, revisa si esta duplicado/corrupto)"
        lineas.append(f"GITHUB_TOKEN (deberia ser tu PAT_TOKEN): presente, {len(GITHUB_TOKEN)} caracteres, prefijo '{prefijo}...'{aviso}")
    else:
        lineas.append("GITHUB_TOKEN: FALTA")
    lineas.append(f"GITHUB_REPOSITORY: {GITHUB_REPOSITORY or 'FALTA'}")

    # Config del bot
    config = load_json(CONFIG_PATH, {})
    lineas.append("\n*Config del bot:*")
    lineas.append(f"pausado: {config.get('pausado')}")
    lineas.append(f"hora_aviso: {config.get('hora_aviso')}")
    lineas.append(f"ultimo_envio: {config.get('ultimo_envio')}")
    lineas.append(f"propuesta_pendiente: {'si' if config.get('propuesta_pendiente') else 'no'}")
    lineas.append(f"offset (Telegram): {config.get('offset')}")

    # Estado de la cartera
    state = load_json(STATE_PATH, {})
    lineas.append("\n*Cartera:*")
    lineas.append(f"created_at: {state.get('created_at')}")
    lineas.append(f"capital_inicial: {state.get('capital_inicial')}")
    lineas.append(f"num. posiciones: {len(state.get('positions', {}))}")
    lineas.append(f"num. transacciones: {len(state.get('transactions', []))}")
    lineas.append(f"num. snapshots: {len(state.get('snapshots', []))}")

    # Prueba en vivo de precios (detecta tickers problematicos como AGGH.L)
    lineas.append("\n*Precios en vivo (por posicion):*")
    for ticker in state.get("positions", {}).keys():
        price, motivo = get_price_con_diagnostico(ticker)
        if price is not None:
            lineas.append(f"{ticker}: OK ({price:.2f})")
        else:
            lineas.append(f"{ticker}: FALLO -> {motivo}")

    return "\n".join(lineas)


def send_telegram(text: str):
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


def send_telegram_largo(texto: str):
    """Si el texto es mas largo que el limite, lo divide en varias partes numeradas."""
    if len(texto) <= TELEGRAM_MAX_CHARS:
        send_telegram(texto)
        return
    trozos = []
    resto = texto
    while resto:
        trozos.append(resto[:TELEGRAM_MAX_CHARS])
        resto = resto[TELEGRAM_MAX_CHARS:]
    total = len(trozos)
    for i, trozo in enumerate(trozos, start=1):
        cabecera = f"[Parte {i}/{total}]\n\n" if total > 1 else ""
        send_telegram(cabecera + trozo)


def check_alerts(config):
    state = load_json(STATE_PATH, {})
    hoy_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    alertados_hoy = config.get("alertados_hoy", {})
    alertados_hoy = {t: d for t, d in alertados_hoy.items() if d == hoy_str}

    for ticker, pos in state.get("positions", {}).items():
        if alertados_hoy.get(ticker) == hoy_str:
            continue
        price, motivo = get_price_con_diagnostico(ticker)
        if price is None:
            continue  # no podemos evaluar variacion sin precio real
        try:
            precio_ayer = get_historical_price(ticker, 1)
        except Exception:
            continue
        variacion = pct_change(precio_ayer, price)
        if variacion is not None and abs(variacion) >= UMBRAL_ALERTA_PCT:
            direccion = "subido" if variacion > 0 else "caido"
            send_telegram(f"⚠️ *Alerta*: {ticker} ha {direccion} un *{variacion:+.2f}%* en las ultimas 24h.")
            alertados_hoy[ticker] = hoy_str

    config["alertados_hoy"] = alertados_hoy
    return config


def _dispatch_agente(mensaje: str, modo: str):
    """
    Lanza el workflow del agente. Devuelve (status_code, detalle_error).
    detalle_error es None si status_code == 204 (exito).
    """
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return 0, "Falta GITHUB_TOKEN o GITHUB_REPOSITORY en el entorno del bot (revisa los secrets del repo)."
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/workflows/agente-semanal.yml/dispatches"
    try:
        resp = requests.post(url, headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }, json={"ref": "main", "inputs": {"mensaje": mensaje[:500], "modo": modo}}, timeout=20)
        if resp.status_code == 204:
            return 204, None
        detalle = f"HTTP {resp.status_code}. Respuesta de GitHub: {resp.text[:300]}"
        return resp.status_code, detalle
    except Exception as e:
        return -1, f"Excepcion de red al llamar a la API de GitHub: {e}"


def start_proposal(instrucciones: str):
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        send_telegram("No se pudo pedir la propuesta: falta configuracion de GitHub (revisa /diagnostico).")
        return
    code, detalle = _dispatch_agente(instrucciones, "proponer")
    if code == 204:
        config = load_json(CONFIG_PATH, {})
        config["propuesta_pendiente"] = {"instrucciones": instrucciones[:500], "fecha": datetime.now(timezone.utc).isoformat()}
        save_json(CONFIG_PATH, config)
        send_telegram("Pidiendo al agente una propuesta (sin ejecutar nada aun). Te mando el plan en unos minutos (puede tardar mas si hace analisis de mercado completo). Luego responde /confirmar o /cancelar.")
    else:
        send_telegram(f"No se pudo pedir la propuesta.\n\nMotivo: {detalle}\n\nUsa /diagnostico para revisar el estado de las credenciales.")


def confirm_proposal():
    config = load_json(CONFIG_PATH, {})
    propuesta = config.get("propuesta_pendiente")
    if not propuesta:
        send_telegram("No hay ninguna propuesta pendiente de confirmar.")
        return
    mensaje = propuesta["instrucciones"] + " (Ya propusiste un plan para esto antes: ejecutalo ahora de verdad.)"
    code, detalle = _dispatch_agente(mensaje, "ejecutar")
    if code == 204:
        config["propuesta_pendiente"] = None
        save_json(CONFIG_PATH, config)
        send_telegram("Ejecutando el plan confirmado. Te aviso con el resumen en unos minutos.")
    else:
        send_telegram(f"No se pudo ejecutar.\n\nMotivo: {detalle}\n\nLa propuesta sigue pendiente, prueba /confirmar de nuevo tras revisar /diagnostico.")


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
    elif text.startswith("/diagnostico"):
        send_telegram_largo(build_diagnostico_message())
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
            "/diagnostico - estado interno del sistema (para depurar)\n"
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
                send_telegram(f"Hubo un error procesando ese comando ({e}), pero sigo funcionando.")

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


def _envio_de_emergencia(texto: str):
    """
    Envio minimo que no depende de nada mas del modulo (ni de bot_config.json,
    ni de current_total_value, ni de nada que pueda estar roto). Solo necesita
    las variables de entorno de Telegram, que ya se validaron al importar el
    modulo (si faltaran, el proceso habria muerto antes de llegar aqui con un
    KeyError, y eso lo cubre el workflow con el paso 'if: always()').
    """
    try:
        import requests as _requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        _requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": texto}, timeout=15)
    except Exception:
        pass  # si ni esto funciona, ya no hay nada mas que podamos hacer desde Python


if __name__ == "__main__":
    try:
        cfg = poll_updates()
        if not cfg.get("pausado"):
            cfg = check_alerts(cfg)
            save_json(CONFIG_PATH, cfg)
        check_scheduled_send(cfg)
    except Exception as e:
        import traceback
        detalle = traceback.format_exc()[-1500:]
        print(f"[ERROR CRITICO] El bot fallo de forma inesperada: {e}\n{detalle}")
        _envio_de_emergencia(
            f"⚠️ El bot de Telegram ha fallado con un error inesperado y no pudo "
            f"completar su ciclo normal.\n\nTipo: {type(e).__name__}\n"
            f"Detalle: {str(e)[:400]}\n\n"
            f"Esto es un aviso de emergencia; revisa el log de Actions para el "
            f"detalle completo. Si ves este mensaje repetido cada 10 minutos, "
            f"algo esta roto de forma persistente."
        )
        raise  # relanzamos para que Actions marque la ejecucion como fallida (visible en rojo)
