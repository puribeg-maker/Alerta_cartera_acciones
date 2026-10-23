"""Revisa precios de acciones y envía alertas por mail según límites de venta y stop loss.

Dos modos (variable MODO):
  - alertas: avisa solo cuando una acción entra a una zona nueva (venta, stop o cerca).
  - resumen: manda siempre un mail con el estado de todas las acciones (cierre del día).

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
ICONO = {"OK": "✅", "CERCA_VENTA": "🟡", "CERCA_STOP": "🟠", "VENTA": "🟢", "STOP": "🔴"}
TEXTO = {
    "OK": "OK",
    "CERCA_VENTA": "Cerca de venta",
    "CERCA_STOP": "Cerca del stop",
    "VENTA": "Sobre precio de venta",
    "STOP": "Bajo el stop loss",
}
MENSAJES = {
    "VENTA": "🟢 SUPERÓ precio de venta",
    "CERCA_VENTA": "🟡 Cerca del precio de venta",
    "STOP": "🔴 PERFORÓ el stop loss",
    "CERCA_STOP": "🟠 Cerca del stop loss",
}


def fmt(n: float, dec: int = 2) -> str:
    """Formato chileno: 1.234,56"""
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(n: float) -> str:
    return f"{'+' if n > 0 else ''}{fmt(n, 1)}%"


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
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1d", interval="5m")
        if hist.empty:  # fuera de horario o sin datos intradía: último cierre
            hist = t.history(period="5d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def enviar_mail(asunto: str, texto: str, html: str | None = None) -> None:
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = os.environ["EMAIL_USER"]
    msg["To"] = os.environ["EMAIL_TO"]
    msg.set_content(texto)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["EMAIL_USER"], os.environ["EMAIL_APP_PASSWORD"])
        s.send_message(msg)


def modo_alertas(config: list, estado: dict) -> None:
    alertas, errores = [], 0
    for item in config:
        ticker, venta, stop = item["accion"], float(item["venta"]), float(item["stop_loss"])
        p = precio_actual(ticker)
        if p is None:
            errores += 1
            continue
        z = zona(p, venta, stop)
        previa = estado.get(ticker, "OK")
        # Avisa solo al entrar a una zona nueva igual o más grave (evita spam cada 30 min)
        if z != "OK" and z != previa and PRIORIDAD[z] >= PRIORIDAD[previa]:
            alertas.append(
                f"{MENSAJES[z]} — {ticker}: actual {fmt(p)} | venta {fmt(venta)} | stop {fmt(stop)}"
            )
        # Avisa cuando sale de la zona del stop loss hacia arriba y vuelve a OK
        elif z == "OK" and previa in ("CERCA_STOP", "STOP"):
            alertas.append(
                f"🔵 Se alejó del stop loss, vuelve a OK — {ticker}: actual {fmt(p)} | stop {fmt(stop)} "
                f"({fmt_pct((stop / p - 1) * 100)})"
            )
        estado[ticker] = z

    if alertas:
        enviar_mail(f"Alerta acciones ({len(alertas)})", "\n".join(alertas))
    print(f"Modo alertas | revisadas: {len(config)} | alertas enviadas: {len(alertas)} | errores de precio: {errores}")


def modo_resumen(config: list) -> None:
    filas_txt, filas_html, problemas = [], [], 0
    for item in config:
        ticker, venta, stop = item["accion"], float(item["venta"]), float(item["stop_loss"])
        nombre = ticker.replace(".SN", "")
        p = precio_actual(ticker)
        if p is None:
            problemas += 1
            filas_txt.append(f"⚠️ {nombre}: sin precio disponible")
            filas_html.append(
                f"<tr><td><b>{nombre}</b></td><td colspan='5'>⚠️ Sin precio disponible</td></tr>"
            )
            continue

        z = zona(p, venta, stop)
        if z != "OK":
            problemas += 1
        a_venta = (venta / p - 1) * 100  # cuánto tiene que subir para llegar a venta
        a_stop = (stop / p - 1) * 100    # cuánto tiene que caer para llegar al stop

        filas_txt.append(
            f"{ICONO[z]} {nombre}: {fmt(p)} | a venta {fmt_pct(a_venta)} | a stop {fmt_pct(a_stop)} | {TEXTO[z]}"
        )
        filas_html.append(
            "<tr>"
            f"<td><b>{nombre}</b></td>"
            f"<td align='right'>{fmt(p)}</td>"
            f"<td align='right'>{fmt(venta)}<br><small>{fmt_pct(a_venta)}</small></td>"
            f"<td align='right'>{fmt(stop)}<br><small>{fmt_pct(a_stop)}</small></td>"
            f"<td>{ICONO[z]} {TEXTO[z]}</td>"
            "</tr>"
        )

    if problemas == 0:
        titulo = "✅ Todo OK"
    else:
        titulo = f"⚠️ {problemas} acción(es) requieren atención"

    texto = f"Cierre del día — {titulo}\n\n" + "\n".join(filas_txt)
    html = f"""
<div style="font-family:Arial,sans-serif;font-size:14px">
  <h3 style="margin:0 0 12px">Cierre del día — {titulo}</h3>
  <table cellpadding="8" cellspacing="0" border="1" style="border-collapse:collapse;border-color:#ddd">
    <tr style="background:#f3f3f3">
      <th align="left">Acción</th><th>Precio actual</th>
      <th>Precio venta<br><small>falta subir</small></th>
      <th>Stop loss<br><small>distancia</small></th>
      <th align="left">Estado</th>
    </tr>
    {''.join(filas_html)}
  </table>
  <p style="color:#777;font-size:12px">Margen de alerta "cerca": {fmt(MARGEN * 100, 1)}%</p>
</div>"""
    enviar_mail(f"Cierre acciones: {titulo}", texto, html)
    print(f"Modo resumen | revisadas: {len(config)} | requieren atención: {problemas}")


def main() -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    estado = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    config = json.loads(os.environ["ALERTAS_CONFIG"])
    modo = os.environ.get("MODO", "alertas")

    if modo == "resumen":
        modo_resumen(config)
    else:
        modo_alertas(config, estado)

    STATE_FILE.write_text(json.dumps(estado))


if __name__ == "__main__":
    main()
