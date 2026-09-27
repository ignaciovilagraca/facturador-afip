"""PDF de una factura (E, o A, B, C y notas de crédito) con el mismo diseño que "Comprobantes en línea" de ARCA.

El nombre del archivo es el que usa ARCA: <CUIT>_<tipo>_<PPPPP>_<NNNNNNNN>.pdf
"""
import base64
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import qrcode
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

LOGO = Path(__file__).parent / "assets" / "arca_logo.png"
# ARCA usa Arial; se usa Helvetica, que tiene las mismas métricas y no se embebe, para que el PDF pese poco
ALTO = 842  # A4 en puntos; las coordenadas de abajo se miden desde arriba, como en el PDF de ARCA


MONEDA_ISO = {"DOL": "USD", "060": "EUR", "PES": "ARS"}


def _fecha(aaaammdd):
    return datetime.strptime(aaaammdd, "%Y%m%d").strftime("%d/%m/%Y")


def _coma(valor, decimales):
    return f"{Decimal(str(valor)):.{decimales}f}".replace(".", ",")


def _numero(valor):
    d = Decimal(str(valor))
    return int(d) if d == d.to_integral_value() else float(d)


def url_qr(s):
    """URL del QR según la especificación de ARCA (RG 4892)."""
    f = s["factura"]
    datos = {
        "ver": 1,
        "fecha": datetime.strptime(s["fecha"], "%Y%m%d").strftime("%Y-%m-%d"),
        "cuit": int(s["emisor"]["cuit"]),
        "ptoVta": s["punto_venta"],
        "tipoCmp": 19,
        "nroCmp": s["numero"],
        "importe": _numero(s["total"]),
        "moneda": s["moneda"],
        "ctz": _numero(s["cotizacion"]),
    }
    if f["cliente"].get("cuit_pais"):
        datos["tipoDocRec"] = 80
        datos["nroDocRec"] = int(f["cliente"]["cuit_pais"])
    datos["tipoCodAut"] = "E"
    datos["codAut"] = int(s["cae"])
    p = base64.b64encode(json.dumps(datos, separators=(",", ":")).encode()).decode()
    return f"https://www.arca.gob.ar/fe/qr/?p={p}"


def descripciones(s):
    """Textos de las tablas de ARCA (país, CUIT país, moneda, unidad). Se guardan al emitir; si faltan,
    quien llama los busca antes (en homologación, que tiene las mismas tablas)."""
    if not s.get("descripciones"):
        raise ValueError("Faltan las descripciones de ARCA (país, moneda, unidad) en el JSON de la factura")
    return s["descripciones"]


class _Hoja:
    def __init__(self, destino):
        self.c = canvas.Canvas(destino if hasattr(destino, "write") else str(destino), pagesize=(595, ALTO))

    def texto(self, x, y, txt, fuente="Helvetica", tam=9, alinear="izq"):
        """y es la línea de base medida desde arriba."""
        txt = "" if txt is None else str(txt)
        self.c.setFont(fuente, tam)
        dibujar = {"izq": self.c.drawString, "der": self.c.drawRightString,
                   "centro": self.c.drawCentredString}[alinear]
        dibujar(x, ALTO - y, txt)
        return x + pdfmetrics.stringWidth(txt, fuente, tam)

    def par(self, x, y, etiqueta, valor, tam=9, fuente_valor="Helvetica", tam_valor=None):
        fin = self.texto(x, y, etiqueta, "Helvetica-Bold", tam)
        return self.texto(fin, y, " " + valor, fuente_valor, tam_valor or tam)

    def rect(self, x0, y0, x1, y1, grosor=0.25, relleno=None):
        self.c.setLineWidth(grosor)
        if relleno is not None:
            self.c.setFillGray(relleno)
        self.c.rect(x0, ALTO - y1, x1 - x0, y1 - y0, stroke=1, fill=relleno is not None)
        self.c.setFillGray(0)

    def linea(self, x0, y0, x1, y1, grosor=0.25):
        self.c.setLineWidth(grosor)
        self.c.line(x0, ALTO - y0, x1, ALTO - y1)

    def qr(self, datos, x0, y0, lado):
        """QR vectorial: pesa mucho menos que una imagen y se imprime nítido."""
        qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(datos)
        matriz = qr.get_matrix()
        m = lado / len(matriz)
        self.c.setFillGray(0)
        for fila, celdas in enumerate(matriz):
            col = 0
            while col < len(celdas):
                if celdas[col]:
                    inicio = col
                    while col < len(celdas) and celdas[col]:
                        col += 1
                    self.c.rect(x0 + inicio * m, ALTO - y0 - (fila + 1) * m, (col - inicio) * m, m, stroke=0, fill=1)
                col += 1

    def imagen(self, img, x0, y0, x1, y1):
        self.c.drawImage(img, x0, ALTO - y1, x1 - x0, y1 - y0, mask="auto")

    def marca_de_agua(self, txt):
        self.c.saveState()
        self.c.setFillGray(0.5, 0.25)
        self.c.setFont("Helvetica-Bold", 40)
        self.c.translate(595 / 2, ALTO / 2)
        self.c.rotate(40)
        self.c.drawCentredString(0, 0, txt)
        self.c.restoreState()


# Vista previa de un borrador: se arma con la validación en homologación, pero ese número, CAE y QR no sirven
VISTA_PREVIA = "VISTA PREVIA – SIN VALOR FISCAL"
A_ASIGNAR = "se asigna al emitir"


def _pie(h, s, url, x_der, y_cae, x_logo, y_qr, y_logo, y_leyenda):
    """QR, logo, CAE y leyenda "Comprobante Autorizado"; en la vista previa, la aclaración y la marca de agua."""
    if s.get("vista_previa"):
        h.texto(x_der, y_cae, "CAE N°:", "Helvetica-Bold", 10, "der")
        h.texto(x_der + 5, y_cae, A_ASIGNAR, "Helvetica", 10)
        h.texto(x_logo, y_leyenda, VISTA_PREVIA, "Helvetica-BoldOblique", 9)
        h.marca_de_agua(VISTA_PREVIA)
        return
    h.qr(url, 20, y_qr, 80)
    h.imagen(str(LOGO), x_logo, y_logo, x_logo + 55.07, y_logo + 24)
    h.texto(x_der, y_cae, "CAE N°:", "Helvetica-Bold", 10, "der")
    h.texto(x_der + 5, y_cae, s["cae"], "Helvetica", 10)
    h.texto(x_der, y_cae + 15, "Fecha de Vto. de CAE:", "Helvetica-Bold", 10, "der")
    h.texto(x_der + 5, y_cae + 15, _fecha(s["vencimiento_cae"]), "Helvetica", 10)
    h.texto(x_logo, y_leyenda, "Comprobante Autorizado", "Helvetica-BoldOblique", 9)


def generar(s, destino):
    f, em, cli = s["factura"], _emisor(s), s["factura"]["cliente"]
    desc = descripciones(s)
    iso = MONEDA_ISO.get(s["moneda"], s["moneda"])
    divisa = f"{iso} - {desc['moneda']}"
    h = _Hoja(destino)

    # Encabezado
    h.rect(18, 19, 577, 171)
    h.linea(18, 37.5, 577, 37.5)
    h.texto(297, 33.05, "ORIGINAL", "Helvetica-Bold", 12, "centro")
    h.linea(297.5, 77, 297.5, 170)
    h.rect(274, 39, 321, 77, 0.5)
    h.texto(297.5, 61.9, "E", "Helvetica-Bold", 24, "centro")
    h.texto(297.5, 74.04, "COD. 19", "Helvetica-Bold", 8, "centro")

    h.texto(147, 67.29, em["razon_social"], "Helvetica-Bold", 10, "centro")
    h.par(24, 115.6, "Razón Social:", em["razon_social"])
    etiqueta = "Domicilio Comercial:"
    ancho = pdfmetrics.stringWidth(etiqueta + " ", "Helvetica-Bold", 9)
    primera, *resto = simpleSplit(em["domicilio_comercial"], "Helvetica", 9, 295 - 24 - ancho)
    h.par(24, 139.6, etiqueta, primera)
    resto = simpleSplit(" ".join(resto), "Helvetica", 9, 295 - 24) if resto else []
    for i, linea in enumerate(resto):
        h.texto(24, 150.0 + i * 10.4, linea)
    h.texto(24, 165.4, f"Condición frente al IVA: {em['condicion_iva']}", "Helvetica-Bold", 9)

    h.texto(334, 67.7 - 2.66, "FACTURA DE EXPORTACIÓN", "Helvetica-Bold", 12)
    # En esta columna ARCA pone los valores en posiciones fijas
    h.texto(334, 93.4, "Compr. Nro:", "Helvetica-Bold", 9)
    h.texto(396, 93.29, A_ASIGNAR if s.get("vista_previa") else f"{s['punto_venta']:05d}-{s['numero']:08d}", "Helvetica-Bold", 10)
    h.texto(334, 107.4, "Fecha de Emisión:", "Helvetica-Bold", 9)
    h.texto(424, 107.29, _fecha(s["fecha"]), "Helvetica-Bold", 10)
    h.texto(334, 126.4, "CUIT:", "Helvetica-Bold", 9)
    h.texto(364, 126.4, em["cuit"])
    h.texto(334, 138.4, "Ingresos Brutos:", "Helvetica-Bold", 9)
    h.texto(419, 138.4, em["ingresos_brutos"])
    h.texto(334, 150.4, "Fecha de Inicio de Actividades:", "Helvetica-Bold", 9)
    h.texto(484, 150.4, datetime.strptime(em["inicio_actividades"], "%Y-%m-%d").strftime("%d/%m/%Y"))
    h.texto(334, 165.4, "IVA EXENTO OPERACIÓN DE EXPORTACIÓN", "Helvetica-Bold", 9)

    # Cliente
    h.rect(18, 171, 577, 221, 0.5)
    h.texto(24, 183.9, "Señor(es):", "Helvetica-Bold", 9)
    h.texto(79, 183.9, cli["nombre"])
    h.texto(265, 183.9, "Domicilio:", "Helvetica-Bold", 9)
    h.texto(314, 183.9, cli.get("domicilio", ""))
    if cli.get("cuit_pais"):
        h.par(24, 201.9, "CUIT País:", f"{cli['cuit_pais']} ({desc['cuit_pais']})")
    h.texto(24, 215.9, "ID Impositivo:", "Helvetica-Bold", 9)
    h.texto(89, 215.9, cli.get("id_impositivo", ""))

    # Divisa y destino
    h.rect(18, 223, 577, 272)
    h.par(24, 235.9, "Divisa:", divisa)
    h.par(24, 250.9, "Destino del Comprobante:", desc["pais"])

    # Forma de pago
    h.rect(18, 288, 577, 307)
    h.texto(23, 300.9, "Forma de Pago:", "Helvetica-Bold", 9)
    # Si la forma de pago es larga, se corre a la izquierda para no pisar "Fecha de Pago:"
    forma = f.get("forma_pago", "")
    h.texto(min(131, 280 - pdfmetrics.stringWidth(forma, "Helvetica", 8)), 300.6, forma, "Helvetica", 8)
    h.texto(283.5, 300.9, "Fecha de Pago:", "Helvetica-Bold", 9)
    if s.get("fecha_pago"):
        h.texto(351, 301.1, _fecha(s["fecha_pago"]), "Helvetica", 8)
    h.texto(407, 300.9, "Incoterms:", "Helvetica-Bold", 9)

    # Tabla de ítems
    for x0, x1 in [(18, 48), (48, 352), (352, 428), (428, 507), (507, 577)]:
        h.rect(x0, 309, x1, 327, relleno=0.8)
    h.texto(24.8, 321.04, "Ítem", "Helvetica-Bold", 8)
    h.texto(52, 321.04, "Descripción", "Helvetica-Bold", 8)
    h.texto(372.9, 321.04, "Cantidad", "Helvetica-Bold", 8)
    h.texto(437.4, 320.66, f"Precio Unit. ({iso})", "Helvetica-Bold", 7)
    h.texto(508.2, 320.66, f"Total por ítem ({iso})", "Helvetica-Bold", 7)
    y = 340.66
    for i, it in enumerate(f["items"], 1):
        cantidad = Decimal(str(it.get("cantidad", 1)))
        precio = Decimal(str(it["precio"]))
        total = (cantidad * precio).quantize(Decimal("0.01")) - Decimal(str(it.get("bonificacion", 0)))
        h.texto(26.8, y - 0.3, f"{i:04d}", "Helvetica-Bold", 6)
        for linea in simpleSplit(it["descripcion"], "Helvetica", 7, 352 - 50 - 4)[:3]:
            h.texto(50, y, linea, "Helvetica", 7)
        h.texto(427, y, _coma(cantidad, 6), "Helvetica", 7, "der")
        h.texto(504, y, _coma(precio, 6), "Helvetica", 7, "der")
        h.texto(576, y, _coma(total, 2), "Helvetica", 7, "der")
        fin = h.texto(360.6, y + 12, "U. Medida:", "Helvetica-Bold", 7)
        h.texto(fin + 2, y + 12, desc["unidad"], "Helvetica", 7)
        y += 24

    # Totales
    h.rect(18, 655, 577, 704, 0.5)
    fin = h.texto(23, 671.54, f"Tipo de Cambio: {Decimal(s['cotizacion']):.6f}", "Helvetica-Bold", 8)
    h.linea(23, 672.2, fin, 672.2, 0.444)
    h.texto(572, 671.54, f"Divisa: {divisa}", "Helvetica-Bold", 8, "der")
    h.linea(572 - pdfmetrics.stringWidth(f"Divisa: {divisa}", "Helvetica-Bold", 8), 672.2, 572, 672.2, 0.444)
    h.texto(398.4, 693.29, "Importe Total:", "Helvetica-Bold", 10)
    h.texto(466.1, 692.16, iso, "Helvetica", 7)
    h.texto(572, 693.29, _coma(s["total"], 2), "Helvetica-Bold", 10, "der")

    # CAE, QR y leyendas
    _pie(h, s, None if s.get("vista_previa") else url_qr(s), 470, 718.29, 115, 710, 720, 759.4)
    h.texto(114.6, 776.3, "Esta Agencia no se responsabiliza por la veracidad de los datos ingresados en el detalle de la operación",
            "Helvetica-BoldOblique", 6)

    h.c.showPage()
    h.c.save()
    return destino


# --- Facturas comunes (A, B, C) ---

TIPO_NOTA_CREDITO = {"A": 3, "B": 8, "C": 13}
TIPO_CMP = {"A": 1, "B": 6, "C": 11}


def codigo_comprobante(s):
    return (TIPO_NOTA_CREDITO if s.get("nota_credito") else TIPO_CMP)[s["tipo"]]
DOC_ETIQUETA = {80: "CUIT", 86: "CUIL", 96: "DNI", 99: "Doc."}
# Textos de ARCA (FEParamGetCondicionIvaReceptor)
CONDICION_IVA = {
    1: "IVA Responsable Inscripto", 4: "IVA Sujeto Exento", 5: "Consumidor Final",
    6: "Responsable Monotributo", 7: "Sujeto No Categorizado", 8: "Proveedor del Exterior",
    9: "Cliente del Exterior", 10: "IVA Liberado – Ley N° 19.640", 13: "Monotributista Social",
    15: "IVA No Alcanzado", 16: "Monotributo Trabajador Independiente Promovido",
}


def _emisor(s):
    if not s.get("emisor"):
        raise ValueError("El JSON de la factura no tiene los datos del emisor")
    return s["emisor"]


def url_qr_comun(s, em):
    rec = s["receptor"]
    datos = {
        "ver": 1,
        "fecha": datetime.strptime(s["fecha"], "%Y%m%d").strftime("%Y-%m-%d"),
        "cuit": int(em["cuit"]),
        "ptoVta": s["punto_venta"],
        "tipoCmp": codigo_comprobante(s),
        "nroCmp": s["numero"],
        "importe": _numero(s["total"]),
        "moneda": s["moneda"],
        "ctz": _numero(s["cotizacion"]),
        "tipoDocRec": int(rec["doc_tipo"]),
        "nroDocRec": int(rec["doc_nro"] or 0),
        "tipoCodAut": "E",
        "codAut": int(s["cae"]),
    }
    p = base64.b64encode(json.dumps(datos, separators=(",", ":")).encode()).decode()
    return f"https://www.arca.gob.ar/fe/qr/?p={p}"


def generar_comun(s, destino):
    """Factura o nota de crédito C con el diseño de "Comprobantes en línea". A y B usan la misma base (sin detalle de IVA por ítem)."""
    em, rec, f = _emisor(s), s.get("receptor", {}), s["factura"]
    items = f["items"]
    rec_f = f.get("receptor", {})
    nombre_rec = rec.get("nombre") or rec_f.get("nombre", "")
    domicilio_rec = rec.get("domicilio") or rec_f.get("domicilio", "")
    signo = "$" if s["moneda"] == "PES" else MONEDA_ISO.get(s["moneda"], s["moneda"])
    h = _Hoja(destino)

    # Encabezado
    h.rect(15, 23, 581, 169)
    h.linea(15, 50.5, 581, 50.5)
    h.texto(298, 42.31, "ORIGINAL", "Helvetica-Bold", 14, "centro")
    h.linea(298.5, 89, 298.5, 169)
    h.rect(275, 51, 322, 92, 0.5)
    h.texto(298.5, 73.9, s["tipo"], "Helvetica-Bold", 24, "centro")
    h.texto(298.5, 86.04, f"COD. {codigo_comprobante(s):03d}", "Helvetica-Bold", 8, "centro")

    h.texto(144.5, 77.29, em["razon_social"], "Helvetica-Bold", 10, "centro")
    h.texto(21, 116.59, "Razón Social:", "Helvetica-Bold", 9)
    h.texto(84, 116.59, em["razon_social"])
    h.texto(21, 140.59, "Domicilio Comercial:", "Helvetica-Bold", 9)
    for i, linea in enumerate(simpleSplit(em["domicilio_comercial"], "Helvetica", 9, 295 - 116)[:2]):
        h.texto(116, 140.59 + i * 10.35, linea)
    h.texto(21, 165.41, "Condición frente al IVA:", "Helvetica-Bold", 9)
    h.texto(131, 165.41, em["condicion_iva"], "Helvetica-Bold", 9)

    h.texto(341, 77.33, "NOTA DE CRÉDITO" if s.get("nota_credito") else "FACTURA", "Helvetica-Bold", 18)
    h.texto(341, 99.24, "Punto de Venta:", "Helvetica-Bold", 9)
    if s.get("vista_previa"):
        h.texto(417, 100.04, A_ASIGNAR, "Helvetica-Bold", 10)
    else:
        h.texto(417, 100.04, f"{s['punto_venta']:05d}", "Helvetica-Bold", 10)
        h.texto(461, 99.24, "Comp. Nro:", "Helvetica-Bold", 9)
        h.texto(517, 100.04, f"{s['numero']:08d}", "Helvetica-Bold", 10)
    h.texto(341, 115.41, "Fecha de Emisión:", "Helvetica-Bold", 9)
    h.texto(428, 115.54, _fecha(s["fecha"]), "Helvetica-Bold", 10)
    h.texto(341, 138.41, "CUIT:", "Helvetica-Bold", 9)
    h.texto(369, 138.41, em["cuit"])
    h.texto(341, 150.41, "Ingresos Brutos:", "Helvetica-Bold", 9)
    h.texto(419, 150.41, em["ingresos_brutos"])
    h.texto(341, 162.41, "Fecha de Inicio de Actividades:", "Helvetica-Bold", 9)
    h.texto(486, 162.41, datetime.strptime(em["inicio_actividades"], "%Y-%m-%d").strftime("%d/%m/%Y"))

    # Período facturado (solo servicios)
    h.rect(15, 170, 581, 192)
    periodo = s.get("periodo")
    if periodo:
        h.texto(21, 185.29, "Período Facturado Desde:", "Helvetica-Bold", 10)
        h.texto(159, 185.29, _fecha(periodo["desde"]), "Helvetica", 10)
        h.texto(232.4, 185.29, "Hasta:", "Helvetica-Bold", 10)
        h.texto(265, 185.29, _fecha(periodo["hasta"]), "Helvetica", 10)
        h.texto(363.1, 185.29, "Fecha de Vto. para el pago:", "Helvetica-Bold", 10)
        h.texto(495, 185.29, _fecha(periodo["vto_pago"]), "Helvetica", 10)

    # Receptor
    h.rect(15, 194, 581, 256, 0.5)
    doc_tipo = int(rec.get("doc_tipo", 99))
    if doc_tipo != 99:
        fin = h.texto(21, 204.59, f"{DOC_ETIQUETA.get(doc_tipo, 'Doc.')}: ", "Helvetica-Bold", 9)
        h.texto(fin, 204.59, str(rec.get("doc_nro", "")))
    h.texto(222.4, 203.63, "Apellido y Nombre / Razón Social:", "Helvetica-Bold", 8)
    h.texto(353, 203.63, nombre_rec, "Helvetica", 8)
    h.texto(21, 220.63, "Condición frente al IVA:", "Helvetica-Bold", 8)
    h.texto(131, 220.63, CONDICION_IVA.get(int(rec.get("condicion_iva", 5)), ""), "Helvetica", 8)
    h.texto(312.4, 220.63, "Domicilio:", "Helvetica-Bold", 8)
    for i, linea in enumerate(simpleSplit(domicilio_rec, "Helvetica", 8, 578 - 353)[:3]):
        h.texto(353, 220.63 + i * 9.2, linea, "Helvetica", 8)
    h.texto(21, 240.63, "Condición de venta:", "Helvetica-Bold", 8)
    h.texto(113, 240.63, s.get("condicion_venta", "Contado"), "Helvetica", 8)

    # Tabla de ítems
    for x0, x1 in [(15, 55), (55, 196), (196, 261), (261, 304), (303, 384), (384, 416), (416, 489), (488, 581)]:
        h.rect(x0, 260, x1, 278, relleno=0.8)
    for x, txt, tam, y in [(19, "Código", 8, 272.04), (60, "Producto / Servicio", 8, 272.04), (211.4, "Cantidad", 8, 272.04),
                           (263.6, "U. Medida", 8, 272.04), (324.1, "Precio Unit.", 7, 271.66), (387, "% Bonif", 7, 271.66),
                           (434.4, "Imp. Bonif.", 7, 271.66), (520.5, "Subtotal", 7, 271.66)]:
        h.texto(x, y, txt, "Helvetica-Bold", tam)
    y = 291.04
    for it in items:
        cantidad = Decimal(str(it.get("cantidad", 1)))
        precio = Decimal(str(it["precio"]))
        bruto = (cantidad * precio).quantize(Decimal("0.01"))
        bonif = Decimal(str(it.get("bonificacion", 0))).quantize(Decimal("0.01"))
        pct = (bonif / bruto * 100).quantize(Decimal("0.01")) if bruto else Decimal("0")
        if it.get("codigo"):
            h.texto(19, y, str(it["codigo"])[:8], "Helvetica", 8)
        lineas = simpleSplit(it["descripcion"], "Helvetica", 8, 196 - 57 - 2)[:3]
        for i, linea in enumerate(lineas):
            h.texto(57, y + i * 10, linea, "Helvetica", 8)
        h.texto(259, y, _coma(cantidad, 2), "Helvetica", 8, "der")
        h.texto(268.3, y - 0.38, "unidades", "Helvetica", 7)
        h.texto(382, y, _coma(precio, 2), "Helvetica", 8, "der")
        h.texto(407.8, y, _coma(pct, 2), "Helvetica", 8, "der")
        h.texto(487, y, _coma(bonif, 2), "Helvetica", 8, "der")
        h.texto(579, y, _coma(bruto - bonif, 2), "Helvetica", 8, "der")
        y += max(14, 10 * len(lineas) + 4)
    asoc = s.get("asociado")
    if asoc:
        h.texto(57, y + 6, f"Comprobante asociado: Factura {asoc['tipo']} {asoc['punto_venta']:05d}-{asoc['numero']:08d}"
                f" del {_fecha(asoc['fecha'])}", "Helvetica-Bold", 8)

    # Totales
    h.rect(15, 517, 581, 611, 1.0)
    if s["tipo"] == "C":
        filas = [("Subtotal: " + signo, s["total"], 9, 561.59), ("Importe Otros Tributos: " + signo, "0", 9, 579.59)]
    else:
        filas = [("Importe Neto Gravado: " + signo, s["neto"], 9, 543.59), ("IVA: " + signo, s["iva"], 9, 561.59),
                 ("Importe Otros Tributos: " + signo, "0", 9, 579.59)]
    for etiqueta, valor, tam, yy in filas:
        h.texto(491, yy, etiqueta, "Helvetica-Bold", tam, "der")
        h.texto(573, yy, _coma(valor, 2), "Helvetica-Bold", tam, "der")
    h.texto(491, 598.54, f"Importe Total: {signo}", "Helvetica-Bold", 10, "der")
    h.texto(573, 598.54, _coma(s["total"], 2), "Helvetica-Bold", 10, "der")

    # CAE, QR y leyendas
    h.texto(275.8, 657.29, "Pág. 1/1", "Helvetica-Bold", 10)
    _pie(h, s, None if s.get("vista_previa") else url_qr_comun(s, em), 473, 654.54, 113, 653, 653, 695.59)
    h.texto(113, 712.73, "Esta Agencia no se responsabiliza por los datos ingresados en el detalle de la operación",
            "Helvetica-BoldOblique", 6)

    h.c.showPage()
    h.c.save()
    return destino


def generar_desde(s, destino):
    """Genera el PDF con el diseño que corresponde al tipo. `destino` puede ser una ruta o un buffer."""
    return (generar_comun if s.get("tipo") in TIPO_CMP else generar)(s, destino)


def nombre_archivo(s):
    """Nombre que usa ARCA: <CUIT>_<tipo de comprobante>_<punto de venta>_<número>.pdf"""
    tipo = codigo_comprobante(s) if s.get("tipo") in TIPO_CMP else 19
    return f"{_emisor(s)['cuit']}_{tipo:03d}_{s['punto_venta']:05d}_{s['numero']:08d}.pdf"

