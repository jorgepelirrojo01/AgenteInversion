# Agente de inversion simulado (10000 EUR ficticios)

Proyecto educativo para aprender a construir agentes con el Claude Agent SDK.
El agente gestiona una cartera simulada, sin dinero real, usando precios de
mercado reales (via yfinance).

## 1. Instalacion

Requisitos: Python 3.10+, Node.js 18+ (lo usa el SDK internamente).

```bash
cd investment_agent
python -m venv venv
source venv/bin/activate   # en Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Configura tu API key de Anthropic (o usa tu sesion de Claude Code si ya tienes Pro/Max):

```bash
export ANTHROPIC_API_KEY="tu-api-key"   # Linux/Mac
setx ANTHROPIC_API_KEY "tu-api-key"     # Windows
```

## 2. Primera sesion

```bash
python agent.py "Es la primera sesion. Analiza el mercado actual y define tu estrategia inicial para los 10000 EUR."
```

El agente vera que la cartera esta vacia (todo en cash), decidira una estrategia,
ejecutara sus primeras compras y guardara un snapshot.

## 3. Sesiones periodicas

Para simular una gestion realista, ejecuta el agente periodicamente (por ejemplo,
una vez por semana):

```bash
python agent.py
```

Cada ejecucion es una sesion independiente: el agente relee el estado de
`portfolio_state.json`, decide si actuar, y guarda un nuevo snapshot.

### Automatizar con cron (Linux/Mac)

```bash
crontab -e
# Todos los lunes a las 9:00
0 9 * * 1 cd /ruta/a/investment_agent && venv/bin/python agent.py >> log.txt 2>&1
```

### Automatizar con el Programador de tareas (Windows)

Crea una tarea que ejecute `venv\Scripts\python.exe agent.py` con la frecuencia
que quieras (semanal, por ejemplo).

## 4. Ver la evolucion (a 1, 3, 6 meses...)

```bash
python ver_evolucion.py
```

Muestra una tabla con el valor total en cada snapshot, la rentabilidad acumulada
desde el inicio, y el historial completo de transacciones con el razonamiento
que dio el agente en cada una.

## 5. Estructura de archivos

- `portfolio_state.json` — estado de la cartera (cash, posiciones, transacciones, snapshots). Es la "memoria" del agente entre sesiones.
- `tools.py` — herramientas del agente: `get_price`, `get_portfolio`, `buy`, `sell`, `save_snapshot`.
- `agent.py` — punto de entrada: define el system prompt (rol, objetivo, reglas) y lanza una sesion.
- `ver_evolucion.py` — informe de rentabilidad y transacciones.

## 6.5. Comandos del bot de Telegram

| Comando | Que hace |
|---|---|
| `/actualiza` | Estado actual + rentabilidad total, 24h, 7d y 30d |
| `/composicion` | Desglose en % de cada posicion sobre el total |
| `/historial` | Ultimos movimientos realizados, con el motivo de cada uno |
| `/comparar` | Tu cartera vs. haber comprado y mantenido un ETF global desde el dia 1 |
| `/hora HH:MM` | Cambia la hora del aviso diario (hora de Madrid) |
| `/pausar` | Silencia avisos y desactiva la revision automatica semanal |
| `/reanudar` | Reactiva avisos y revision automatica |
| `/rebalancear <texto>` | Pide una PROPUESTA al agente (no ejecuta nada aun) |
| `/confirmar` | Ejecuta de verdad la ultima propuesta pendiente |
| `/cancelar` | Descarta la propuesta pendiente sin ejecutarla |
| `/help` | Lista de comandos |

Nota sobre `/pausar`: solo bloquea la revision automatica del cron semanal. Si lanzas el
workflow a mano desde Actions, o usas `/rebalancear`, se ejecuta igualmente — la pausa
es para "no quiero que decida nada solo mientras no le preste atencion", no un bloqueo total.

## 6. Alojarlo en GitHub Actions (sin ordenador ni servidor propio)

Esta es la forma de que el agente corra solo, en la nube, gratis, sin depender de
que tu PC este encendido.

### 6.1 Crear el repositorio

```bash
cd investment_agent
git init
git add .
git commit -m "Primera version del agente de inversion"
```

Crea un repo nuevo en github.com (puede ser privado) y conectalo:

```bash
git remote add origin https://github.com/TU_USUARIO/investment-agent.git
git branch -M main
git push -u origin main
```

### 6.2 Configurar el Secret con tu API key

La API key NUNCA se sube al codigo. Se guarda aparte, cifrada, en la
configuracion del propio repo:

1. En GitHub, entra a tu repo -> pestana **Settings**.
2. Menu lateral -> **Secrets and variables** -> **Actions**.
3. Boton **New repository secret**.
4. Name: `ANTHROPIC_API_KEY`
5. Value: tu API key de platform.claude.com
6. Guardar.

El workflow la usa automaticamente via `${{ secrets.ANTHROPIC_API_KEY }}` — nunca
aparece en logs ni en el codigo.

### 6.3 Verificar que el workflow esta activo

Ve a la pestana **Actions** de tu repo en GitHub. Deberias ver el workflow
"Revision semanal de cartera" listado. Si quieres probarlo sin esperar al
lunes, entra en el, pulsa **Run workflow** (boton a la derecha) y lanzalo
a mano.

### 6.4 Revisar resultados

Cada ejecucion queda registrada en la pestana Actions, con el log completo de
lo que hizo el agente (que consulto, que decidio, que compro/vendio y por que).
Ademas, cada semana veras un commit nuevo en el repo con el `portfolio_state.json`
actualizado — es tu historial versionado de la cartera.

Para ver la evolucion en formato resumen, clona el repo actualizado a tu PC
en cualquier momento y ejecuta:

```bash
git pull
python ver_evolucion.py
```

## Notas

- Esto es 100% simulado: no se conecta a ningun broker ni mueve dinero real.
- Los precios que usa (`yfinance`) son reales, asi que la rentabilidad simulada
  refleja lo que habria pasado con dinero real.
- Puedes editar el `SYSTEM_PROMPT` en `agent.py` para cambiar la estrategia
  (ej. mas conservadora, cripto incluido, solo ETFs, rebalanceo mensual fijo, etc.).
