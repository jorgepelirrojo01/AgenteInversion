"""
Muestra la evolucion de la cartera simulada a partir de los snapshots guardados.
Uso: python ver_evolucion.py
"""

import json
import os

STATE_PATH = os.path.join(os.path.dirname(__file__), "portfolio_state.json")

with open(STATE_PATH, "r", encoding="utf-8") as f:
    state = json.load(f)

capital_inicial = float(state.get("capital_inicial", 10000.0))
print(f"Capital inicial: {capital_inicial:.2f} EUR ({state.get('created_at', '?')})\n")
print(f"{'Fecha':<12} {'Valor total':>14} {'Rentabilidad':>14}  Nota")
print("-" * 72)

for snap in state.get("snapshots", []):
    valor = snap["total_value"]
    rentabilidad = (valor / capital_inicial - 1) * 100
    print(f"{snap['date']:<12} {valor:>12.2f} EUR {rentabilidad:>+12.2f} %  {snap.get('note', '')}")

print("\nTransacciones realizadas:")
if not state.get("transactions"):
    print("  (ninguna)")
for tx in state.get("transactions", []):
    print(f"  {tx['date']} | {tx['type'].upper():<4} {tx['ticker']:<10} "
          f"{tx['shares']:.6f} uds @ {tx['price']:.2f} ({tx['amount_eur']:.2f} EUR) - {tx.get('reason','')}")
