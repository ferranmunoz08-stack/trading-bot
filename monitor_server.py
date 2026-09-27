"""
monitor_server.py
------------------
Versió pensada per córrer 24/7 en un servidor (Render, etc.), no al teu
ordinador. En comptes de vigilar UNA posició amb valors fixos, vigila
TOTES les posicions obertes del teu compte de Paper Trading i calcula
el Stop-Loss / Take-Profit a partir del preu mitjà de compra (avg_entry_price)
que ja guarda Alpaca. Així, si el servidor es reinicia, no es perd res:
tot es recalcula a partir de les dades reals del compte.

Configuració del risc (percentatges fixos aplicats a CADA posició):
"""

import os
import time
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    raise RuntimeError("Falten ALPACA_API_KEY / ALPACA_SECRET_KEY (variables d'entorn).")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)

# ---------------------------------------------------------------------------
# Configuració del risc (ajusta-ho segons la teva estratègia)
# ---------------------------------------------------------------------------
STOP_LOSS_PCT = 0.05     # ven si el preu cau un 5% respecte al preu de compra
TAKE_PROFIT_PCT = 0.10   # ven si el preu puja un 10% respecte al preu de compra
INTERVAL_SEGONS = 60     # cada quant es comprova el preu (segons)


def vendre(symbol: str, qty: str):
    order_data = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    ordre = trading_client.submit_order(order_data)
    print(f"✅ VENUDA: {qty} de {symbol} (ID ordre: {ordre.id})", flush=True)


def revisar_posicions():
    posicions = trading_client.get_all_positions()
    if not posicions:
        print("Sense posicions obertes ara mateix.", flush=True)
        return

    for p in posicions:
        preu_entrada = float(p.avg_entry_price)
        preu_actual = float(p.current_price)
        stop_loss = preu_entrada * (1 - STOP_LOSS_PCT)
        take_profit = preu_entrada * (1 + TAKE_PROFIT_PCT)

        print(
            f"{p.symbol}: entrada={preu_entrada:.2f}$ actual={preu_actual:.2f}$ "
            f"SL={stop_loss:.2f}$ TP={take_profit:.2f}$ "
            f"P&L={p.unrealized_pl}$",
            flush=True,
        )

        if preu_actual <= stop_loss:
            print(f"🔴 {p.symbol}: Stop-Loss tocat.", flush=True)
            vendre(p.symbol, p.qty)
        elif preu_actual >= take_profit:
            print(f"🟢 {p.symbol}: Take-Profit tocat.", flush=True)
            vendre(p.symbol, p.qty)


if __name__ == "__main__":
    print("🚀 Monitor iniciat. Vigilant posicions cada "
          f"{INTERVAL_SEGONS}s...", flush=True)
    while True:
        try:
            revisar_posicions()
        except Exception as e:
            # No aturem mai el bucle per un error puntual (p. ex. de xarxa)
            print(f"⚠️ Error durant la revisió: {e}", flush=True)
        time.sleep(INTERVAL_SEGONS)
