"""Alta en ARCA sobre una carpeta de datos, con certificados de prueba firmados por una CA falsa. Sin red."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from facturador_afip import configuracion
from facturador_afip.arca import ErrorArca
from facturador_afip.datos import Datos

CUIT = "20111111112"  # ficticio


def certificado_de_arca(clave, entorno, cuit=CUIT, vence_en_dias=730):
    """Un certificado como el que da ARCA: CN = alias, serialNumber = "CUIT ...", emisor Computadores (Test)."""
    ca = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ahora = datetime.now(timezone.utc)
    emisor = "Computadores Test" if entorno == "homo" else "Computadores"
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "prueba1a2b"),
                                     x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}")]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, emisor)]))
            .public_key(clave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(ahora - timedelta(days=1)).not_valid_after(ahora + timedelta(days=vence_en_dias))
            .sign(ca, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def clave_de(datos, entorno):
    archivo = datos.certs / ("afip.key" if entorno == "homo" else "afip_prod.key")
    return serialization.load_pem_private_key(archivo.read_bytes(), password=None)


def iniciar(datos):
    return configuracion.iniciar(datos, "20-11111111-2", "Persona de Prueba", "prueba1a2b", {
        "condicion_iva": "Responsable Monotributo", "domicilio_comercial": "Calle 123, CABA",
        "ingresos_brutos": "Exento", "inicio_actividades": "2020-01-01"})


def test_alta_completa(tmp_path):
    datos = Datos(tmp_path / "datos")  # todavía no existe, como en una instalación nueva
    assert configuracion.etapa(datos)["etapa"] == "sin_configurar"
    assert "iniciar_configuracion" in configuracion.falta_configurar(datos)
    with pytest.raises(ErrorArca, match="estado_configuracion"):
        datos.credencial("homo")

    r = iniciar(datos)
    assert sorted(r["creados"]) == ["afip.csr", "afip.key", "afip_prod.csr", "afip_prod.key"]
    assert r["csr"]["homo"]["texto"].startswith("-----BEGIN CERTIFICATE REQUEST-----")
    assert r["csr"]["homo"]["alias"] == "prueba1a2b"
    assert "PRIVATE KEY" not in json.dumps(r)  # la clave nunca sale
    assert r["siguiente"]["etapa"] == "falta_certificado_homo"
    env = (datos.raiz / ".env").read_text()
    assert f"AFIP_CUIT={CUIT}" in env and "AFIP_CONDICION_IVA=Responsable Monotributo" in env
    assert oct((datos.certs / "afip_prod.key").stat().st_mode)[-3:] == "600"

    # Repetir no pisa las claves
    clave = (datos.certs / "afip_prod.key").read_bytes()
    r = iniciar(datos)
    assert r["creados"] == [] and (datos.certs / "afip_prod.key").read_bytes() == clave

    # Homologación: el texto que muestra WSASS
    r = configuracion.guardar_certificado(datos, "homo", texto=certificado_de_arca(clave_de(datos, "homo"), "homo"))
    assert r["alias"] == "prueba1a2b" and r["siguiente"]["etapa"] == "falta_certificado_prod"

    # Producción: la ruta del .crt descargado
    descargado = tmp_path / "Downloads" / "prueba1a2b.crt"
    descargado.parent.mkdir()
    descargado.write_text(certificado_de_arca(clave_de(datos, "prod"), "prod"))
    r = configuracion.guardar_certificado(datos, "prod", ruta=str(descargado))
    assert r["siguiente"]["etapa"] == "sin_perfil"
    assert datos.certificado("prod")["alias"] == "prueba1a2b"
    assert datos.credencial("prod").cuit == CUIT

    r = configuracion.guardar_perfil(datos, {"nombre": "Persona", "condicion_iva_emisor": "monotributo",
                                             "punto_venta_prod_comunes": 5, "formato": {"descripcion": "Consultoría"}})
    p = r["perfil"]
    assert p["punto_venta_prod_comunes"] == 5 and p["alias_certificado"] == "prueba1a2b"
    assert p["formato"]["descripcion"] == "Consultoría" and p["formato"]["idioma"] == 1  # se mezcla con la plantilla
    assert p["cliente_por_defecto"] is None  # la plantilla no deja datos de ejemplo
    assert r["siguiente"]["etapa"] == "listo_para_verificar"
    assert configuracion.falta_configurar(datos) is None


def test_certificados_equivocados(tmp_path):
    datos = Datos(tmp_path)
    iniciar(datos)
    otra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    casos = [
        ({"entorno": "homo", "texto": (datos.certs / "afip.csr").read_text()}, "Eso es el CSR"),
        ({"entorno": "homo", "texto": (datos.certs / "afip.key").read_text()}, "clave privada"),
        ({"entorno": "homo", "texto": certificado_de_arca(clave_de(datos, "homo"), "prod")}, "es de producción"),
        ({"entorno": "prod", "texto": certificado_de_arca(clave_de(datos, "prod"), "homo")}, "es de homologación"),
        ({"entorno": "homo", "texto": certificado_de_arca(otra, "homo")}, "no corresponde a la clave"),
        ({"entorno": "homo", "texto": certificado_de_arca(clave_de(datos, "homo"), "homo", cuit="20222222223")},
         "no es del CUIT"),
        ({"entorno": "homo", "texto": certificado_de_arca(clave_de(datos, "homo"), "homo", vence_en_dias=-1)}, "venció"),
        ({"entorno": "homo", "texto": "hola"}, "No es un certificado válido"),
        ({"entorno": "prod", "ruta": str(tmp_path / "no-existe.crt")}, "No existe"),
        ({"entorno": "prod", "ruta": str(datos.raiz / ".env")}, "tiene que ser el certificado"),
        ({"entorno": "homo"}, "una de las dos"),
    ]
    for args, esperado in casos:
        with pytest.raises(ErrorArca, match=esperado):
            configuracion.guardar_certificado(datos, **args)
    assert not (datos.certs / "afip_homo.crt").exists()


def test_validaciones_de_inicio_y_perfil(tmp_path):
    datos = Datos(tmp_path)
    for args, esperado in [({"alias": "con-guion"}, "solo con letras"), ({"cuit": "123"}, "11 dígitos"),
                           ({"emisor": {"inicio_actividades": "01/01/2020"}}, "AAAA-MM-DD")]:
        base = {"cuit": CUIT, "nombre": "X", "alias": "abc123"}
        with pytest.raises(ErrorArca, match=esperado):
            configuracion.iniciar(datos, **(base | args))
    iniciar(datos)
    with pytest.raises(ErrorArca, match="otro CUIT"):
        configuracion.iniciar(datos, "20222222223", "Otra", "otro1")
    for perfil, esperado in [({"inventado": 1}, "no válidos"), ({"condicion_iva_emisor": "rico"}, "monotributo"),
                             ({"punto_venta_prod": "cuatro"}, "número de punto de venta")]:
        with pytest.raises(ErrorArca, match=esperado):
            configuracion.guardar_perfil(datos, perfil)


def test_guia():
    assert configuracion.secciones_guia()[:2] == ["introduccion", "paso1"]
    h = configuracion.guia("homologacion")
    assert "WSASS" in h and "guardar_certificado" in h and "<!--" not in h
    assert "El servicio debe ser delegable" in configuracion.guia("verificar_y_errores")
    with pytest.raises(ErrorArca, match="Sección no válida"):
        configuracion.guia("inventada")


def test_clave_csr_y_certificado_sin_carpeta():
    """Lo que usa la web: todo en memoria, sin carpeta de datos."""
    clave_pem, csr = configuracion.generar_clave_y_csr(CUIT, "Persona de Prueba", "prueba1a2b")
    assert clave_pem.startswith(b"-----BEGIN ") and clave_pem.rstrip().endswith(b"KEY-----")
    assert csr.startswith("-----BEGIN CERTIFICATE REQUEST-----")
    clave = serialization.load_pem_private_key(clave_pem, password=None)
    pem, vence = configuracion.validar_certificado(certificado_de_arca(clave, "prod"), clave_pem, "prod", cuit=CUIT)
    assert pem.startswith("-----BEGIN CERTIFICATE-----") and vence > datetime.now(timezone.utc)
    # También en DER (.cer) y como bytes
    der = configuracion.cargar_certificado(pem).public_bytes(serialization.Encoding.DER)
    assert configuracion.validar_certificado(der, clave_pem, "prod")[0] == pem
    with pytest.raises(ErrorArca, match="no es del CUIT"):
        configuracion.validar_certificado(pem, clave_pem, "prod", cuit="20222222223")
    with pytest.raises(ErrorArca, match="solo con letras"):
        configuracion.generar_clave_y_csr(CUIT, "X", "con espacio")
    assert configuracion.alias_valido("abc123") and not configuracion.alias_valido("abc-123")
