"""
monitor_once.py
----------------
Igual que monitor_server.py, però fa NOMÉS UNA comprovació i acaba
(no es queda en bucle). Pensat per executar-se periòdicament des de
GitHub Actions (cada 15 minuts), no des del teu ordinador.

Recalcula el Stop-Loss / Take-Profit de cada posició oberta a partir del
preu mitjà de compra (avg_entry_price), així que no cal guardar cap estat
entre execucions: cada vegada que s'executa, parteix de zero i mira les
dades reals del compte.
"""

import os
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    raise RuntimeError("Falten ALPACA_API_KEY / ALPACA_SECRET_KEY (variables d'entorn).")

# ⚠️ LIVE TRADING: paper=False -> diners REALS.
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=False)

# ---------------------------------------------------------------------------
# Configuració del risc (ajusta-ho segons la teva estratègia)
# ---------------------------------------------------------------------------
STOP_LOSS_PCT = 0.05     # ven si el preu cau un 5% respecte al preu de compra
TAKE_PROFIT_PCT = 0.10   # ven si el preu puja un 10% respecte al preu de compra


def vendre(symbol: str, qty: str):
    order_data = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    ordre = trading_client.submit_order(order_data)
    print(f"✅ VENUDA: {qty} de {symbol} (ID ordre: {ordre.id})")


def revisar_posicions():
    posicions = trading_client.get_all_positions()
    if not posicions:
        print("Sense posicions obertes ara mateix.")
        return

    for p in posicions:
        preu_entrada = float(p.avg_entry_price)
        preu_actual = float(p.current_price)
        stop_loss = preu_entrada * (1 - STOP_LOSS_PCT)
        take_profit = preu_entrada * (1 + TAKE_PROFIT_PCT)

        print(
            f"{p.symbol}: entrada={preu_entrada:.2f}$ actual={preu_actual:.2f}$ "
            f"SL={stop_loss:.2f}$ TP={take_profit:.2f}$ "
            f"P&L={p.unrealized_pl}$"
        )

        if preu_actual <= stop_loss:
            print(f"🔴 {p.symbol}: Stop-Loss tocat.")
            vendre(p.symbol, p.qty)
        elif preu_actual >= take_profit:
            print(f"🟢 {p.symbol}: Take-Profit tocat.")
            vendre(p.symbol, p.qty)


if __name__ == "__main__":
    print("🔎 Comprovació puntual de posicions...")
    revisar_posicions()
    print("✅ Comprovació acabada.")
