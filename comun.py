"""Facturas comunes (A, B y C) por WSFE. facturar.py lo usa cuando el JSON tiene "tipo": "A", "B" o "C".

Diferencias con la Factura E:
- WSFE no recibe el detalle de ítems: se informan los totales (neto, IVA por alícuota, total).
  Los ítems quedan en el JSON guardado.
- En A y B el precio de cada ítem es el neto, sin IVA, y lleva su alícuota. En C el precio es final.
- Con concepto 2 (servicios) o 3 (productos y servicios) van el período facturado y el vencimiento del pago.
"""
import json
from calendar import monthrange
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from xml.sax.saxutils import escape

from afip import (CUIT, FEV1_NS, RAIZ, campo_fe, login, mensajes_fe, punto_de_venta_activo_fe,
                  ultimo_comprobante_fe, wsfe)

TIPOS = {"A": 1, "B": 6, "C": 11}
# Alícuotas de IVA: porcentaje -> código de ARCA
ALICUOTAS = {"0": 3, "2.5": 9, "5": 8, "10.5": 4, "21": 5, "27": 6}
DOCUMENTOS = {"CUIT": 80, "CUIL": 86, "DNI": 96, "CF": 99}
CONCEPTOS = {1: "Productos", 2: "Servicios", 3: "Productos y servicios"}


def _dos(x):
    return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _fch(d):
    return d.strftime("%Y%m%d")


def _tag(nombre, valor):
    return f"<{nombre}>{escape(str(valor))}</{nombre}>"


def calcular(f):
    """Devuelve (neto, iva_total, total, alicuotas) a partir de los ítems."""
    tipo = f["tipo"]
    neto, por_alicuota = Decimal("0"), {}
    for it in f["items"]:
        subtotal = _dos(Decimal(str(it.get("cantidad", 1))) * Decimal(str(it["precio"]))) - _dos(it.get("bonificacion", 0))
        neto += subtotal
        if tipo in ("A", "B"):
            pct = format(Decimal(str(it.get("iva", 21))).normalize(), "f")  # 21.0 -> "21", 10.50 -> "10.5"
            if pct not in ALICUOTAS:
                raise SystemExit(f"Alícuota de IVA no válida: {it.get('iva')} (válidas: {', '.join(ALICUOTAS)})")
            por_alicuota[pct] = por_alicuota.get(pct, Decimal("0")) + subtotal
    alicuotas = [(ALICUOTAS[pct], base, _dos(base * Decimal(pct) / 100)) for pct, base in por_alicuota.items()]
    iva = sum((a[2] for a in alicuotas), Decimal("0"))
    return neto, iva, neto + iva, alicuotas


def cotizacion(env, auth, moneda, fecha):
    if moneda == "PES":
        return Decimal("1")
    # Igual que en la Factura E: la del día hábil anterior, o la última publicada
    dia = min(fecha - timedelta(days=1), date.today())
    for _ in range(10):
        r = wsfe(env, "FEParamGetCotizacion", auth,
                 f"<MonId>{escape(moneda)}</MonId><FchCotiz>{_fch(dia)}</FchCotiz>")
        ctz = campo_fe(r, "MonCotiz")
        if ctz:
            return Decimal(ctz)
        dia -= timedelta(days=1)
    raise SystemExit(f"No se pudo obtener la cotización de {moneda}: {mensajes_fe(r, 'Errors', 'Err')}")


def emitir(f, env, pto_forzado, hoy):
    tipo = f["tipo"]
    cbte_tipo = TIPOS[tipo]
    rec = f.get("receptor", {})
    doc_tipo = DOCUMENTOS.get(str(rec.get("doc_tipo", "CF")).upper(), rec.get("doc_tipo"))
    doc_nro = rec.get("doc_nro") or 0
    if doc_tipo == 99:
        doc_nro = 0
    # Condición frente al IVA del receptor (RG 5616): 1 RI, 4 exento, 5 consumidor final, 6 monotributo...
    cond_iva = rec.get("condicion_iva") or (1 if tipo == "A" else 5)
    concepto = int(f.get("concepto", 2))
    fecha = date.fromisoformat(f["fecha"]) if f.get("fecha") else hoy
    moneda = f.get("moneda", "PES")
    neto, iva, total, alicuotas = calcular(f)

    auth = login(env, "wsfe")
    pto = str(pto_forzado or f.get("punto_venta") or punto_de_venta_activo_fe(env, auth) or "")
    if not pto:
        raise SystemExit("No hay punto de venta activo de web services para facturas comunes")
    nro = ultimo_comprobante_fe(env, auth, pto, cbte_tipo) + 1
    ctz = Decimal(str(f["cotizacion"])) if f.get("cotizacion") else cotizacion(env, auth, moneda, fecha)

    servicios = ""
    if concepto in (2, 3):
        desde = date.fromisoformat(f["servicio_desde"]) if f.get("servicio_desde") else fecha.replace(day=1)
        hasta = (date.fromisoformat(f["servicio_hasta"]) if f.get("servicio_hasta")
                 else fecha.replace(day=monthrange(fecha.year, fecha.month)[1]))
        vto = date.fromisoformat(f["fecha_vto_pago"]) if f.get("fecha_vto_pago") else fecha
        servicios = _tag("FchServDesde", _fch(desde)) + _tag("FchServHasta", _fch(hasta)) + _tag("FchVtoPago", _fch(vto))

    print(f"\nEntorno:       {env}")
    print(f"Comprobante:   Factura {tipo} {int(pto):05d}-{nro:08d} del {_fch(fecha)} ({CONCEPTOS[concepto]})")
    print(f"Receptor:      {rec.get('nombre', 'Consumidor final')} (doc {doc_tipo} {doc_nro}, cond. IVA {cond_iva})")
    if tipo in ("A", "B"):
        print(f"Neto:          {moneda} {neto}   IVA: {iva}")
    print(f"Total:         {moneda} {total}" + (f" (cotización {ctz})" if moneda != "PES" else ""))
    for it in f["items"]:
        print(f"  - {it['descripcion']}: {it.get('cantidad', 1)} x {it['precio']}"
              + (f" + IVA {it.get('iva', 21)}%" if tipo in ("A", "B") else ""))

    if env == "prod" and input("\nEmitir en PRODUCCIÓN? Escribí 'si' para confirmar: ").strip().lower() != "si":
        raise SystemExit("Cancelado, no se emitió nada.")

    iva_xml = ""
    if alicuotas:
        iva_xml = "<Iva>" + "".join(
            f"<AlicIva>{_tag('Id', i)}{_tag('BaseImp', b)}{_tag('Importe', m)}</AlicIva>" for i, b, m in alicuotas
        ) + "</Iva>"
    det = (
        "<FECAEDetRequest>"
        + _tag("Concepto", concepto)
        + _tag("DocTipo", doc_tipo)
        + _tag("DocNro", doc_nro)
        + _tag("CbteDesde", nro)
        + _tag("CbteHasta", nro)
        + _tag("CbteFch", _fch(fecha))
        + _tag("ImpTotal", total)
        + _tag("ImpTotConc", "0.00")
        + _tag("ImpNeto", neto)
        + _tag("ImpOpEx", "0.00")
        + _tag("ImpTrib", "0.00")
        + _tag("ImpIVA", iva)
        + servicios
        + _tag("MonId", moneda)
        + _tag("MonCotiz", ctz)
        + (_tag("CanMisMonExt", f.get("cancela_misma_moneda", "N")) if moneda != "PES" else "")
        + _tag("CondicionIVAReceptorId", cond_iva)
        + iva_xml
        + "</FECAEDetRequest>"
    )
    body = (
        "<FeCAEReq><FeCabReq>" + _tag("CantReg", 1) + _tag("PtoVta", pto) + _tag("CbteTipo", cbte_tipo)
        + f"</FeCabReq><FeDetReq>{det}</FeDetReq></FeCAEReq>"
    )
    r = wsfe(env, "FECAESolicitar", auth, body)
    resultado = campo_fe(r, "Resultado")
    errores = mensajes_fe(r, "Errors", "Err")
    observaciones = mensajes_fe(r, "Observaciones", "Obs")
    eventos = mensajes_fe(r, "Events", "Evt")
    cae = campo_fe(r, "CAE")

    if resultado != "A" or not cae:
        raise SystemExit("\nRECHAZADA. " + " | ".join(errores + observaciones + eventos))

    salida = {
        "entorno": env,
        "tipo": tipo,
        "punto_venta": int(pto),
        "numero": nro,
        "fecha": _fch(fecha),
        "cae": cae,
        "vencimiento_cae": campo_fe(r, "CAEFchVto"),
        "moneda": moneda,
        "cotizacion": str(ctz),
        "neto": str(neto),
        "iva": str(iva),
        "total": str(total),
        "alicuotas": [{"id": i, "base": str(b), "importe": str(m)} for i, b, m in alicuotas],
        "receptor": {"doc_tipo": doc_tipo, "doc_nro": str(doc_nro), "condicion_iva": cond_iva},
        "observaciones": observaciones,
        "emisor_cuit": CUIT,
        "factura": f,
    }
    destino = RAIZ / "facturas" / env / f"{tipo}-{int(pto):05d}-{nro:08d}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(salida, indent=2, ensure_ascii=False))

    print(f"\nAPROBADA. CAE {cae}, vence {salida['vencimiento_cae']}")
    if observaciones:
        # ARCA a veces aprueba y a la vez pide anular (por ejemplo, CUIT receptora inexistente)
        print("\n" + "!" * 70)
        print("ATENCIÓN: ARCA aprobó la factura CON OBSERVACIONES. Revisalas: algunas")
        print("indican que hay que anularla con una nota de crédito.")
        for o in observaciones:
            print(f"  - {o}")
        print("!" * 70 + "\n")
    for ev in eventos:
        print(f"Evento: {ev}")
    print(f"Guardada en {destino.relative_to(RAIZ)}")
    print("El PDF todavía no está disponible para facturas comunes.")
