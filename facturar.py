#!/usr/bin/env python3
"""Emite una factura a partir de un archivo JSON.

- Factura E (exportación, WSFEX): el formato de ejemplo_factura.json.
- Facturas comunes A, B o C (WSFE): el JSON lleva "tipo": "A", "B" o "C"; ver ejemplo_factura_comun.json.
  Con "nota_credito_de" es una nota de crédito de esa letra; ver ejemplo_nota_credito.json.

Uso:
    python3 facturar.py factura.json          # homologación
    python3 facturar.py factura.json --prod   # producción, pide confirmación

Los certificados salen de certs/, el CUIT y los datos del emisor de .env, y cada factura aprobada queda en
facturas/<entorno>/ con su PDF. Toda la lógica está en la biblioteca facturador_afip (src/).
"""
import argparse
import json
import logging
from pathlib import Path

from facturador_afip import emision
from facturador_afip.arca import ErrorArca
from facturador_afip.datos import Datos

RAIZ = Path(__file__).parent


def mostrar(prep):
    r = prep.resumen
    print(f"\nEntorno:       {prep.env}")
    print(f"Comprobante:   {r['titulo']} del {r['fecha']}" + (f" ({r['concepto']})" if r.get("concepto") else ""))
    if r.get("asociado"):
        print(f"Asociada a:    {r['asociado']}")
    print(f"{'Cliente:' if prep.tipo == 'E' else 'Receptor:'}{'':6}{r['receptor']}")
    if r.get("periodo"):
        p = r["periodo"]
        print(f"Período:       {p['desde']} a {p['hasta']}, vence {p['vto_pago']}")
    if r.get("neto"):
        print(f"Neto:          {r['moneda']} {r['neto']}   IVA: {r['iva']}")
    print(f"Total:         {r['moneda']} {r['total']}" + (f" (cotización {r['cotizacion']})" if r["moneda"] != "PES" else ""))
    for descripcion, cantidad, precio, iva in r["items"]:
        print(f"  - {descripcion}: {cantidad} x {precio}" + (f" + IVA {iva}%" if iva is not None else ""))
    if r.get("fecha_pago"):
        print(f"Fecha de pago: {r['fecha_pago']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archivo", type=Path)
    ap.add_argument("--prod", action="store_true", help="emitir en producción")
    ap.add_argument("--pto", type=int, help="punto de venta; pisa el del JSON (útil en homologación)")
    args = ap.parse_args()
    env = "prod" if args.prod else "homo"
    f = json.loads(args.archivo.read_text())

    datos = Datos(RAIZ)
    auth = datos.login(env, emision.servicio_de(f))
    prep = emision.preparar(f, env, auth, args.pto)
    mostrar(prep)

    if env == "prod" and input("\nEmitir en PRODUCCIÓN? Escribí 'si' para confirmar: ").strip().lower() != "si":
        raise SystemExit("Cancelado, no se emitió nada.")

    try:
        salida = emision.enviar(prep, auth, datos.emisor())
    except emision.Rechazada as e:
        raise SystemExit(f"\n{e}") from e
    registro, pdf = datos.guardar_comprobante(salida, emision.nombre_registro(salida))

    print(f"\nAPROBADA. CAE {salida['cae']}, vence {salida['vencimiento_cae']}")
    if salida.get("observaciones"):
        # ARCA a veces aprueba y a la vez pide anular (por ejemplo, CUIT receptora inexistente)
        print("\n" + "!" * 70)
        print("ATENCIÓN: ARCA aprobó la factura CON OBSERVACIONES. Revisalas: algunas")
        print("indican que hay que anularla con una nota de crédito.")
        for o in salida["observaciones"]:
            print(f"  - {o}")
        print("!" * 70 + "\n")
    for ev in salida.get("eventos") or []:
        print(f"Evento: {ev}")
    print(f"Guardada en {registro.relative_to(RAIZ)}")
    if pdf:
        print(f"PDF: {pdf.relative_to(RAIZ)}")
    else:
        print(f"No se pudo generar el PDF. Generalo con: .venv/bin/python pdf.py {registro.relative_to(RAIZ)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        main()
    except ErrorArca as e:
        raise SystemExit(str(e)) from e
