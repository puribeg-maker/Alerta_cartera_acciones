"""Revisa precios de acciones y envía alertas por mail según límites de venta y stop loss.

Toda la información sensible (config, credenciales) viene de GitHub Secrets.
Los logs NO imprimen tickers, precios ni límites, porque en repos públicos los logs son públicos.
"""
import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

import yfinance as yf

STATE_FILE = Path("state/state.json")
MARGEN = float(os.environ.get("MARGEN_PCT", "2")) / 100  # "cerca" = dentro de este % del límite

PRIORIDAD = {"OK": 0, "CERCA_VENTA": 1, "CERCA_STOP": 1, "VENTA": 2, "STOP": 2}
MENSAJES = {
    "VENTA": "🟢 SUPERÓ precio de venta",
    "CERCA_VENTA": "🟡 Cerca del precio de venta",
    "STOP": "🔴 PERFORÓ el stop loss",
    "CERCA_STOP": "🟠 Cerca del stop loss",
}


def zona(precio: float, venta: float, stop: float) -> str:
    if precio >= venta:
        return "VENTA"
    if precio <= stop:
        return "STOP"
    if precio >= venta * (1 - MARGEN):
        return "CERCA_VENTA"
    if precio <= stop * (1 + MARGEN):
        return "CERCA_STOP"
    return "OK"


def precio_actual(ticker: str) -> float | None:
    t = yf.Ticker(ticker)
    hist = t.history(period="1d", interval="5m")
    if hist.empty:  # fuera de horario o sin datos intradía: último cierre
        hist = t.history(period="5d")
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def enviar_mail(lineas: list[str]) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"Alerta acciones ({len(lineas)})"
    msg["From"] = os.environ["EMAIL_USER"]
    msg["To"] = os.environ["EMAIL_TO"]
    msg.set_content("\n".join(lineas))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["EMAIL_USER"], os.environ["EMAIL_APP_PASSWORD"])
        s.send_message(msg)


def main() -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    estado = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    config = json.loads(os.environ["ALERTAS_CONFIG"])

    alertas, errores = [], 0
    for item in config:
        ticker, venta, stop = item["accion"], float(item["venta"]), float(item["stop_loss"])
        try:
            p = precio_actual(ticker)
        except Exception:
            p = None
        if p is None:
            errores += 1
            continue

        z = zona(p, venta, stop)
        previa = estado.get(ticker, "OK")
        # Avisa solo al entrar a una zona nueva igual o más grave (evita spam cada 30 min)
        if z != "OK" and z != previa and PRIORIDAD[z] >= PRIORIDAD[previa]:
            alertas.append(
                f"{MENSAJES[z]} — {ticker}: actual {p:,.2f} | venta {venta:,.2f} | stop {stop:,.2f}"
            )
        estado[ticker] = z

    STATE_FILE.write_text(json.dumps(estado))
    if alertas:
        enviar_mail(alertas)
    # Log público: solo conteos, nada sensible
    print(f"Revisadas: {len(config)} | alertas enviadas: {len(alertas)} | errores de precio: {errores}")


if __name__ == "__main__":
    main()
