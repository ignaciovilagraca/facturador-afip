"""Emisión de facturas en dos pasos: `preparar` (arma el pedido y el resumen) y `enviar` (pide el CAE).

Separarlos permite mostrar el resumen y pedir confirmación antes de emitir, tanto en la terminal como
en la web. Soporta la Factura E (WSFEX) y las comunes A, B y C (WSFE), según el campo "tipo" del JSON:

- Factura E: formato de ejemplo_factura.json (sin "tipo", o "tipo": "E").
- Comunes: "tipo" "A", "B" o "C"; formato de ejemplo_factura_comun.json. WSFE no recibe el detalle de
  ítems, solo los totales: en A y B el precio de cada ítem es el neto y lleva su alícuota de IVA.
- Notas de crédito: una común con "nota_credito_de": {"punto_venta", "numero", "fecha"} de la factura que
  anula o ajusta, de la misma letra. Se informa como comprobante asociado (CbtesAsoc).
"""
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from .arca import (FE_NS, ErrorArca, campo, campo_fe, descripciones_parametros, errores, mensajes_fe,
                  punto_de_venta_activo, punto_de_venta_activo_fe, ultimo_comprobante, ultimo_comprobante_fe,
                  wsfe, wsfex)

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
TIPO_FACTURA_E = 19
TIPOS_COMUNES = {"A": 1, "B": 6, "C": 11}
NOTAS_CREDITO = {"A": 3, "B": 8, "C": 13}
# Alícuotas de IVA: porcentaje -> código de ARCA
ALICUOTAS = {"0": 3, "2.5": 9, "5": 8, "10.5": 4, "21": 5, "27": 6}
DOCUMENTOS = {"CUIT": 80, "CUIL": 86, "DNI": 96, "CF": 99}
CONCEPTOS = {1: "Productos", 2: "Servicios", 3: "Productos y servicios"}


class Rechazada(ErrorArca):
    """ARCA rechazó la factura. `mensajes` trae los errores, observaciones y eventos."""

    def __init__(self, mensajes):
        mensajes = [m for m in mensajes if m]
        # ARCA repite en cada respuesta avisos de mantenimiento (evento 103): no hacen al rechazo
        self.mensajes = [m for m in mensajes if not m.startswith("103:")] or mensajes
        super().__init__("RECHAZADA. " + " | ".join(self.mensajes))


@dataclass
class Preparada:
    tipo: str            # "E", "A", "B" o "C"
    env: str
    servicio: str        # "wsfex" o "wsfe"
    cbte_tipo: int       # código de ARCA: 19 (E), 1/6/11 (A/B/C), 3/8/13 (notas de crédito)
    punto_venta: int
    numero: int
    cuerpo: str          # XML del pedido de autorización
    resumen: dict        # lo que se le muestra al usuario antes de emitir
    salida: dict         # datos que se guardan con la factura aprobada
    factura: dict = field(default_factory=dict)


def hoy():
    return datetime.now(TZ).date()


def tipo_de(f):
    return str(f.get("tipo", "E")).upper()


def servicio_de(f):
    return "wsfe" if tipo_de(f) in TIPOS_COMUNES else "wsfex"


def cbte_tipo_de(f):
    tipo = tipo_de(f)
    if tipo not in TIPOS_COMUNES:
        return TIPO_FACTURA_E
    return (NOTAS_CREDITO if f.get("nota_credito_de") else TIPOS_COMUNES)[tipo]


def nombre_comprobante(f):
    tipo = tipo_de(f)
    return f"{'Nota de crédito' if f.get('nota_credito_de') and tipo in TIPOS_COMUNES else 'Factura'} {tipo}"


def nombre_registro(salida):
    """Nombre del JSON de una factura aprobada, el mismo que usa facturador-afip: E-00004-00000001.json"""
    prefijo = "NC-" if salida.get("nota_credito") else ""
    return f"{prefijo}{salida['tipo']}-{salida['punto_venta']:05d}-{salida['numero']:08d}.json"


def _dos(x):
    return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _fch(d):
    return d.strftime("%Y%m%d")


def _fecha(valor, por_defecto):
    return date.fromisoformat(valor) if valor else por_defecto


def _tag(nombre, valor):
    return f"<{nombre}>{escape(str(valor))}</{nombre}>"


def _tag_opcional(nombre, valor):
    return _tag(nombre, valor) if valor not in (None, "") else ""


def preparar(f, env, auth, pto=None):
    """Arma el pedido sin enviarlo. `auth` tiene que ser del servicio de `servicio_de(f)`."""
    f = dict(f)
    f["tipo"] = tipo_de(f)
    if not f.get("items"):
        raise ErrorArca("La factura no tiene ítems")
    for i, it in enumerate(f["items"], 1):
        if not isinstance(it, dict) or not it.get("descripcion") or it.get("precio") in (None, ""):
            raise ErrorArca(f"El ítem {i} tiene que tener descripcion y precio, por ejemplo "
                            '{"descripcion": "Consultoría", "cantidad": 1, "precio": 1000}')
        try:
            Decimal(str(it["precio"])), Decimal(str(it.get("cantidad", 1)))
        except ArithmeticError as e:
            raise ErrorArca(f"El precio o la cantidad del ítem {i} no es un número: {it}") from e
    if f["tipo"] not in TIPOS_COMUNES and f["tipo"] != "E":
        raise ErrorArca(f"Tipo de comprobante no válido: {f['tipo']} (válidos: A, B, C, E)")
    if f["tipo"] == "E" and f.get("nota_credito_de"):
        raise ErrorArca("Las notas de crédito de Factura E todavía no están soportadas")
    return _preparar_comun(f, env, auth, pto) if f["tipo"] in TIPOS_COMUNES else _preparar_e(f, env, auth, pto)


def enviar(prep: Preparada, auth, emisor: dict):
    """Pide el CAE. Devuelve el registro de la factura aprobada o lanza `Rechazada`."""
    enviar_a = _enviar_comun if prep.servicio == "wsfe" else _enviar_e
    salida = enviar_a(prep, auth)
    salida["emisor"] = emisor
    salida["factura"] = prep.factura
    if prep.servicio == "wsfex":
        try:
            salida["descripciones"] = descripciones_parametros(prep.env, auth, prep.factura)
        except ErrorArca:
            pass  # solo se usan para el PDF, que las puede buscar después
    return salida


# --- Factura E (WSFEX) ---

def _cotizacion_e(env, auth, moneda, fecha_cbte):
    """ARCA valida contra la cotización del día anterior a la fecha del comprobante."""
    if moneda == "PES":
        return Decimal("1")
    dia = min(fecha_cbte - timedelta(days=1), hoy())
    # Si ese día no tiene cotización publicada (hoy temprano, feriados), usa la anterior
    for _ in range(10):
        r = wsfex(env, "FEXGetPARAM_Ctz", auth,
                  body=f"<Mon_id>{escape(moneda)}</Mon_id><FchCotiz>{dia.isoformat()}</FchCotiz>")
        ctz = campo(r, "Mon_ctz")
        if ctz:
            return Decimal(ctz)
        dia -= timedelta(days=1)
    raise ErrorArca(f"No se pudo obtener la cotización de {moneda}: {errores(r)}")


def _items_e(items):
    xml, total = [], Decimal("0")
    for it in items:
        cantidad = Decimal(str(it.get("cantidad", 1)))
        precio = Decimal(str(it["precio"]))
        bonif = _dos(it.get("bonificacion", 0))
        subtotal = _dos(cantidad * precio) - bonif
        total += subtotal
        xml.append(
            "<Item>"
            + _tag_opcional("Pro_codigo", it.get("codigo", ""))
            + _tag("Pro_ds", it["descripcion"])
            + _tag("Pro_qty", cantidad)
            + _tag("Pro_umed", it.get("unidad", 7))
            + _tag("Pro_precio_uni", precio)
            + _tag("Pro_bonificacion", bonif)
            + _tag("Pro_total_item", subtotal)
            + "</Item>"
        )
    return "".join(xml), total


def _preparar_e(f, env, auth, pto):
    cli = f.get("cliente") or {}
    if not cli.get("nombre"):
        raise ErrorArca("Falta el nombre del cliente")
    pto = str(pto or f.get("punto_venta") or punto_de_venta_activo(env, auth) or "")
    if not pto:
        raise ErrorArca("No hay punto de venta activo de 'Comprobantes de Exportación - Web Services'")

    moneda = f.get("moneda", "DOL")
    fecha = _fecha(f.get("fecha"), hoy())
    ctz = Decimal(str(f["cotizacion"])) if f.get("cotizacion") else _cotizacion_e(env, auth, moneda, fecha)
    items_xml, total = _items_e(f["items"])
    tipo_expo = int(f.get("tipo_expo", 2))
    fecha_pago = _fecha(f.get("fecha_pago"), hoy() + timedelta(days=30)) if tipo_expo != 1 else None
    nro = ultimo_comprobante(env, auth, pto, TIPO_FACTURA_E) + 1
    id_req = int(campo(wsfex(env, "FEXGetLast_ID", auth), "Id") or 0) + 1

    cuerpo = (
        "<Cmp>"
        + _tag("Id", id_req)
        + _tag("Fecha_cbte", _fch(fecha))
        + _tag("Cbte_Tipo", TIPO_FACTURA_E)
        + _tag("Punto_vta", pto)
        + _tag("Cbte_nro", nro)
        + _tag("Tipo_expo", tipo_expo)
        # Para servicios (2) y otros (4) el permiso de embarque va vacío
        + ("<Permiso_existente></Permiso_existente>" if tipo_expo != 1
           else _tag("Permiso_existente", f.get("permiso_existente", "N")))
        + _tag_opcional("Dst_cmp", f.get("pais_destino"))
        + _tag("Cliente", cli["nombre"])
        + _tag_opcional("Cuit_pais_cliente", cli.get("cuit_pais"))
        + _tag_opcional("Domicilio_cliente", cli.get("domicilio"))
        + _tag_opcional("Id_impositivo", cli.get("id_impositivo"))
        + _tag("Moneda_Id", moneda)
        + _tag("Moneda_ctz", ctz)
        + _tag_opcional("Obs_comerciales", f.get("obs_comerciales"))
        + _tag("Imp_total", total)
        + _tag_opcional("Obs", f.get("obs"))
        + _tag_opcional("Forma_pago", f.get("forma_pago"))
        + ((_tag_opcional("Incoterms", f.get("incoterms")) + _tag_opcional("Incoterms_Ds", f.get("incoterms_ds")))
           if tipo_expo == 1 else "")
        + _tag("Idioma_cbte", f.get("idioma", 1))
        + (_tag("Fecha_pago", _fch(fecha_pago)) if fecha_pago else "")
        + f"<Items>{items_xml}</Items>"
        + "</Cmp>"
    )
    resumen = {
        "titulo": f"Factura E {int(pto):05d}-{nro:08d}",
        "fecha": fecha.isoformat(),
        "receptor": f"{cli['nombre']} ({cli.get('id_impositivo') or cli.get('cuit_pais')})",
        "moneda": moneda,
        "cotizacion": str(ctz),
        "total": str(total),
        "fecha_pago": fecha_pago.isoformat() if fecha_pago else None,
        "forma_pago": f.get("forma_pago"),
        "items": [(it["descripcion"], it.get("cantidad", 1), it["precio"], None) for it in f["items"]],
    }
    salida = {
        "entorno": env,
        "tipo": "E",
        "punto_venta": int(pto),
        "moneda": moneda,
        "cotizacion": str(ctz),
        "total": str(total),
        "fecha_pago": _fch(fecha_pago) if fecha_pago else None,
    }
    return Preparada("E", env, "wsfex", TIPO_FACTURA_E, int(pto), nro, cuerpo, resumen, salida, f)


def _enviar_e(prep, auth):
    r = wsfex(prep.env, "FEXAuthorize", auth, body=prep.cuerpo)
    res = r.find(f"{{{FE_NS}}}FEXResultAuth")
    resultado = campo(res, "Resultado") if res is not None else None
    eventos = [f"{campo(e, 'EventCode')}: {campo(e, 'EventMsg')}" for e in r.iter(f"{{{FE_NS}}}FEXEvents")
               if campo(e, "EventCode") not in (None, "0")]
    obs = campo(res, "Motivos_Obs") if res is not None else None
    if resultado != "A":
        raise Rechazada([errores(r), obs] + eventos)
    return {
        **prep.salida,
        "numero": int(campo(res, "Cbte_nro")),
        "fecha": campo(res, "Fch_cbte"),
        "cae": campo(res, "Cae"),
        "vencimiento_cae": campo(res, "Fch_venc_Cae"),
        "observaciones": [obs] if obs else [],
        "eventos": eventos,
    }


# --- Facturas comunes A, B y C (WSFE) ---

def calcular_comun(f):
    """Devuelve (neto, iva_total, total, alicuotas) a partir de los ítems."""
    neto, por_alicuota = Decimal("0"), {}
    for it in f["items"]:
        subtotal = _dos(Decimal(str(it.get("cantidad", 1))) * Decimal(str(it["precio"]))) - _dos(it.get("bonificacion", 0))
        neto += subtotal
        if f["tipo"] in ("A", "B"):
            pct = format(Decimal(str(it.get("iva", 21))).normalize(), "f")  # 21.0 -> "21", 10.50 -> "10.5"
            if pct not in ALICUOTAS:
                raise ErrorArca(f"Alícuota de IVA no válida: {it.get('iva')} (válidas: {', '.join(ALICUOTAS)})")
            por_alicuota[pct] = por_alicuota.get(pct, Decimal("0")) + subtotal
    alicuotas = [(ALICUOTAS[pct], base, _dos(base * Decimal(pct) / 100)) for pct, base in por_alicuota.items()]
    iva = sum((a[2] for a in alicuotas), Decimal("0"))
    return neto, iva, neto + iva, alicuotas


def _cotizacion_fe(env, auth, moneda, fecha):
    if moneda == "PES":
        return Decimal("1")
    # Igual que en la Factura E: la del día hábil anterior, o la última publicada
    dia = min(fecha - timedelta(days=1), hoy())
    for _ in range(10):
        r = wsfe(env, "FEParamGetCotizacion", auth, f"<MonId>{escape(moneda)}</MonId><FchCotiz>{_fch(dia)}</FchCotiz>")
        ctz = campo_fe(r, "MonCotiz")
        if ctz:
            return Decimal(ctz)
        dia -= timedelta(days=1)
    raise ErrorArca(f"No se pudo obtener la cotización de {moneda}: {mensajes_fe(r, 'Errors', 'Err')}")


def _preparar_comun(f, env, auth, pto):
    tipo = f["tipo"]
    asociado = f.get("nota_credito_de")
    cbte_tipo = cbte_tipo_de(f)
    rec = f.get("receptor") or {}
    doc_tipo = DOCUMENTOS.get(str(rec.get("doc_tipo", "CF")).upper(), rec.get("doc_tipo"))
    doc_nro = str(rec.get("doc_nro") or "0").replace("-", "").strip()
    if doc_tipo == 99:
        doc_nro = "0"
    if tipo == "A" and doc_tipo != 80:
        raise ErrorArca("La Factura A exige el CUIT del receptor")
    # Condición frente al IVA del receptor (RG 5616): 1 RI, 4 exento, 5 consumidor final, 6 monotributo...
    cond_iva = int(rec.get("condicion_iva") or (1 if tipo == "A" else 5))
    concepto = int(f.get("concepto", 2))
    fecha = _fecha(f.get("fecha"), hoy())
    moneda = f.get("moneda", "PES")
    neto, iva, total, alicuotas = calcular_comun(f)

    pto = str(pto or f.get("punto_venta") or punto_de_venta_activo_fe(env, auth) or "")
    if not pto:
        raise ErrorArca("No hay punto de venta activo de web services para facturas comunes")
    nro = ultimo_comprobante_fe(env, auth, pto, cbte_tipo) + 1
    ctz = Decimal(str(f["cotizacion"])) if f.get("cotizacion") else _cotizacion_fe(env, auth, moneda, fecha)

    servicios, periodo = "", None
    if concepto in (2, 3):
        desde = _fecha(f.get("servicio_desde"), fecha.replace(day=1))
        hasta = _fecha(f.get("servicio_hasta"), fecha.replace(day=monthrange(fecha.year, fecha.month)[1]))
        vto = _fecha(f.get("fecha_vto_pago"), fecha)
        servicios = _tag("FchServDesde", _fch(desde)) + _tag("FchServHasta", _fch(hasta)) + _tag("FchVtoPago", _fch(vto))
        periodo = {"desde": _fch(desde), "hasta": _fch(hasta), "vto_pago": _fch(vto)}

    asoc_xml, asoc = "", None
    if asociado:
        try:
            asoc = {"tipo": tipo, "punto_venta": int(asociado["punto_venta"]), "numero": int(asociado["numero"]),
                    "fecha": _fch(date.fromisoformat(asociado["fecha"]))}
        except (KeyError, ValueError) as e:
            raise ErrorArca("nota_credito_de necesita punto_venta, numero y fecha (AAAA-MM-DD) de la factura") from e
        asoc_xml = ("<CbtesAsoc><CbteAsoc>" + _tag("Tipo", TIPOS_COMUNES[tipo]) + _tag("PtoVta", asoc["punto_venta"])
                    + _tag("Nro", asoc["numero"]) + _tag("Cuit", auth.cuit) + _tag("CbteFch", asoc["fecha"])
                    + "</CbteAsoc></CbtesAsoc>")

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
        + asoc_xml
        + iva_xml
        + "</FECAEDetRequest>"
    )
    cuerpo = ("<FeCAEReq><FeCabReq>" + _tag("CantReg", 1) + _tag("PtoVta", pto) + _tag("CbteTipo", cbte_tipo)
              + f"</FeCabReq><FeDetReq>{det}</FeDetReq></FeCAEReq>")
    resumen = {
        "titulo": f"{nombre_comprobante(f)} {int(pto):05d}-{nro:08d}",
        "asociado": (f"Factura {tipo} {asoc['punto_venta']:05d}-{asoc['numero']:08d} del {asoc['fecha']}"
                     if asoc else None),
        "fecha": fecha.isoformat(),
        "receptor": f"{rec.get('nombre') or 'Consumidor final'} ({'CF' if doc_tipo == 99 else doc_nro})",
        "concepto": CONCEPTOS.get(concepto),
        "moneda": moneda,
        "cotizacion": str(ctz),
        "neto": str(neto) if tipo in ("A", "B") else None,
        "iva": str(iva) if tipo in ("A", "B") else None,
        "total": str(total),
        "periodo": periodo,
        "items": [(it["descripcion"], it.get("cantidad", 1), it["precio"],
                   it.get("iva", 21) if tipo in ("A", "B") else None) for it in f["items"]],
    }
    salida = {
        "entorno": env,
        "tipo": tipo,
        "nota_credito": bool(asociado),
        "asociado": asoc,
        "punto_venta": int(pto),
        "numero": nro,
        "fecha": _fch(fecha),
        "moneda": moneda,
        "cotizacion": str(ctz),
        "neto": str(neto),
        "iva": str(iva),
        "total": str(total),
        "alicuotas": [{"id": i, "base": str(b), "importe": str(m)} for i, b, m in alicuotas],
        "concepto": concepto,
        "periodo": periodo,
        "receptor": {"doc_tipo": doc_tipo, "doc_nro": doc_nro, "condicion_iva": cond_iva,
                     "nombre": rec.get("nombre", ""), "domicilio": rec.get("domicilio", "")},
        "condicion_venta": f.get("condicion_venta", "Contado"),
    }
    return Preparada(tipo, env, "wsfe", cbte_tipo, int(pto), nro, cuerpo, resumen, salida, f)


def _enviar_comun(prep, auth):
    r = wsfe(prep.env, "FECAESolicitar", auth, prep.cuerpo)
    observaciones = mensajes_fe(r, "Observaciones", "Obs")
    eventos = mensajes_fe(r, "Events", "Evt")
    cae = campo_fe(r, "CAE")
    if campo_fe(r, "Resultado") != "A" or not cae:
        raise Rechazada(mensajes_fe(r, "Errors", "Err") + observaciones + eventos)
    return {
        **prep.salida,
        "cae": cae,
        "vencimiento_cae": campo_fe(r, "CAEFchVto"),
        # ARCA a veces aprueba y a la vez pide anular (por ejemplo, CUIT receptora inexistente)
        "observaciones": observaciones,
        "eventos": eventos,
    }
