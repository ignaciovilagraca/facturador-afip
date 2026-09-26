#!/usr/bin/env python3
"""Busca códigos de las tablas de WSFEX (consulta homologación, las tablas son las mismas).

Uso:
    python3 parametros.py paises <texto>      # código de país destino (Dst_cmp)
    python3 parametros.py cuit-pais <texto>   # CUIT genérico del país del cliente
    python3 parametros.py monedas <texto>
"""
import sys

from afip import campo, login, wsfex, FE_NS

TABLAS = {
    "paises": ("FEXGetPARAM_DST_pais", "ClsFEXResponse_DST_pais", "DST_Codigo", "DST_Ds"),
    "cuit-pais": ("FEXGetPARAM_DST_CUIT", "ClsFEXResponse_DST_cuit", "DST_CUIT", "DST_Ds"),
    "monedas": ("FEXGetPARAM_MON", "ClsFEXResponse_Mon", "Mon_Id", "Mon_Ds"),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in TABLAS:
        raise SystemExit(__doc__)
    metodo, nodo, cod, ds = TABLAS[sys.argv[1]]
    filtro = " ".join(sys.argv[2:]).upper()
    r = wsfex("homo", metodo, login("homo"))
    for x in r.iter(f"{{{FE_NS}}}{nodo}"):
        desc = campo(x, ds) or ""
        if filtro in desc.upper():
            print(f"{campo(x, cod)}\t{desc}")


if __name__ == "__main__":
    main()
