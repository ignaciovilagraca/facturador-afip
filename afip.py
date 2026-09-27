"""Cliente mínimo de ARCA (ex AFIP): login en WSAA y llamadas a WSFEX (Factura E)."""
import base64
import json
import os
import ssl
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

RAIZ = Path(__file__).parent
CERTS = RAIZ / "certs"


def _cargar_env():
    env = RAIZ / ".env"
    if env.exists():
        for linea in env.read_text().splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                clave, valor = linea.split("=", 1)
                os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env()
CUIT = os.environ.get("AFIP_CUIT") or ""

ENTORNOS = {
    "homo": {
        "cert": CERTS / "afip_homo.crt",
        "key": CERTS / "afip.key",
        "wsaa": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        "wsfex": "https://wswhomo.afip.gov.ar/wsfexv1/service.asmx",
        "wsfe": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    },
    "prod": {
        "cert": CERTS / "afip_prod.crt",
        "key": CERTS / "afip_prod.key",
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
        tree = ET.fromstring(e.read())
        fault = tree.find(".//faultstring")
        raise SystemExit(f"Error SOAP: {fault.text if fault is not None else e}")


def login(env, servicio="wsfex"):
    """Devuelve (token, sign) para el servicio. Reutiliza el ticket mientras siga vigente."""
    if not CUIT:
        raise SystemExit("Falta AFIP_CUIT en .env")
    cfg = ENTORNOS[env]
    cache = CERTS / f"ta_{servicio}_{env}.json"
    if cache.exists():
        ta = json.loads(cache.read_text())
        if datetime.fromisoformat(ta["expira"]) > datetime.now(timezone.utc) + timedelta(minutes=5):
            print(f"WSAA ({servicio}): ticket en caché, vence {ta['expira']}")
            return ta["token"], ta["sign"]

    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    tra = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<loginTicketRequest version="1.0"><header>'
        f"<uniqueId>{int(ahora.timestamp())}</uniqueId>"
        f"<generationTime>{(ahora - timedelta(minutes=10)).isoformat()}</generationTime>"
        f"<expirationTime>{(ahora + timedelta(minutes=10)).isoformat()}</expirationTime>"
        f"</header><service>{servicio}</service></loginTicketRequest>"
    )
    cms = subprocess.run(
        ["openssl", "cms", "-sign", "-signer", cfg["cert"], "-inkey", cfg["key"],
         "-nodetach", "-outform", "DER"],
        input=tra.encode(), capture_output=True, check=True,
    ).stdout

    resp = soap(
        cfg["wsaa"], "",
        '<loginCms xmlns="http://wsaa.view.sua.dvadac.desein.afip.gov">'
        f"<in0>{base64.b64encode(cms).decode()}</in0></loginCms>",
    )
    ret = next(e.text for e in resp.iter() if e.tag.endswith("loginCmsReturn"))
    ta = ET.fromstring(unescape(ret))
    token, sign = ta.findtext(".//token"), ta.findtext(".//sign")
    expira = ta.findtext(".//expirationTime")
    cache.write_text(json.dumps({"token": token, "sign": sign, "expira": expira}))
    cache.chmod(0o600)
    print(f"WSAA ({servicio}): login OK, ticket vence {expira}")
    return token, sign


def wsfex(env, metodo, auth, extra_auth="", body=""):
    """Llama a un método de WSFEX y devuelve el nodo <metodo>Result."""
    cfg = ENTORNOS[env]
    auth_xml = (
        f"<Auth><Token>{auth[0]}</Token><Sign>{auth[1]}</Sign><Cuit>{CUIT}</Cuit>{extra_auth}</Auth>"
        if auth else ""
    )
    resp = soap(cfg["wsfex"], FE_NS + metodo,
                f'<{metodo} xmlns="{FE_NS}">{auth_xml}{body}</{metodo}>')
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
        raise SystemExit(f"FEXGetLast_CMP: {errores(r)}")
    return int(nro)


def _param(env, auth, metodo, nodo, cod, ds, valor):
    r = wsfex(env, metodo, auth)
    for x in r.iter(f"{{{FE_NS}}}{nodo}"):
        if campo(x, cod) == str(valor):
            return (campo(x, ds) or "").strip()
    return ""


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
    auth_xml = (f"<Auth><Token>{auth[0]}</Token><Sign>{auth[1]}</Sign><Cuit>{CUIT}</Cuit></Auth>"
                if auth else "")
    resp = soap(ENTORNOS[env]["wsfe"], FEV1_NS + metodo,
                f'<{metodo} xmlns="{FEV1_NS}">{auth_xml}{body}</{metodo}>')
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
        raise SystemExit(f"FECompUltimoAutorizado: {mensajes_fe(r, 'Errors', 'Err')}")
    return int(nro)
