"""
auto_trader.py
--------------
Decideix AUTOMÀTICAMENT quan comprar, basant-se en l'indicador tècnic RSI
(Relative Strength Index), per a una cistella diversificada d'actius.
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    raise RuntimeError("Falten ALPACA_API_KEY / ALPACA_SECRET_KEY (variables d'entorn).")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

CAPITAL_SIMULAT = 100.0
WATCHLIST = ["AAPL", "JNJ", "KO", "XOM"]
ASSIGNACIO_PER_ACTIU = CAPITAL_SIMULAT / len(WATCHLIST)

RSI_PERIODE = 14
RSI_SOBREVENUT = 30


def calcular_rsi(preus_tancament, periode=RSI_PERIODE):
    if len(preus_tancament) < periode + 1:
        return None

    guanys, perdues = [], []
    for i in range(1, len(preus_tancament)):
        diferencia = preus_tancament[i] - preus_tancament[i - 1]
        guanys.append(max(diferencia, 0))
        perdues.append(max(-diferencia, 0))

    mitjana_guany = sum(guanys[-periode:]) / periode
    mitjana_perdua = sum(perdues[-periode:]) / periode

    if mitjana_perdua == 0:
        return 100.0

    rs = mitjana_guany / mitjana_perdua
    return 100 - (100 / (1 + rs))


def obtenir_rsi_actual(symbol: str):
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=90),
        feed=DataFeed.IEX,
    )
    resposta = data_client.get_stock_bars(request)

    if symbol not in resposta.data or not resposta.data[symbol]:
        print(f"⚠️ {symbol}: no s'han rebut dades històriques (resposta buida).")
        return None, None

    tancaments = [float(b.close) for b in resposta.data[symbol]]
    return calcular_rsi(tancaments), tancaments[-1] if tancaments else None


def ja_tinc_posicio(symbol: str) -> bool:
    try:
        trading_client.get_open_position(symbol)
        return True
    except Exception:
        return False


def comprar(symbol: str, preu_actual: float):
    quantitat = round(ASSIGNACIO_PER_ACTIU / preu_actual, 4)
    order_data = MarketOrderRequest(
        symbol=symbol,
        qty=quantitat,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    ordre = trading_client.submit_order(order_data)
    print(
        f"✅ COMPRA per senyal RSI: {quantitat} de {symbol} "
        f"(~{ASSIGNACIO_PER_ACTIU}$) — ID ordre: {ordre.id}"
    )


def revisar_oportunitats():
    for symbol in WATCHLIST:
        if ja_tinc_posicio(symbol):
            print(f"{symbol}: ja tens posició oberta, no es revisa RSI.")
            continue

        rsi, preu_actual = obtenir_rsi_actual(symbol)
        if rsi is None:
            print(f"{symbol}: dades insuficients per calcular l'RSI.")
            continue

        print(f"{symbol}: RSI={rsi:.1f} | preu={preu_actual:.2f}$")

        if rsi < RSI_SOBREVENUT:
            print(f"🟢 {symbol}: RSI sobrevenut ({rsi:.1f}). Comprant...")
            comprar(symbol, preu_actual)
        else:
            print(f"{symbol}: sense senyal de compra (RSI {rsi:.1f}).")


if __name__ == "__main__":
    print("🤖 Revisant senyals de compra (RSI) a la cistella diversificada...")
    revisar_oportunitats()
    print("✅ Revisió de compra acabada.")
