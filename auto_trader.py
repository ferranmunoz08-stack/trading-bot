"""
auto_trader.py
--------------
Decideix AUTOMÀTICAMENT quan comprar, basant-se en l'indicador tècnic RSI
(Relative Strength Index), per a una cistella diversificada d'actius.

Lògica:
  - RSI < 30  -> l'actiu està "sobrevenut" -> senyal de COMPRA
  - RSI > 70  -> l'actiu està "sobrecomprat" -> no comprem (i si ja hi ets,
                 el propi monitor_once.py se n'ocuparà si toca el Take-Profit)

Pensat per executar-se periòdicament (via GitHub Actions), abans de
monitor_once.py. No compra si ja tens una posició oberta en aquell actiu
(evita duplicar compres cada 15 minuts).
"""

import os
import requests
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from datetime import datetime, timedelta

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

# ---------------------------------------------------------------------------
# Funció de notificació per Telegram
# ---------------------------------------------------------------------------
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

# ⚠️ LIVE TRADING: paper=False -> diners REALS.
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=False)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# ---------------------------------------------------------------------------
# Configuració de la cistella diversificada
# ---------------------------------------------------------------------------
CAPITAL_SIMULAT = 100.0
WATCHLIST = ["AAPL", "JNJ", "KO", "XOM"]   # Tecnologia, Salut, Consum, Energia
ASSIGNACIO_PER_ACTIU = CAPITAL_SIMULAT / len(WATCHLIST)  # 25$ cadascun

RSI_PERIODE = 14
RSI_SOBREVENUT = 30  # per sota d'aquest valor -> senyal de compra


def calcular_rsi(preus_tancament, periode=RSI_PERIODE):
    """Calcula l'RSI (mètode de Wilder) a partir d'una llista de preus de tancament."""
    if len(preus_tancament) < periode + 1:
        return None  # no hi ha prou dades

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
    """Obté el preu actual (o darrer de tancament si està tancat) i el RSI."""
    preu_actual = None

    # 1. Prova d'obtenir l'últim trade realitzat
    try:
        trade_req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        trade_resp = data_client.get_stock_latest_trade(trade_req)
        if symbol in trade_resp and trade_resp[symbol].price > 0:
            preu_actual = float(trade_resp[symbol].price)
    except Exception as e:
        print(f"⚠️ {symbol}: no s'ha trobat trade recent ({e})")

    # 2. Si no hi ha trade, prova d'obtenir l'últim Quote (Ask/Bid)
    if preu_actual is None:
        try:
            quote_req = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
            quote_resp = data_client.get_stock_latest_quote(quote_req)
            if symbol in quote_resp:
                q = quote_resp[symbol]
                preu_actual = float(q.ask_price or q.bid_price or 0.0)
                if preu_actual == 0:
                    preu_actual = None
        except Exception as e:
            print(f"⚠️ {symbol}: error en quote ({e})")

    # 3. Obtenir barres d'1 hora per calcular l'RSI i com a últim recurs de preu
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Hour,
        start=datetime.now() - timedelta(days=30),  # Marge ampli per cobrir caps de setmana
        feed=DataFeed.IEX,
    )
    resposta = data_client.get_stock_bars(request)

    if symbol not in resposta.data or not resposta.data[symbol]:
        print(f"⚠️ {symbol}: no s'han rebut dades històriques d'Alpaca.")
        return None, preu_actual

    tancaments = [float(b.close) for b in resposta.data[symbol]]
    
    # Si encara no teníem preu, agafem el tancament de l'última barra disponible
    if preu_actual is None or preu_actual == 0:
        if tancaments:
            preu_actual = tancaments[-1]

    rsi = calcular_rsi(tancaments)
    return rsi, preu_actual


def obtenir_informacio_compte():
    """Retorna l'equity (total) i l'efectiu disponible del compte d'Alpaca."""
    try:
        account = trading_client.get_account()
        equity = float(account.equity)
        cash = float(account.cash)
        return equity, cash
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
    try:
        trading_client.get_open_position(symbol)
        return True
    except Exception:
        return False  # no hi ha posició oberta per aquest símbol


def comprar(symbol: str, preu_actual: float, rsi: float):
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
    
    equity, cash = obtenir_informacio_compte()
    text_compte = f"\n\n💰 **Compte Alpaca:**\n• Total: `${equity:.2f}`\n• Disponible: `${cash:.2f}`" if equity is not None else ""

    msg = (
        f"🟢 **COMPRA REALITZADA**\n"
        f"• **Símbol:** `{symbol}`\n"
        f"• **Quantitat:** `{quantitat}`\n"
        f"• **Preu:** `${preu_actual:.2f}`\n"
        f"• **RSI:** `{rsi:.1f}`"
        f"{text_compte}"
    )
    enviar_telegram(msg)


def revisar_oportunitats():
    informacio_actius = []
    compra_efectuada = False

    for symbol in WATCHLIST:
        posicio_oberta = ja_tinc_posicio(symbol)
        rsi, preu_actual = obtenir_rsi_i_preu_actual(symbol)

        if rsi is None or preu_actual is None:
            print(f"{symbol}: dades insuficients per calcular l'RSI o preu.")
            informacio_actius.append(f"• **{symbol}**: Sense dades")
            continue

        print(f"{symbol}: RSI={rsi:.1f} | preu={preu_actual:.2f}$")

        if posicio_oberta:
            print(f"{symbol}: ja tens posició oberta, no es compra.")
            informacio_actius.append(f"• **{symbol}**: Preu = `${preu_actual:.2f}` | RSI = `{rsi:.1f}` *(Posició oberta)*")
            continue

        informacio_actius.append(f"• **{symbol}**: Preu = `${preu_actual:.2f}` | RSI = `{rsi:.1f}`")

        if rsi < RSI_SOBREVENUT:
            print(f"🟢 {symbol}: RSI sobrevenut ({rsi:.1f}). Comprant...")
            comprar(symbol, preu_actual, rsi)
            compra_efectuada = True
        else:
            print(f"{symbol}: sense senyal de compra (RSI {rsi:.1f}).")

    # Si no s'ha comprat res, enviem el resum de les 4 variables i el saldo del compte:
    if not compra_efectuada:
        llista_text = "\n".join(informacio_actius)
        equity, cash = obtenir_informacio_compte()
        text_compte = f"\n\n💰 **Compte Alpaca:**\n• Total cartera: `${equity:.2f}`\n• En efectiu: `${cash:.2f}`" if equity is not None else ""
        estat_mercat = "🟢 Mercat OBERT" if mercat_esta_obert() else "🔴 Mercat TANCAT"

        msg_resum = (
            f"ℹ️ **Sense operacions de compra**\n"
            f"Estat: {estat_mercat}\n\n"
            f"📊 **Estat de les 4 variables:**\n"
            f"{llista_text}"
            f"{text_compte}"
        )
        enviar_telegram(msg_resum)


if __name__ == "__main__":
    print("🤖 Revisant senyals de compra (RSI) a la cistella diversificada...")
    revisar_oportunitats()
    print("✅ Revisió de compra acabada.")
