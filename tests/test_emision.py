"""Preparación de comprobantes con ARCA simulada: nada sale a la red."""
import xml.etree.ElementTree as ET

import pytest

from facturador_afip import emision
from facturador_afip.arca import FE_NS, Auth, ErrorArca

CUIT = "20111111112"  # ficticio
AUTH = Auth("token", "sign", CUIT)
FACTURA_C = {
    "tipo": "C", "concepto": 2, "fecha": "2026-09-30",
    "receptor": {"nombre": "Cliente de prueba", "doc_tipo": "DNI", "doc_nro": "30123456", "condicion_iva": 5},
    "items": [{"descripcion": "Consultoría", "cantidad": 1, "precio": 150000}],
}
FACTURA_E = {
    "tipo_expo": 2, "pais_destino": 212,
    "cliente": {"nombre": "Example Corp", "cuit_pais": "55000002126", "id_impositivo": "00-0000000",
                "domicilio": "123 Main St, New York, NY 10001, USA"},
    "moneda": "DOL", "cotizacion": 1000, "idioma": 1, "forma_pago": "Transferencia bancaria",
    "fecha": "2026-09-30", "fecha_pago": "2026-10-01",
    "items": [{"codigo": "SERV", "descripcion": "Consultoría de software", "cantidad": 1, "unidad": 7, "precio": 1000}],
}


def _ultimo_id(env, metodo, auth, extra_auth="", body=""):
    r = ET.Element("r")
    ET.SubElement(r, f"{{{FE_NS}}}Id").text = "3"
    return r


@pytest.fixture(autouse=True)
def arca_simulada(monkeypatch):
    monkeypatch.setattr(emision, "ultimo_comprobante_fe", lambda env, auth, pto, tipo: 41)
    monkeypatch.setattr(emision, "ultimo_comprobante", lambda env, auth, pto, tipo=19: 7)
    monkeypatch.setattr(emision, "wsfex", _ultimo_id)


def test_factura_c():
    prep = emision.preparar(FACTURA_C, "homo", AUTH, 4)
    assert (prep.tipo, prep.servicio, prep.cbte_tipo, prep.punto_venta, prep.numero) == ("C", "wsfe", 11, 4, 42)
    assert "<CbteTipo>11</CbteTipo>" in prep.cuerpo and "<ImpTotal>150000.00</ImpTotal>" in prep.cuerpo
    assert "<CbtesAsoc>" not in prep.cuerpo
    assert prep.resumen["titulo"] == "Factura C 00004-00000042" and prep.resumen["neto"] is None
    assert prep.salida["periodo"] == {"desde": "20260901", "hasta": "20260930", "vto_pago": "20260930"}
    assert emision.nombre_registro({**prep.salida, "cae": "1"}) == "C-00004-00000042.json"


def test_factura_a_con_iva():
    f = {**FACTURA_C, "tipo": "A", "receptor": {"nombre": "Empresa", "doc_tipo": "CUIT", "doc_nro": "30-11111111-8",
                                                 "condicion_iva": 1},
         "items": [{"descripcion": "Servicio", "cantidad": 2, "precio": 1000, "iva": 21},
                   {"descripcion": "Otro", "precio": 100, "iva": 10.5}]}
    prep = emision.preparar(f, "homo", AUTH, 4)
    assert prep.cbte_tipo == 1 and prep.salida["neto"] == "2100.00" and prep.salida["iva"] == "430.50"
    assert prep.salida["total"] == "2530.50" and len(prep.salida["alicuotas"]) == 2
    assert "<DocNro>30111111118</DocNro>" in prep.cuerpo
    with pytest.raises(ErrorArca, match="exige el CUIT"):
        emision.preparar({**f, "receptor": {"doc_tipo": "DNI", "doc_nro": "1"}}, "homo", AUTH, 4)
    with pytest.raises(ErrorArca, match="Alícuota de IVA no válida"):
        emision.preparar({**f, "items": [{"descripcion": "x", "precio": 1, "iva": 15}]}, "homo", AUTH, 4)


def test_nota_de_credito_informa_comprobante_asociado():
    nc = {**FACTURA_C, "nota_credito_de": {"punto_venta": 4, "numero": 42, "fecha": "2026-09-30"}}
    prep = emision.preparar(nc, "homo", AUTH, 4)
    assert prep.cbte_tipo == 13 and "<CbteTipo>13</CbteTipo>" in prep.cuerpo
    assert f"<CbtesAsoc><CbteAsoc><Tipo>11</Tipo><PtoVta>4</PtoVta><Nro>42</Nro><Cuit>{CUIT}</Cuit>" in prep.cuerpo
    assert prep.resumen["titulo"].startswith("Nota de crédito C") and "00004-00000042" in prep.resumen["asociado"]
    assert emision.nombre_registro({**prep.salida, "cae": "1"}) == "NC-C-00004-00000042.json"
    with pytest.raises(ErrorArca, match="nota_credito_de necesita"):
        emision.preparar({**FACTURA_C, "nota_credito_de": {"numero": 1}}, "homo", AUTH, 4)


def test_factura_e():
    prep = emision.preparar(FACTURA_E, "homo", AUTH, 5)
    assert (prep.tipo, prep.servicio, prep.cbte_tipo, prep.numero) == ("E", "wsfex", 19, 8)
    assert "<Cbte_Tipo>19</Cbte_Tipo>" in prep.cuerpo and "<Id>4</Id>" in prep.cuerpo
    assert "<Fecha_pago>20261001</Fecha_pago>" in prep.cuerpo and "<Permiso_existente></Permiso_existente>" in prep.cuerpo
    assert prep.salida["total"] == "1000.00" and prep.salida["cotizacion"] == "1000"
    assert emision.nombre_registro({**prep.salida, "numero": 8, "cae": "1"}) == "E-00005-00000008.json"


def test_validaciones():
    casos = [
        ({**FACTURA_C, "items": []}, "no tiene ítems"),
        ({**FACTURA_C, "items": [{"descripcion": "x", "importe": 10}]}, "tiene que tener descripcion y precio"),
        ({**FACTURA_C, "items": [{"descripcion": "x", "precio": "diez"}]}, "no es un número"),
        ({**FACTURA_C, "tipo": "Z"}, "Tipo de comprobante no válido"),
        ({**FACTURA_E, "nota_credito_de": {"punto_venta": 1, "numero": 1, "fecha": "2026-01-01"}}, "todavía no"),
        ({**FACTURA_E, "cliente": {}}, "Falta el nombre del cliente"),
    ]
    for factura, esperado in casos:
        with pytest.raises(ErrorArca, match=esperado):
            emision.preparar(factura, "homo", AUTH, 4)
