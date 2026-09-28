"""
auto_trader.py
--------------
Decideix AUTOMÀTICAMENT quan comprar, basant-se en l'indicador tècnic RSI
(Relative Strength Index), per a una cistella dinàmica d'actius.

Configuració d'alta activitat:
  - Watchlist: NVDA, TSLA, AAPL, AMD
  - RSI Període: 7
  - RSI Sobrevenut: < 40
"""

import os
import requests
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from datetime import datetime, timedelta

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed


def enviar_telegram(missatge):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": missatge,
            "parse_mode": "Markdown"
        }
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Error enviant a Telegram: {e}")


load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    raise RuntimeError("Falten ALPACA_API_KEY / ALPACA_SECRET_KEY (variables d'entorn).")

# ⚠️ LIVE TRADING: paper=False -> diners REALS. (Canviar a True si és Paper)
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=False)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# ---------------------------------------------------------------------------
# Configuració ajustada per a MÉS ACTIVITAT de trading
# ---------------------------------------------------------------------------
CAPITAL_SIMULAT = 100.0
WATCHLIST = ["NVDA", "TSLA", "AAPL", "AMD"]
ASSIGNACIO_PER_ACTIU = CAPITAL_SIMULAT / len(WATCHLIST)  # 25$ cadascuna

RSI_PERIODE = 7
RSI_SOBREVENUT = 40


def calcular_rsi(preus_tancament, periode=RSI_PERIODE):
    """Calcula l'RSI (mètode de Wilder) a partir d'una llista de preus de tancament."""
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


def obtenir_rsi_i_preu_actual(symbol: str):
    """Obté el preu d'Alpaca mitjançant Snapshot i calcula el RSI de 7 períodes."""
    preu_actual = None

    try:
        req_snap = StockSnapshotRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        snap = data_client.get_stock_snapshot(req_snap)
        if symbol in snap:
            dades = snap[symbol]
            if dades.latest_trade and dades.latest_trade.price > 0:
                preu_actual = float(dades.latest_trade.price)
            elif dades.latest_quote and (dades.latest_quote.ask_price > 0 or dades.latest_quote.bid_price > 0):
                preu_actual = float(dades.latest_quote.ask_price or dades.latest_quote.bid_price)
            elif dades.daily_bar and dades.daily_bar.close > 0:
                preu_actual = float(dades.daily_bar.close)
    except Exception as e:
        print(f"⚠️ {symbol}: error obtenint Snapshot ({e})")

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Hour,
        start=datetime.now() - timedelta(days=20),
        feed=DataFeed.IEX,
    )
    resposta = data_client.get_stock_bars(request)

    if symbol not in resposta.data or not resposta.data[symbol]:
        return None, preu_actual

    tancaments = [float(b.close) for b in resposta.data[symbol]]
    
    if preu_actual is None or preu_actual == 0:
        if tancaments:
            preu_actual = tancaments[-1]

    rsi = calcular_rsi(tancaments, periode=RSI_PERIODE)
    return rsi, preu_actual


def obtenir_informacio_compte():
    """Retorna l'equity (total) i l'efectiu disponible del compte d'Alpaca."""
    try:
        account = trading_client.get_account()
        return float(account.equity), float(account.cash)
    except Exception as e:
        print(f"⚠️ Error obtenint dades del compte: {e}")
        return None, None


def mercat_esta_obert() -> bool:
    """Comprova si la borsa dels EUA està actualment oberta."""
    try:
        clock = trading_client.get_clock()
        return clock.is_open
    except Exception:
        return False


def ja_tinc_posicio(symbol: str) -> bool:
    """Comprova si ja tenim una posició oberta per a aquest actiu."""
    try:
        trading_client.get_open_position(symbol)
        return True
    except Exception:
        return False


def comprar(symbol: str, preu_actual: float, rsi: float) -> bool:
    """Executa la compra si hi ha saldo suficient. Retorna True si s'executa amb èxit, False si falla."""
    equity, cash = obtenir_informacio_compte()

    # Validació de saldo abans d'enviar l'ordre
    if cash is not None and cash < ASSIGNACIO_PER_ACTIU:
        print(f"⚠️ Saldo insuficient per comprar {symbol}: ${cash:.2f} disponible.")
        return False

    quantitat = round(ASSIGNACIO_PER_ACTIU / preu_actual, 4)
    order_data = MarketOrderRequest(
        symbol=symbol,
        qty=quantitat,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )

    try:
        ordre = trading_client.submit_order(order_data)
        print(f"✅ COMPRA realitzada: {quantitat} de {symbol} (~{ASSIGNACIO_PER_ACTIU}$) — ID: {ordre.id}")

        text_compte = f"\n\n💰 **Compte Alpaca:**\n• Total: `${equity:.2f}`\n• Disponible: `${cash:.2f}`" if equity is not None else ""
        msg = (
            f"🟢 **COMPRA REALITZADA**\n"
            f"• **Símbol:** `{symbol}`\n"
            f"• **Quantitat:** `{quantitat}`\n"
            f"• **Preu:** `${preu_actual:.2f}`\n"
            f"• **RSI (7):** `{rsi:.1f}`"
            f"{text_compte}"
        )
        enviar_telegram(msg)
        return True

    except Exception as e:
        print(f"❌ Error en executar la compra de {symbol}: {e}")
        return False


def revisar_oportunitats():
    informacio_actius = []
    compra_efectuada = False

    for symbol in WATCHLIST:
        posicio_oberta = ja_tinc_posicio(symbol)
        rsi, preu_actual = obtenir_rsi_i_preu_actual(symbol)

        if rsi is None or preu_actual is None:
            print(f"{symbol}: dades insuficients.")
            informacio_actius.append(f"• **{symbol}**: Sense dades")
            continue

        print(f"{symbol}: RSI(7)={rsi:.1f} | Preu={preu_actual:.2f}$")

        if posicio_oberta:
            print(f"{symbol}: ja tenim posició oberta, no es compra.")
            informacio_actius.append(f"• **{symbol}**: Preu = `${preu_actual:.2f}` | RSI = `{rsi:.1f}` *(Posició oberta)*")
            continue

        # Avaluar senyal de compra (RSI < 40)
        if rsi < RSI_SOBREVENUT:
            print(f"🟢 {symbol}: RSI sobrevenut ({rsi:.1f} < {RSI_SOBREVENUT}). Executant compra...")
            exit_compra = comprar(symbol, preu_actual, rsi)
            
            if exit_compra:
                compra_efectuada = True
            else:
                # Si ha fallat la compra (ex: falta de saldo), ho anotem a la llista del resum
                informacio_actius.append(f"• **{symbol}**: Preu = `${preu_actual:.2f}` | RSI = `{rsi:.1f}` ⚠️ *(Intenta comprar: Error/Sense saldo)*")
        else:
            print(f"{symbol}: sense senyal de compra (RSI {rsi:.1f}).")
            informacio_actius.append(f"• **{symbol}**: Preu = `${preu_actual:.2f}` | RSI = `{rsi:.1f}`")

    # Si NO s'ha completat cap compra amb èxit, s'envia el resum de TOTS els actius
    if not compra_efectuada:
        llista_text = "\n".join(informacio_actius)
        equity, cash = obtenir_informacio_compte()
        text_compte = f"\n\n💰 **Compte:**\n• Total cartera: `${equity:.2f}`\n• En efectiu: `${cash:.2f}`" if equity is not None else ""
        estat_mercat = "🟢 Mercat OBERT" if mercat_esta_obert() else "🔴 Mercat TANCAT"

        msg_resum = (
            f"ℹ️ **Resum de mercat**\n"
            f"Estat: {estat_mercat}\n\n"
            f"📊 **Variables (RSI < 40 per comprar):**\n"
            f"{llista_text}"
            f"{text_compte}"
        )
        enviar_telegram(msg_resum)


if __name__ == "__main__":
    print("🤖 Revisant senyals de compra (RSI 7) a la cistella dinàmica...")
    revisar_oportunitats()
    print("✅ Revisió finalitzada.")
