#!/usr/bin/env python3
"""Emite una Factura E (exportación) por WSFEX a partir de un archivo JSON.

Uso:
    python3 facturar.py factura.json          # homologación
    python3 facturar.py factura.json --prod   # producción, pide confirmación

Ver ejemplo_factura.json para el formato.
"""
import argparse
import json
import os
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from afip import CUIT, RAIZ, campo, errores, login, punto_de_venta_activo, ultimo_comprobante, wsfex, FE_NS

TIPO_FACTURA_E = 19
HOY = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date()


def dos_decimales(x):
    return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def aaaammdd(valor, por_defecto):
    return (date.fromisoformat(valor) if valor else por_defecto).strftime("%Y%m%d")


def tag(nombre, valor):
    return f"<{nombre}>{escape(str(valor))}</{nombre}>" if valor not in (None, "") else ""


def cotizacion(env, auth, moneda, fecha_cbte):
    """ARCA valida contra la cotización del día anterior a la fecha del comprobante."""
    if moneda == "PES":
        return Decimal("1")
    dia = min(fecha_cbte - timedelta(days=1), HOY)
    # Si ese día no tiene cotización publicada (hoy temprano, feriados), usa la anterior
    for _ in range(10):
        r = wsfex(env, "FEXGetPARAM_Ctz", auth,
                  body=f"<Mon_id>{escape(moneda)}</Mon_id><FchCotiz>{dia.isoformat()}</FchCotiz>")
        ctz = campo(r, "Mon_ctz")
        if ctz:
            break
        dia -= timedelta(days=1)
    if not ctz:
        raise SystemExit(f"No se pudo obtener la cotización de {moneda}: {errores(r)}")
    return Decimal(ctz)


def armar_items(items):
    xml, total = [], Decimal("0")
    for it in items:
        cantidad = Decimal(str(it.get("cantidad", 1)))
        precio = Decimal(str(it["precio"]))
        bonif = dos_decimales(it.get("bonificacion", 0))
        subtotal = dos_decimales(cantidad * precio) - bonif
        total += subtotal
        xml.append(
            "<Item>"
            + tag("Pro_codigo", it.get("codigo", ""))
            + tag("Pro_ds", it["descripcion"])
            + tag("Pro_qty", cantidad)
            + tag("Pro_umed", it.get("unidad", 7))
            + tag("Pro_precio_uni", precio)
            + tag("Pro_bonificacion", bonif)
            + tag("Pro_total_item", subtotal)
            + "</Item>"
        )
    return "".join(xml), total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archivo", type=Path)
    ap.add_argument("--prod", action="store_true", help="emitir en producción")
    ap.add_argument("--pto", help="punto de venta; pisa el del JSON (útil en homologación)")
    args = ap.parse_args()
    env = "prod" if args.prod else "homo"
    f = json.loads(args.archivo.read_text())
    cli = f["cliente"]

    auth = login(env)
    pto = str(args.pto or f.get("punto_venta") or punto_de_venta_activo(env, auth) or "")
    if not pto:
        raise SystemExit("No hay punto de venta activo de 'Comprobantes de Exportación - Web Services'")

    moneda = f.get("moneda", "DOL")
    fecha_cbte = date.fromisoformat(f["fecha"]) if f.get("fecha") else HOY
    ctz = Decimal(str(f["cotizacion"])) if f.get("cotizacion") else cotizacion(env, auth, moneda, fecha_cbte)
    items_xml, total = armar_items(f["items"])
    tipo_expo = int(f.get("tipo_expo", 2))
    fecha = aaaammdd(f.get("fecha"), HOY)
    nro = ultimo_comprobante(env, auth, pto, TIPO_FACTURA_E) + 1
    id_req = int(campo(wsfex(env, "FEXGetLast_ID", auth), "Id") or 0) + 1

    print(f"\nEntorno:       {env}")
    print(f"Comprobante:   Factura E {int(pto):05d}-{nro:08d} del {fecha}")
    print(f"Cliente:       {cli['nombre']} ({cli.get('cuit_pais') or cli.get('id_impositivo')})")
    print(f"Total:         {moneda} {total} (cotización {ctz})")
    for it in f["items"]:
        print(f"  - {it['descripcion']}: {it.get('cantidad', 1)} x {it['precio']}")

    if env == "prod" and input("\nEmitir en PRODUCCIÓN? Escribí 'si' para confirmar: ").strip().lower() != "si":
        raise SystemExit("Cancelado, no se emitió nada.")

    cmp = (
        "<Cmp>"
        + tag("Id", id_req)
        + tag("Fecha_cbte", fecha)
        + tag("Cbte_Tipo", TIPO_FACTURA_E)
        + tag("Punto_vta", pto)
        + tag("Cbte_nro", nro)
        + tag("Tipo_expo", tipo_expo)
        # Para servicios (2) y otros (4) el permiso de embarque va vacío
        + ("<Permiso_existente></Permiso_existente>" if tipo_expo != 1 else tag("Permiso_existente", f.get("permiso_existente", "N")))
        + tag("Dst_cmp", f.get("pais_destino"))
        + tag("Cliente", cli["nombre"])
        + tag("Cuit_pais_cliente", cli.get("cuit_pais"))
        + tag("Domicilio_cliente", cli.get("domicilio"))
        + tag("Id_impositivo", cli.get("id_impositivo"))
        + tag("Moneda_Id", moneda)
        + tag("Moneda_ctz", ctz)
        + tag("Obs_comerciales", f.get("obs_comerciales"))
        + tag("Imp_total", total)
        + tag("Obs", f.get("obs"))
        + tag("Forma_pago", f.get("forma_pago"))
        + (tag("Incoterms", f.get("incoterms")) + tag("Incoterms_Ds", f.get("incoterms_ds")) if tipo_expo == 1 else "")
        + tag("Idioma_cbte", f.get("idioma", 1))
        + (tag("Fecha_pago", aaaammdd(f.get("fecha_pago"), HOY + timedelta(days=30))) if tipo_expo != 1 else "")
        + f"<Items>{items_xml}</Items>"
        + "</Cmp>"
    )
    r = wsfex(env, "FEXAuthorize", auth, body=cmp)
    res = r.find(f"{{{FE_NS}}}FEXResultAuth")
    resultado = campo(res, "Resultado") if res is not None else None
    eventos = [f"{campo(e, 'EventCode')}: {campo(e, 'EventMsg')}" for e in r.iter(f"{{{FE_NS}}}FEXEvents")
               if campo(e, "EventCode") not in (None, "0")]

    if resultado != "A":
        obs = campo(res, "Motivos_Obs") if res is not None else None
        raise SystemExit(f"\nRECHAZADA. {errores(r)} {obs or ''} {' | '.join(eventos)}".strip())

    salida = {
        "entorno": env,
        "punto_venta": int(pto),
        "numero": int(campo(res, "Cbte_nro")),
        "fecha": campo(res, "Fch_cbte"),
        "cae": campo(res, "Cae"),
        "vencimiento_cae": campo(res, "Fch_venc_Cae"),
        "moneda": moneda,
        "cotizacion": str(ctz),
        "total": str(total),
        "observaciones": campo(res, "Motivos_Obs"),
        "fecha_pago": aaaammdd(f.get("fecha_pago"), HOY + timedelta(days=30)) if tipo_expo != 1 else None,
        # Datos del emisor al momento de emitir, para poder generar el PDF más adelante
        "emisor": {
            "cuit": CUIT,
            "razon_social": os.environ.get("AFIP_RAZON_SOCIAL"),
            "domicilio_comercial": os.environ.get("AFIP_DOMICILIO_COMERCIAL"),
            "condicion_iva": os.environ.get("AFIP_CONDICION_IVA"),
            "ingresos_brutos": os.environ.get("AFIP_INGRESOS_BRUTOS"),
            "inicio_actividades": os.environ.get("AFIP_INICIO_ACTIVIDADES"),
        },
        "factura": f,
    }
    destino = RAIZ / "facturas" / env / f"E-{int(pto):05d}-{salida['numero']:08d}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(salida, indent=2, ensure_ascii=False))

    print(f"\nAPROBADA. CAE {salida['cae']}, vence {salida['vencimiento_cae']}")
    if salida["observaciones"]:
        print(f"Observaciones: {salida['observaciones']}")
    for ev in eventos:
        print(f"Evento: {ev}")
    print(f"Guardada en {destino.relative_to(RAIZ)}")

    # La factura ya está emitida y guardada: si algo de acá en adelante falla,
    # el PDF se puede regenerar con pdf.py a partir del JSON.
    try:
        from afip import descripciones_parametros
        salida["descripciones"] = descripciones_parametros(env, auth, f)
        destino.write_text(json.dumps(salida, indent=2, ensure_ascii=False))
        import pdf
        archivo = pdf.generar(salida, destino.with_name(pdf.nombre_archivo(salida)))
        print(f"PDF: {archivo.relative_to(RAIZ)}")
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo generar el PDF ({e}). Generalo con: .venv/bin/python pdf.py {destino.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
