#!/usr/bin/env python3
"""Prueba de conexión con ARCA: login en WSAA y consultas de solo lectura.

Uso: python3 probar_conexion.py [homo|prod] [wsfex|wsfe]
  wsfex: Factura E (exportación), por defecto
  wsfe:  facturas comunes (A, B, C)
"""
import sys

from afip import (ENTORNOS, login, puntos_de_venta, puntos_de_venta_fe,
                  punto_de_venta_activo, punto_de_venta_activo_fe, ultimo_comprobante, ultimo_comprobante_fe,
                  wsfe, wsfex)


def probar_wsfex(env):
    r = wsfex(env, "FEXDummy", None)
    print(f"FEXDummy: { {c.tag.split('}')[1]: c.text for c in r} }")
    auth = login(env)
    ptos, err = puntos_de_venta(env, auth)
    print(f"Puntos de venta: {ptos or 'ninguno'} {err}")
    pto = punto_de_venta_activo(env, auth)
    if pto is None:
        raise SystemExit("No hay punto de venta activo de 'Comprobantes de Exportación - Web Services'")
    print(f"Última Factura E en pto {pto}: {ultimo_comprobante(env, auth, pto)}")


def probar_wsfe(env):
    r = wsfe(env, "FEDummy", None)
    print(f"FEDummy: { {c.tag.split('}')[1]: c.text for c in r} }")
    auth = login(env, "wsfe")
    ptos, err = puntos_de_venta_fe(env, auth)
    print(f"Puntos de venta: {ptos or 'ninguno'} {' '.join(err)}")
    pto = punto_de_venta_activo_fe(env, auth)
    if pto is None:
        raise SystemExit("No hay punto de venta activo de web services para facturas comunes")
    for letra, tipo in (("A", 1), ("B", 6), ("C", 11)):
        print(f"Última Factura {letra} en pto {pto}: {ultimo_comprobante_fe(env, auth, pto, tipo)}")


def main():
    env = sys.argv[1] if len(sys.argv) > 1 else "homo"
    servicio = sys.argv[2] if len(sys.argv) > 2 else "wsfex"
    if env not in ENTORNOS or servicio not in ("wsfex", "wsfe"):
        raise SystemExit(__doc__)
    print(f"Entorno: {env} / {servicio}")
    probar_wsfex(env) if servicio == "wsfex" else probar_wsfe(env)


if __name__ == "__main__":
    main()
