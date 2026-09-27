#!/usr/bin/env python3
"""Genera el PDF de una factura guardada, con el mismo diseño que "Comprobantes en línea" de ARCA.

Uso: .venv/bin/python pdf.py facturas/prod/E-00004-00000001.json

facturar.py lo genera solo después de cada factura aprobada; esto sirve si falló o para un JSON viejo.
El PDF queda al lado del JSON, con el nombre que usa ARCA: <CUIT>_<tipo>_<PPPPP>_<NNNNNNNN>.pdf
"""
import json
import sys
from pathlib import Path

from facturador_afip.arca import ErrorArca
from facturador_afip.datos import Datos


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    origen = Path(sys.argv[1])
    destino = Datos(Path(__file__).parent).generar_pdf(json.loads(origen.read_text()), origen)
    print(f"PDF: {destino}")


if __name__ == "__main__":
    try:
        main()
    except ErrorArca as e:
        raise SystemExit(str(e)) from e
