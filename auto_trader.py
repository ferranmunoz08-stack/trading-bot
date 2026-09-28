"""
auto_trader.py
--------------
Decideix AUTOMÀTICAMENT quan comprar, basant-se en l'indicador tècnic RSI
(Relative Strength Index), per a una cistella diversificada d'actius.

Lògica:
  - RSI < 30  -> l'actiu està "sobrevenut" -> senyal de COMPRA
  - RSI > 70  -> l'actiu està "sobrecomprat" -> no comprem (i si ja hi ets,
                 el propi monitor_once.py se n'ocuparà si toca el Take-Profit)

L'RSI es calcula amb preus de tancament diaris, però el PREU mostrat i usat
per calcular la quantitat a comprar és l'últim preu negociat (en directe).

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
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
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


def obtenir_rsi_actual(symbol: str):
    """Descarrega les últimes barres diàries i calcula l'RSI actual.
    Retorna (rsi, ultim_tancament_diari)."""
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=90),  # marge ampli de dies
        feed=DataFeed.IEX,  # feed gratuït
    )
    resposta = data_client.get_stock_bars(request)

    if symbol not in resposta.data or not resposta.data[symbol]:
        print(f"⚠️ {symbol}: no s'han rebut dades històriques (resposta buida).")
        return None, None

    tancaments = [float(b.close) for b in resposta.data[symbol]]
    return calcular_rsi(tancaments), tancaments[-1] if tancaments else None


def obtenir_preu_actual(symbol: str, preu_de_reserva: float):
    """Retorna l'últim preu negociat (en directe). Si falla, usa el preu de reserva
    (l'últim tancament diari) perquè el bot no es quedi sense preu."""
    try:
        req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        return float(data_client.get_stock_latest_trade(req)[symbol].price)
    except Exception as e:
        print(f"⚠️ {symbol}: no s'ha pogut obtenir el preu en directe ({e}). "
              f"Faig servir l'últim tancament.")
        return preu_de_reserva


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
    # Notificació de compra realitzada a Telegram:
    msg = (
        f"🟢 **COMPRA REALITZADA**\n"
        f"• **Símbol:** `{symbol}`\n"
        f"• **Quantitat:** `{quantitat}`\n"
        f"• **Preu:** `${preu_actual:.2f}`\n"
        f"• **RSI:** `{rsi:.1f}`"
    )
    enviar_telegram(msg)


def revisar_oportunitats():
    informacio_actius = []
    compra_efectuada = False

    for symbol in WATCHLIST:
        posicio_oberta = ja_tinc_posicio(symbol)
        rsi, ultim_tancament = obtenir_rsi_actual(symbol)

        if rsi is None or ultim_tancament is None:
            print(f"{symbol}: dades insuficients per calcular l'RSI.")
            informacio_actius.append(f"• **{symbol}**: Sense dades")
            continue

        # Preu en directe (l'RSI segueix basant-se en tancaments diaris)
        preu_actual = obtenir_preu_actual(symbol, ultim_tancament)

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

    # Si no s'ha comprat res, enviem el resum de les 4 variables:
    if not compra_efectuada:
        llista_text = "\n".join(informacio_actius)
        msg_resum = (
            f"ℹ️ **Sense operacions de compra**\n"
            f"No s'ha complert cap condició de compra.\n\n"
            f"📊 **Estat de les 4 variables:**\n"
            f"{llista_text}"
        )
        enviar_telegram(msg_resum)


if __name__ == "__main__":
    print("🤖 Revisant senyals de compra (RSI) a la cistella diversificada...")
    revisar_oportunitats()
    print("✅ Revisió de compra acabada.")
