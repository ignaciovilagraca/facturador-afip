#!/usr/bin/env python3
"""Prueba de conexión con ARCA: login en WSAA y consultas de solo lectura a WSFEX.

Uso: python3 probar_conexion.py [homo|prod]
"""
import sys

from afip import ENTORNOS, login, puntos_de_venta, punto_de_venta_activo, ultimo_comprobante, wsfex


def main():
    env = sys.argv[1] if len(sys.argv) > 1 else "homo"
    if env not in ENTORNOS:
        raise SystemExit("Uso: python3 probar_conexion.py [homo|prod]")
    print(f"Entorno: {env}")

    r = wsfex(env, "FEXDummy", None)
    estado = {c.tag.split("}")[1]: c.text for c in r}
    print(f"FEXDummy: {estado}")

    auth = login(env)

    ptos, err = puntos_de_venta(env, auth)
    print(f"Puntos de venta: {ptos or 'ninguno'} {err}")

    pto = punto_de_venta_activo(env, auth)
    if pto is None:
        raise SystemExit("No hay punto de venta activo de 'Comprobantes de Exportación - Web Services'")
    print(f"Última Factura E en pto {pto}: {ultimo_comprobante(env, auth, pto)}")


if __name__ == "__main__":
    main()
