"""El PDF se genera a partir del registro de una factura aprobada, en memoria."""
import io

import pytest

from facturador_afip import emision, pdf
from tests.test_emision import AUTH, CUIT, FACTURA_C, FACTURA_E, _ultimo_id

EMISOR = {"cuit": CUIT, "razon_social": "Emisor de prueba", "domicilio_comercial": "Calle 123, CABA",
          "condicion_iva": "Responsable Monotributo", "ingresos_brutos": "Exento", "inicio_actividades": "2020-01-01"}
DESCRIPCIONES = {"pais": "ESTADOS UNIDOS", "cuit_pais": "ESTADOS UNIDOS - PERSONA JURIDICA",
                 "moneda": "DOLAR ESTADOUNIDENSE", "unidad": "unidades"}


@pytest.fixture(autouse=True)
def arca_simulada(monkeypatch):
    monkeypatch.setattr(emision, "ultimo_comprobante_fe", lambda env, auth, pto, tipo: 41)
    monkeypatch.setattr(emision, "ultimo_comprobante", lambda env, auth, pto, tipo=19: 7)
    monkeypatch.setattr(emision, "wsfex", _ultimo_id)


def aprobada(factura, pto):
    """El registro que queda después de que ARCA aprueba: lo que devuelve `enviar`, sin la red."""
    prep = emision.preparar(factura, "homo", AUTH, pto)
    salida = {**prep.salida, "numero": prep.numero, "fecha": prep.salida.get("fecha", "20260930"),
              "cae": "76123456789012", "vencimiento_cae": "20261010", "observaciones": [], "eventos": [],
              "emisor": EMISOR, "factura": prep.factura}
    if prep.tipo == "E":
        salida["descripciones"] = DESCRIPCIONES
    return salida


def generar(salida):
    buffer = io.BytesIO()
    pdf.generar_desde(salida, buffer)
    contenido = buffer.getvalue()
    assert contenido.startswith(b"%PDF") and len(contenido) > 2000
    return contenido


def test_factura_c_y_nota_de_credito():
    s = aprobada(FACTURA_C, 4)
    generar(s)
    assert pdf.nombre_archivo(s) == f"{CUIT}_011_00004_00000042.pdf"
    nc = aprobada({**FACTURA_C, "nota_credito_de": {"punto_venta": 4, "numero": 42, "fecha": "2026-09-30"}}, 4)
    generar(nc)
    assert nc["nota_credito"] and pdf.nombre_archivo(nc) == f"{CUIT}_013_00004_00000042.pdf"


def test_factura_e():
    s = aprobada(FACTURA_E, 5)
    generar(s)
    assert pdf.nombre_archivo(s) == f"{CUIT}_019_00005_00000008.pdf"
    assert pdf.url_qr(s).startswith("https://www.arca.gob.ar/fe/qr/?p=")


def test_vista_previa_sin_cae():
    for factura, pto in ((FACTURA_C, 4), (FACTURA_E, 5)):
        s = {k: v for k, v in aprobada(factura, pto).items() if k not in ("cae", "vencimiento_cae")}
        generar({**s, "vista_previa": True})


def test_faltan_datos():
    s = aprobada(FACTURA_E, 5)
    with pytest.raises(ValueError, match="descripciones"):
        pdf.generar_desde({k: v for k, v in s.items() if k != "descripciones"}, io.BytesIO())
    with pytest.raises(ValueError, match="emisor"):
        pdf.generar_desde({k: v for k, v in s.items() if k != "emisor"}, io.BytesIO())
