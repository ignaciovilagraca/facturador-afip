#!/usr/bin/env python3
"""Busca códigos de las tablas de WSFEX (consulta homologación, las tablas son las mismas).

Uso:
    python3 parametros.py paises <texto>      # código de país destino (Dst_cmp)
    python3 parametros.py cuit-pais <texto>   # CUIT genérico del país del cliente
    python3 parametros.py monedas <texto>
"""
import sys
from pathlib import Path

from facturador_afip.arca import ErrorArca, tabla_parametro
from facturador_afip.datos import Datos

TABLAS = {
    "paises": ("FEXGetPARAM_DST_pais", "ClsFEXResponse_DST_pais", "DST_Codigo", "DST_Ds"),
    "cuit-pais": ("FEXGetPARAM_DST_CUIT", "ClsFEXResponse_DST_cuit", "DST_CUIT", "DST_Ds"),
    "monedas": ("FEXGetPARAM_MON", "ClsFEXResponse_Mon", "Mon_Id", "Mon_Ds"),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in TABLAS:
        raise SystemExit(__doc__)
    filtro = " ".join(sys.argv[2:]).upper()
    datos = Datos(Path(__file__).parent)
    for codigo, descripcion in tabla_parametro("homo", datos.login("homo", "wsfex"), *TABLAS[sys.argv[1]]):
        if filtro in descripcion.upper():
            print(f"{codigo}\t{descripcion}")


if __name__ == "__main__":
    try:
        main()
    except ErrorArca as e:
        raise SystemExit(str(e)) from e
