"""Cliente mínimo de ARCA (ex AFIP): login en WSAA y llamadas a WSFEX (Factura E) y WSFE (A, B, C).

Las credenciales y la caché de tickets se pasan por parámetro: la terminal y el servidor MCP las leen de la carpeta
de datos (`datos.py`); la web las guarda cifradas por usuario. No imprime nada: `log` recibe los mensajes.
"""
import base64
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7

ENTORNOS = {
    "homo": {
        "cert": "afip_homo.crt",
        "key": "afip.key",
        "wsaa": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        "wsfex": "https://wswhomo.afip.gov.ar/wsfexv1/service.asmx",
        "wsfe": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    },
    "prod": {
        "cert": "afip_prod.crt",
        "key": "afip_prod.key",
        "wsaa": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
        "wsfex": "https://servicios1.afip.gov.ar/wsfexv1/service.asmx",
        "wsfe": "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
    },
}

FE_NS = "http://ar.gov.afip.dif.fexv1/"   # WSFEX: Factura E (exportación)
FEV1_NS = "http://ar.gov.afip.dif.FEV1/"  # WSFE: facturas comunes (A, B, C)

# servicios1.afip.gov.ar (producción) negocia DH de 1024 bits, que OpenSSL 3
# rechaza en el nivel de seguridad por defecto. Se baja a SECLEVEL=1 sin
# desactivar la verificación del certificado del servidor.
TLS = ssl.create_default_context()
TLS.set_ciphers("DEFAULT@SECLEVEL=1")


class ErrorArca(Exception):
    """Un error de ARCA o de configuración, con un mensaje para mostrarle al usuario."""


@dataclass
class Credencial:
    cuit: str
    cert_pem: bytes
    key_pem: bytes


Auth = namedtuple("Auth", "token sign cuit")


def soap(url, action, body):
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap:Body>{body}</soap:Body></soap:Envelope>"
    )
    req = urllib.request.Request(
        url,
        data=envelope.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": action},
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=TLS) as r:
            return ET.fromstring(r.read())
    except urllib.error.HTTPError as e:
        # Los SOAP Fault vienen con HTTP 500
        try:
            fault = ET.fromstring(e.read()).find(".//faultstring")
        except ET.ParseError:
            fault = None
        raise ErrorArca(f"Error SOAP: {fault.text if fault is not None else e}") from e
    except urllib.error.URLError as e:
        raise ErrorArca(f"No se pudo conectar con ARCA: {e.reason}") from e


def firmar_tra(tra: bytes, cred: Credencial) -> bytes:
    """CMS firmado con el certificado, con el contenido adentro (lo que pide WSAA)."""
    try:
        cert = x509.load_pem_x509_certificate(cred.cert_pem)
        clave = serialization.load_pem_private_key(cred.key_pem, password=None)
    except ValueError as e:
        raise ErrorArca(f"El certificado o la clave no son válidos: {e}") from e
    return (pkcs7.PKCS7SignatureBuilder().set_data(tra)
            .add_signer(cert, clave, hashes.SHA256())
            .sign(serialization.Encoding.DER, []))


def login(env, servicio, cred, tickets, log=lambda _: None):
    """Devuelve el Auth (token, sign, cuit) del servicio. Reutiliza el ticket mientras siga vigente."""
    ta = tickets.leer(env, servicio)
    if ta and datetime.fromisoformat(ta["expira"]) > datetime.now(timezone.utc) + timedelta(minutes=5):
        log(f"WSAA ({servicio}): ticket en caché, vence {ta['expira']}")
        return Auth(ta["token"], ta["sign"], cred.cuit)

    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    tra = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<loginTicketRequest version="1.0"><header>'
        f"<uniqueId>{int(ahora.timestamp())}</uniqueId>"
        f"<generationTime>{(ahora - timedelta(minutes=10)).isoformat()}</generationTime>"
        f"<expirationTime>{(ahora + timedelta(minutes=10)).isoformat()}</expirationTime>"
        f"</header><service>{servicio}</service></loginTicketRequest>"
    ).encode()
    resp = soap(
        ENTORNOS[env]["wsaa"], "",
        '<loginCms xmlns="http://wsaa.view.sua.dvadac.desein.afip.gov">'
        f"<in0>{base64.b64encode(firmar_tra(tra, cred)).decode()}</in0></loginCms>",
    )
    ret = next(e.text for e in resp.iter() if e.tag.endswith("loginCmsReturn"))
    xml_ta = ET.fromstring(unescape(ret))
    ta = {"token": xml_ta.findtext(".//token"), "sign": xml_ta.findtext(".//sign"),
          "expira": xml_ta.findtext(".//expirationTime")}
    tickets.guardar(env, servicio, ta)
    log(f"WSAA ({servicio}): login OK, ticket vence {ta['expira']}")
    return Auth(ta["token"], ta["sign"], cred.cuit)


def _auth_xml(auth, extra=""):
    if not auth:
        return ""
    return f"<Auth><Token>{auth.token}</Token><Sign>{auth.sign}</Sign><Cuit>{auth.cuit}</Cuit>{extra}</Auth>"


# --- WSFEX: Factura E ---

def wsfex(env, metodo, auth, extra_auth="", body=""):
    """Llama a un método de WSFEX y devuelve el nodo <metodo>Result."""
    resp = soap(ENTORNOS[env]["wsfex"], FE_NS + metodo,
                f'<{metodo} xmlns="{FE_NS}">{_auth_xml(auth, extra_auth)}{body}</{metodo}>')
    return resp.find(f".//{{{FE_NS}}}{metodo}Result")


def campo(nodo, nombre):
    return nodo.findtext(f".//{{{FE_NS}}}{nombre}")


def errores(result):
    err = result.find(f"{{{FE_NS}}}FEXErr")
    if err is None or campo(err, "ErrCode") == "0":
        return ""
    return f"{campo(err, 'ErrCode')}: {campo(err, 'ErrMsg')}"


def puntos_de_venta(env, auth):
    r = wsfex(env, "FEXGetPARAM_PtoVenta", auth)
    return [(campo(p, "Pve_Nro"), campo(p, "Pve_Bloqueado"), campo(p, "Pve_FchBaja"))
            for p in r.iter(f"{{{FE_NS}}}ClsFEXResponse_PtoVenta")], errores(r)


def punto_de_venta_activo(env, auth):
    ptos, _ = puntos_de_venta(env, auth)
    activo = next((p[0] for p in ptos if p[1] == "N" and p[2] in (None, "", "NULL")), None)
    # En homologación no hay puntos de venta dados de alta y cualquiera sirve
    return activo or ("1" if env == "homo" else None)


def ultimo_comprobante(env, auth, pto, tipo=19):
    r = wsfex(env, "FEXGetLast_CMP", auth, f"<Pto_venta>{pto}</Pto_venta><Cbte_Tipo>{tipo}</Cbte_Tipo>")
    nro = campo(r, "Cbte_nro")
    if nro is None:
        raise ErrorArca(f"FEXGetLast_CMP: {errores(r)}")
    return int(nro)


def _param(env, auth, metodo, nodo, cod, ds, valor):
    r = wsfex(env, metodo, auth)
    for x in r.iter(f"{{{FE_NS}}}{nodo}"):
        if campo(x, cod) == str(valor):
            return (campo(x, ds) or "").strip()
    return ""


def tabla_parametro(env, auth, metodo, nodo, cod, ds):
    """Lista (código, descripción) de una tabla de WSFEX."""
    r = wsfex(env, metodo, auth)
    return [(campo(x, cod), (campo(x, ds) or "").strip()) for x in r.iter(f"{{{FE_NS}}}{nodo}")]


def descripciones_parametros(env, auth, factura):
    """Textos de las tablas de ARCA que van impresos en el PDF."""
    cli = factura["cliente"]
    return {
        "pais": _param(env, auth, "FEXGetPARAM_DST_pais", "ClsFEXResponse_DST_pais",
                       "DST_Codigo", "DST_Ds", factura["pais_destino"]),
        "cuit_pais": _param(env, auth, "FEXGetPARAM_DST_CUIT", "ClsFEXResponse_DST_cuit",
                            "DST_CUIT", "DST_Ds", cli["cuit_pais"]) if cli.get("cuit_pais") else "",
        "moneda": _param(env, auth, "FEXGetPARAM_MON", "ClsFEXResponse_Mon",
                         "Mon_Id", "Mon_Ds", factura.get("moneda", "DOL")),
        "unidad": _param(env, auth, "FEXGetPARAM_UMed", "ClsFEXResponse_UMed",
                         "Umed_Id", "Umed_Ds", factura["items"][0].get("unidad", 7)),
    }


# --- WSFE: facturas comunes (A, B, C) ---

def wsfe(env, metodo, auth, body=""):
    """Llama a un método de WSFE y devuelve el nodo <metodo>Result."""
    resp = soap(ENTORNOS[env]["wsfe"], FEV1_NS + metodo,
                f'<{metodo} xmlns="{FEV1_NS}">{_auth_xml(auth)}{body}</{metodo}>')
    return resp.find(f".//{{{FEV1_NS}}}{metodo}Result")


def campo_fe(nodo, nombre):
    return nodo.findtext(f".//{{{FEV1_NS}}}{nombre}")


def mensajes_fe(nodo, contenedor, item):
    """Lista 'código: mensaje' de Errors/Err, Observaciones/Obs o Events/Evt."""
    return [f"{campo_fe(e, 'Code')}: {campo_fe(e, 'Msg')}"
            for c in nodo.iter(f"{{{FEV1_NS}}}{contenedor}")
            for e in c.iter(f"{{{FEV1_NS}}}{item}")]


def puntos_de_venta_fe(env, auth):
    r = wsfe(env, "FEParamGetPtosVenta", auth)
    return [(campo_fe(p, "Nro"), campo_fe(p, "EmisionTipo"), campo_fe(p, "Bloqueado"), campo_fe(p, "FchBaja"))
            for p in r.iter(f"{{{FEV1_NS}}}PtoVenta")], mensajes_fe(r, "Errors", "Err")


def punto_de_venta_activo_fe(env, auth):
    ptos, _ = puntos_de_venta_fe(env, auth)
    activo = next((p[0] for p in ptos if p[2] == "N" and p[3] in (None, "", "NULL")), None)
    # En homologación no hay puntos de venta dados de alta y cualquiera sirve
    return activo or ("1" if env == "homo" else None)


def ultimo_comprobante_fe(env, auth, pto, tipo):
    r = wsfe(env, "FECompUltimoAutorizado", auth, f"<PtoVta>{pto}</PtoVta><CbteTipo>{tipo}</CbteTipo>")
    nro = campo_fe(r, "CbteNro")
    if nro is None:
        raise ErrorArca(f"FECompUltimoAutorizado: {mensajes_fe(r, 'Errors', 'Err')}")
    return int(nro)


# --- Padrón: Constancia de Inscripción (ws_sr_constancia_inscripcion) ---

PADRON = {
    "homo": "https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5",
    "prod": "https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5",
}
SERVICIO_PADRON = "ws_sr_constancia_inscripcion"
IMPUESTO_IVA, IMPUESTO_MONOTRIBUTO, IMPUESTO_IVA_EXENTO = 30, 20, 32


def _hijos(nodo, nombre):
    return [h for h in nodo.iter() if h.tag.split("}")[-1] == nombre]


def _texto(nodo, nombre):
    encontrados = _hijos(nodo, nombre)
    return (encontrados[0].text or "").strip() if encontrados else ""


def consultar_constancia(env, auth, cuit):
    """Datos de la constancia de inscripción de un CUIT, listos para el formulario del emisor.

    Devuelve razon_social, domicilio, condicion_iva ('monotributo', 'responsable_inscripto', 'exento' o None),
    inicio_actividades (sugerido: el mes de la actividad más antigua) y pista_iibb (texto, puede venir vacío).
    """
    cuerpo = (f'<a5:getPersona_v2 xmlns:a5="http://a5.soap.ws.server.puc.sr/"><token>{auth.token}</token>'
              f"<sign>{auth.sign}</sign><cuitRepresentada>{auth.cuit}</cuitRepresentada>"
              f"<idPersona>{cuit}</idPersona></a5:getPersona_v2>")
    r = soap(PADRON[env], "", cuerpo)
    persona = next(iter(_hijos(r, "personaReturn")), None)
    if persona is None:
        raise ErrorArca("ARCA no devolvió datos para ese CUIT")
    errores_constancia = [(_texto(e, "error") or (e.text or "").strip()) for e in _hijos(persona, "errorConstancia")]
    generales = next(iter(_hijos(persona, "datosGenerales")), None)
    if generales is None:
        raise ErrorArca("ARCA no devolvió la constancia: " + (" | ".join(errores_constancia) or "sin detalle"))

    razon = _texto(generales, "razonSocial") or " ".join(
        x for x in (_texto(generales, "apellido"), _texto(generales, "nombre")) if x)

    domicilio = ""
    fiscal = next(iter(_hijos(generales, "domicilioFiscal")), None)
    if fiscal is not None:
        provincia = ("Ciudad Autónoma de Buenos Aires" if _texto(fiscal, "idProvincia") == "0"
                     else _texto(fiscal, "descripcionProvincia").title())
        lugar = ", ".join(x for x in (_texto(fiscal, "localidad").title(), provincia) if x)
        domicilio = " - ".join(x for x in (_texto(fiscal, "direccion").title(), lugar) if x)

    impuestos = {}
    for imp in _hijos(persona, "impuesto"):
        try:
            impuestos[int(_texto(imp, "idImpuesto"))] = (_texto(imp, "estadoImpuesto"), _texto(imp, "descripcionImpuesto"),
                                                        _texto(imp, "motivo"))
        except ValueError:
            continue
    activo = lambda i: impuestos.get(i, ("",))[0] == "AC"  # noqa: E731
    condicion = ("monotributo" if activo(IMPUESTO_MONOTRIBUTO) or _hijos(persona, "categoriaMonotributo")
                 else "responsable_inscripto" if activo(IMPUESTO_IVA)
                 else "exento" if activo(IMPUESTO_IVA_EXENTO) else None)

    periodos = [p for p in (_texto(a, "periodo") for a in _hijos(persona, "actividad")) if len(p) == 6 and p.isdigit()]
    inicio = f"{min(periodos)[:4]}-{min(periodos)[4:]}-01" if periodos else ""

    pista_iibb = next((f"{ds}: {motivo.capitalize() or estado}" for estado, ds, motivo in impuestos.values()
                       if "IIBB" in ds.upper() or "INGRESOS BRUTOS" in ds.upper()), "")
    return {"razon_social": razon, "domicilio": domicilio, "condicion_iva": condicion,
            "inicio_actividades": inicio, "pista_iibb": pista_iibb}
