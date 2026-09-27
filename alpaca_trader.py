"""
alpaca_trader.py
-----------------
Script base per connectar amb el teu compte de PAPER TRADING d'Alpaca.

Funcionalitats:
  1. Consultar saldo i informació del compte (equity, buying power, cash...)
  2. Consultar posicions obertes
  3. Consultar el preu actual d'un actiu
  4. Enviar una ordre de compra amb Stop-Loss i Take-Profit automàtics (bracket order)

Requisits:
  pip install alpaca-py python-dotenv

Configuració:
  Crea un fitxer .env (al mateix directori) amb:
    ALPACA_API_KEY=la_teva_key
    ALPACA_SECRET_KEY=la_teva_secret
  (Veure .env.example inclòs)

  IMPORTANT: no publiquis mai el fitxer .env ni les teves claus en cap
  repositori públic (GitHub, etc.). Afegeix .env al teu .gitignore.
"""

import os
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

# ---------------------------------------------------------------------------
# 1. Configuració i connexió
# ---------------------------------------------------------------------------
load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    raise RuntimeError(
        "Falten les claus API. Crea un fitxer .env amb ALPACA_API_KEY i "
        "ALPACA_SECRET_KEY (veure .env.example)."
    )

# ⚠️ LIVE TRADING: paper=False -> diners REALS. Assegura't que ALPACA_API_KEY
# i ALPACA_SECRET_KEY siguin les claus de LIVE (no les de Paper) abans d'usar-ho.
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=False)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# ---------------------------------------------------------------------------
# CAPITAL SIMULAT
# ---------------------------------------------------------------------------
# El teu compte de Paper Trading a Alpaca ve per defecte amb 100.000 $, i no
# sempre es pot canviar des del dashboard. Per practicar com si tinguessis
# només 100 $, definim aquí un "capital simulat" i calculem tots els riscos
# sobre aquesta xifra, ignorant el saldo real que retorna Alpaca.
CAPITAL_SIMULAT = 100.0          # el teu capital fictici de referència
RISC_MAXIM_PER_OPERACIO = 0.02   # 2% del capital simulat


# ---------------------------------------------------------------------------
# 2. Consultar el saldo / informació del compte
# ---------------------------------------------------------------------------
def mostrar_saldo():
    account = trading_client.get_account()
    print("\n=== SALDO DEL COMPTE (Paper Trading) ===")
    print(f"Equity total:        {account.equity} $")
    print(f"Cash disponible:     {account.cash} $")
    print(f"Buying power:        {account.buying_power} $")
    print(f"Bloquejat en ordres:  {account.initial_margin} $")
    print(f"Compte bloquejat?:   {account.trading_blocked}")
    return account


# ---------------------------------------------------------------------------
# 3. Consultar posicions obertes
# ---------------------------------------------------------------------------
def mostrar_posicions():
    posicions = trading_client.get_all_positions()
    print("\n=== POSICIONS OBERTES ===")
    if not posicions:
        print("No tens cap posició oberta ara mateix.")
        return []
    for p in posicions:
        print(
            f"{p.symbol}: {p.qty} accions | Preu mitjà entrada: {p.avg_entry_price} $ "
            f"| Preu actual: {p.current_price} $ | P&L: {p.unrealized_pl} $ "
            f"({float(p.unrealized_plpc) * 100:.2f}%)"
        )
    return posicions


# ---------------------------------------------------------------------------
# 4. Consultar preu actual d'un actiu
# ---------------------------------------------------------------------------
def preu_actual(symbol: str) -> float:
    req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
    quote = data_client.get_stock_latest_quote(req)[symbol]
    preu = float(quote.ask_price or quote.bid_price)
    print(f"\nPreu actual de {symbol}: {preu} $")
    return preu


# ---------------------------------------------------------------------------
# 5. Calcular la mida de la posició segons el capital SIMULAT (regla del 2%)
# ---------------------------------------------------------------------------
def calcular_quantitat(preu_entrada: float, preu_stop_loss: float) -> float:
    """
    Calcula quantes accions (poden ser fraccionades) comprar perquè, si es
    dispara l'Stop-Loss, la pèrdua no superi el 2% del capital SIMULAT
    (100 $ per defecte), encara que el compte real d'Alpaca tingui 100.000 $.
    """
    risc_maxim_euros = CAPITAL_SIMULAT * RISC_MAXIM_PER_OPERACIO
    risc_per_accio = preu_entrada - preu_stop_loss

    if risc_per_accio <= 0:
        raise ValueError(
            "El preu de Stop-Loss ha de ser MÉS BAIX que el preu d'entrada."
        )

    quantitat = risc_maxim_euros / risc_per_accio
    cost_total = quantitat * preu_entrada

    print("\n=== CÀLCUL DE MIDA DE POSICIÓ (capital simulat) ===")
    print(f"Capital simulat:        {CAPITAL_SIMULAT} $")
    print(f"Risc màxim assumit:     {risc_maxim_euros} $ (2%)")
    print(f"Risc per acció:         {risc_per_accio:.2f} $")
    print(f"Quantitat a comprar:    {quantitat:.4f} accions")
    print(f"Cost total aproximat:   {cost_total:.2f} $ "
          f"({cost_total / CAPITAL_SIMULAT * 100:.1f}% del capital simulat)")

    if cost_total > CAPITAL_SIMULAT:
        print(
            "⚠️  Avís: el cost total supera el teu capital simulat de "
            f"{CAPITAL_SIMULAT} $. Redueix la quantitat o busca un Stop-Loss "
            "més ajustat."
        )

    return round(quantitat, 4)


# ---------------------------------------------------------------------------
# 6. Enviar ordre de compra (fraccionada) + vigilància manual del SL/TP
# ---------------------------------------------------------------------------
# IMPORTANT: Alpaca NO permet ordres "bracket" (Stop-Loss + Take-Profit
# automàtics) quan la quantitat és fraccionada (p. ex. 0.1254 accions).
# Amb un capital tan petit (100$ simulats) necessitem fraccions per poder
# comprar res, així que fem servir una ordre de compra SIMPLE, i el propi
# script vigila el preu per vendre quan toqui l'Stop-Loss o el Take-Profit.

import time


def comprar_amb_proteccio(
    symbol: str,
    qty: float,
    stop_loss_price: float,
    take_profit_price: float,
):
    """
    Compra 'qty' accions (fraccionades permeses) amb una ordre de mercat
    SIMPLE. Retorna l'ordre; el Stop-Loss/Take-Profit NO són automàtics
    a Alpaca en aquest cas: cal cridar vigilar_i_vendre() després.
    """
    order_data = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        # Sense order_class=BRACKET: les fraccionades no ho admeten.
    )
    ordre = trading_client.submit_order(order_data)
    print(f"\n✅ Ordre de compra enviada: {qty} de {symbol}")
    print(f"   ID ordre:    {ordre.id}")
    print(f"   Objectiu Stop-Loss:   {stop_loss_price} $ (vigilància manual)")
    print(f"   Objectiu Take-Profit: {take_profit_price} $ (vigilància manual)")
    return ordre


def vigilar_i_vendre(
    symbol: str,
    qty: float,
    stop_loss_price: float,
    take_profit_price: float,
    interval_segons: int = 60,
):
    """
    Comprova el preu cada 'interval_segons' i ven automàticament la posició
    si es toca el Stop-Loss o el Take-Profit. Es queda executant-se en bucle
    (deixa el Terminal obert) fins que ven o l'atures tu (Ctrl+C).
    """
    print(
        f"\n👀 Vigilant {symbol}... venda automàtica a "
        f"{stop_loss_price} $ (SL) o {take_profit_price} $ (TP)."
    )
    try:
        while True:
            preu = preu_actual(symbol)
            if preu <= stop_loss_price:
                print(f"🔴 Stop-Loss tocat ({preu} $). Venent...")
                _vendre(symbol, qty)
                break
            if preu >= take_profit_price:
                print(f"🟢 Take-Profit tocat ({preu} $). Venent...")
                _vendre(symbol, qty)
                break
            time.sleep(interval_segons)
    except KeyboardInterrupt:
        print("\n⏹️  Vigilància aturada manualment (posició encara oberta).")


def _vendre(symbol: str, qty: float):
    order_data = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    ordre = trading_client.submit_order(order_data)
    print(f"✅ Ordre de venda enviada: {qty} de {symbol} (ID: {ordre.id})")
    return ordre


# ---------------------------------------------------------------------------
# 7. Exemple d'ús
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mostrar_saldo()  # Recorda: el saldo REAL (100.000$) no s'utilitza pels càlculs
    mostrar_posicions()

    # Consultar preu actual d'AAPL
    preu = preu_actual("AAPL")

    # Defineix aquí el teu Stop-Loss (p. ex. un suport tècnic recent)
    stop_loss = preu * 0.95  # exemple: -5% respecte al preu actual
    take_profit = preu * 1.10  # exemple: +10% (ràtio risc/benefici ~1:2)

    # Calcula automàticament la quantitat segons el capital simulat de 100$
    quantitat = calcular_quantitat(preu_entrada=preu, preu_stop_loss=stop_loss)

    # Enviar l'ordre real de PAPER TRADING (compra simple, fraccionada):
    comprar_amb_proteccio(
        symbol="AAPL",
        qty=quantitat,
        stop_loss_price=round(stop_loss, 2),
        take_profit_price=round(take_profit, 2),
    )

    # Un cop comprat, vigila el preu i ven sol quan toqui SL o TP.
    # OJO: això deixa el script corrent en bucle indefinidament (Ctrl+C per
    # aturar-lo). Si prefereixes fer-ho més endavant, comenta aquesta línia.
    vigilar_i_vendre(
        symbol="AAPL",
        qty=quantitat,
        stop_loss_price=round(stop_loss, 2),
        take_profit_price=round(take_profit, 2),
        interval_segons=60,
    )
