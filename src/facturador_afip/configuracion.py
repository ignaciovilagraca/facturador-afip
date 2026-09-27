"""Alta en ARCA: clave y CSR, certificados, perfil y en qué paso está cada uno.

Las funciones de la primera parte (alias, clave y CSR, cargar y validar certificados) no dependen de la carpeta de
datos: las usan la terminal, el servidor MCP y la web, que guarda todo cifrado en su base. Las de la segunda parte
trabajan sobre una carpeta de datos (`Datos`) y guían el alta paso a paso.

Lo que se hace en ARCA (WSASS, Administrador de Relaciones, puntos de venta) lo hace la persona con su clave fiscal;
acá se genera lo que hay que llevar a ARCA, se guarda lo que ARCA devuelve y se verifica.
La clave privada se genera acá y nunca se devuelve ni se lee desde una herramienta.
"""
import json
import re
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .arca import ErrorArca
from .datos import Datos

ARCHIVOS = {  # entorno -> (clave, CSR, certificado)
    "homo": ("afip.key", "afip.csr", "afip_homo.crt"),
    "prod": ("afip_prod.key", "afip_prod.csr", "afip_prod.crt"),
}
CAMPOS_EMISOR = {
    "razon_social": "AFIP_RAZON_SOCIAL",
    "domicilio_comercial": "AFIP_DOMICILIO_COMERCIAL",
    "condicion_iva": "AFIP_CONDICION_IVA",
    "ingresos_brutos": "AFIP_INGRESOS_BRUTOS",
    "inicio_actividades": "AFIP_INICIO_ACTIVIDADES",
}
CLAVES_PERFIL = {"nombre", "punto_venta_prod", "punto_venta_prod_comunes", "condicion_iva_emisor", "alias_certificado",
                 "vencimiento_certificado", "drive_folder_id", "formato", "fechas", "cliente_por_defecto"}


def _plantilla(nombre):
    return (resources.files("facturador_afip") / "plantillas" / nombre).read_text()


# --- Clave, CSR y certificados (sin carpeta de datos) ---

def alias_valido(alias) -> bool:
    """ARCA acepta como alias solo letras y números: sin guiones, espacios ni acentos."""
    return bool(re.fullmatch(r"[A-Za-z0-9]{1,50}", alias or ""))


def _csr(clave, cuit, nombre, alias) -> str:
    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, nombre),
        x509.NameAttribute(NameOID.COMMON_NAME, alias),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}"),
    ])
    csr = x509.CertificateSigningRequestBuilder().subject_name(sujeto).sign(clave, hashes.SHA256())
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def generar_clave_y_csr(cuit, nombre, alias) -> tuple[bytes, str]:
    """Clave RSA de 2048 bits y el pedido de certificado (CSR) con el formato que pide ARCA.
    Devuelve (clave privada en PEM, CSR en PEM). La clave no se comparte nunca; el CSR es público."""
    if not alias_valido(alias):
        raise ErrorArca("El alias va solo con letras y números: ARCA rechaza guiones, espacios y acentos")
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
    return pem, _csr(clave, cuit, nombre, alias)


def cargar_certificado(contenido) -> x509.Certificate:
    """Acepta el certificado en PEM (el texto que muestra WSASS o un .crt) o en DER (.cer), como texto o bytes.
    Distingue lo que se pega por error: el CSR o la clave privada."""
    crudo = contenido.encode() if isinstance(contenido, str) else bytes(contenido or b"")
    if b"PRIVATE KEY" in crudo:
        raise ErrorArca("Eso es una clave privada, no un certificado. No la compartas con nadie; si la mandaste a "
                        "algún lado, conviene generar una nueva con otro alias y hacer el alta de nuevo.")
    if b"CERTIFICATE REQUEST" in crudo:
        raise ErrorArca("Eso es el CSR (el pedido), no el certificado. El certificado lo da ARCA: en homologación, "
                        "el texto que muestra WSASS después de 'Crear DN y obtener certificado'; en producción, el "
                        "archivo que se baja con Ver → Descargar.")
    try:
        return x509.load_pem_x509_certificate(crudo.strip())
    except ValueError:
        pass
    try:
        return x509.load_der_x509_certificate(crudo)
    except ValueError as e:
        raise ErrorArca("No es un certificado válido. Tiene que empezar con -----BEGIN CERTIFICATE----- o ser el "
                        "archivo .crt o .cer que dio ARCA.") from e


def alias_de(cert) -> str | None:
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return cn[0].value if cn else None


def emisor_de(cert) -> str:
    cn = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    return cn[0].value if cn else ""


def validar_certificado(contenido, clave_pem: bytes, entorno: str, cuit: str | None = None) -> tuple[str, datetime]:
    """Controla que el certificado corresponda a la clave, al entorno y, si se pasa, al CUIT.
    Devuelve (certificado en PEM, vencimiento)."""
    if entorno not in ARCHIVOS:
        raise ErrorArca(f"Entorno no válido: {entorno} (homo o prod)")
    cert = cargar_certificado(contenido)
    emisor = emisor_de(cert)
    es_test = "test" in emisor.lower()
    if entorno == "homo" and not es_test:
        raise ErrorArca(f"Este certificado es de producción (emisor '{emisor}'): guardalo como certificado de producción")
    if entorno == "prod" and es_test:
        raise ErrorArca(f"Este certificado es de homologación (emisor '{emisor}'): guardalo como certificado de homologación")
    clave = serialization.load_pem_private_key(clave_pem, password=None)
    if cert.public_key().public_numbers() != clave.public_key().public_numbers():
        raise ErrorArca(f"El certificado no corresponde a la clave de {entorno}: ARCA lo emitió para otro CSR. "
                        f"Pedí el certificado con el CSR de {entorno}.")
    if cuit:
        serie = cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
        if not serie or cuit not in serie[0].value:
            raise ErrorArca(f"El certificado no es del CUIT {cuit}")
    vence = cert.not_valid_after_utc
    if vence < datetime.now(timezone.utc):
        raise ErrorArca(f"El certificado venció el {vence.date()}")
    return cert.public_bytes(serialization.Encoding.PEM).decode(), vence


# --- En qué paso está ---

def etapa(datos: Datos) -> dict:
    """El próximo paso del alta, a partir de lo que hay en la carpeta de datos."""
    env = datos.env
    faltan_emisor = [campo for campo, var in CAMPOS_EMISOR.items() if not env.get(var)]
    if not datos.cuit:
        return _paso("sin_configurar", "iniciar_configuracion",
                     "Pedirle el CUIT, su nombre o razón social y los datos del emisor, elegir un alias solo con letras "
                     "y números, y llamar a iniciar_configuracion.", "paso1")
    if not all((datos.certs / ARCHIVOS[e][0]).exists() for e in ARCHIVOS):
        return _paso("sin_claves", "iniciar_configuracion",
                     "Llamar a iniciar_configuracion con el mismo CUIT para generar la clave y el CSR que faltan.", "paso1")
    for env_, seccion in (("homo", "homologacion"), ("prod", "produccion_certificado")):
        c = datos.certificado(env_)
        if not c.get("existe") or not c.get("valido"):
            return _paso(f"falta_certificado_{env_}", "guardar_certificado",
                         f"Guiar a la persona para obtener el certificado de {'homologación en WSASS' if env_ == 'homo' else 'producción en Administración de Certificados Digitales'} "
                         f"y guardarlo con guardar_certificado (entorno {env_}). El CSR está en ver_csr.", seccion)
    if datos.perfil() is None:
        return _paso("sin_perfil", "guardar_perfil",
                     "Preguntarle su condición frente al IVA, sus puntos de venta de producción, su cliente habitual y "
                     "cómo quiere las facturas, y guardarlo con guardar_perfil.", "punto_de_venta")
    if faltan_emisor:
        return _paso("faltan_datos_emisor", "iniciar_configuracion",
                     f"Faltan datos del emisor para el PDF ({', '.join(faltan_emisor)}): pedirlos y llamar a "
                     "iniciar_configuracion con el mismo CUIT y alias.", "paso1")
    return _paso("listo_para_verificar", "probar_conexion",
                 "Verificar con probar_conexion en homo y en prod, con wsfe (A, B y C) y wsfex (E) según lo que use. "
                 "Si falla, la sección verificar_y_errores de la guía dice qué falta en ARCA (autorización, relación "
                 "o punto de venta).", "verificar_y_errores")


def _paso(nombre, herramienta, que_hacer, seccion):
    return {"etapa": nombre, "herramienta": herramienta, "siguiente_paso": que_hacer, "seccion_guia": seccion}


def falta_configurar(datos: Datos) -> str | None:
    """Mensaje para lo que necesita la configuración completa, o None si no falta nada básico."""
    e = etapa(datos)
    if e["etapa"] in ("sin_perfil", "faltan_datos_emisor", "listo_para_verificar"):
        return None
    return (f"Falta configurar el facturador (etapa: {e['etapa']}). Siguiente paso: {e['siguiente_paso']} "
            "La guía completa está en guia_alta_arca.")


# --- Guía ---

def guia(seccion: str | None = None) -> str:
    texto = (resources.files("facturador_afip") / "guia" / "alta_arca.md").read_text()
    if not seccion:
        return texto
    partes = re.split(r"<!-- seccion:(\w+) -->\n", texto)
    secciones = dict(zip(partes[1::2], partes[2::2]))
    if seccion not in secciones:
        raise ErrorArca(f"Sección no válida: {seccion} (válidas: {', '.join(secciones)})")
    return secciones[seccion].strip()


def secciones_guia() -> list[str]:
    return re.findall(r"<!-- seccion:(\w+) -->", guia())


# --- Paso 1: carpeta, .env, clave y CSR ---

def actualizar_env(datos: Datos, valores: dict):
    """Escribe valores en .env conservando el resto del archivo (comentarios y variables que no se tocan)."""
    archivo = datos.raiz / ".env"
    lineas = archivo.read_text().splitlines() if archivo.exists() else _plantilla("env").splitlines()
    pendientes = dict(valores)
    for i, linea in enumerate(lineas):
        clave = linea.split("=", 1)[0].strip() if "=" in linea and not linea.lstrip().startswith("#") else None
        if clave in pendientes:
            lineas[i] = f"{clave}={pendientes.pop(clave)}"
    lineas += [f"{k}={v}" for k, v in pendientes.items()]
    archivo.write_text("\n".join(lineas) + "\n")
    archivo.chmod(0o600)


def iniciar(datos: Datos, cuit: str, nombre: str, alias: str, emisor: dict | None = None) -> dict:
    """Crea la carpeta de datos, escribe el CUIT y los datos del emisor, y genera las claves y los CSR que falten.
    Nunca pisa una clave existente: las autorizaciones de ARCA dependen de ella."""
    cuit = re.sub(r"\D", "", cuit or "")
    if len(cuit) != 11:
        raise ErrorArca("El CUIT tiene que tener 11 dígitos")
    if not alias_valido(alias):
        raise ErrorArca("El alias va solo con letras y números: ARCA rechaza guiones, espacios y acentos")
    if not (nombre or "").strip():
        raise ErrorArca("Falta el nombre o la razón social")
    if datos.cuit and datos.cuit != cuit:
        raise ErrorArca(f"La carpeta {datos.raiz} ya está configurada con otro CUIT ({datos.cuit}). Usá otra carpeta "
                        "de datos para otro contribuyente.")
    emisor = {k: v for k, v in (emisor or {}).items() if v not in (None, "")}
    if "inicio_actividades" in emisor:
        try:
            date.fromisoformat(emisor["inicio_actividades"])
        except ValueError as e:
            raise ErrorArca("inicio_actividades va como AAAA-MM-DD") from e

    datos.certs.mkdir(parents=True, exist_ok=True)
    datos.certs.chmod(0o700)
    (datos.facturas / "prod").mkdir(parents=True, exist_ok=True)
    valores = {"AFIP_CUIT": cuit}
    if not datos.env.get("AFIP_RAZON_SOCIAL") and "razon_social" not in emisor:
        valores["AFIP_RAZON_SOCIAL"] = nombre.strip()
    valores |= {CAMPOS_EMISOR[k]: v for k, v in emisor.items() if k in CAMPOS_EMISOR}
    actualizar_env(datos, valores)

    creados = []
    for clave_nombre, csr_nombre, _ in ARCHIVOS.values():
        clave_archivo, csr_archivo = datos.certs / clave_nombre, datos.certs / csr_nombre
        if not clave_archivo.exists():
            clave_pem, csr = generar_clave_y_csr(cuit, nombre.strip(), alias)
            clave_archivo.write_bytes(clave_pem)
            clave_archivo.chmod(0o600)
            creados.append(clave_nombre)
            if not csr_archivo.exists():
                csr_archivo.write_text(csr)
                creados.append(csr_nombre)
        elif not csr_archivo.exists():
            clave = serialization.load_pem_private_key(clave_archivo.read_bytes(), password=None)
            csr_archivo.write_text(_csr(clave, cuit, nombre.strip(), alias))
            creados.append(csr_nombre)
    return {"carpeta": str(datos.raiz), "creados": creados, "csr": {e: ver_csr(datos, e) for e in ARCHIVOS},
            "siguiente": etapa(datos)}


def ver_csr(datos: Datos, entorno: str) -> dict:
    """El pedido de certificado (público) de un entorno: texto, ruta y alias."""
    archivo = datos.certs / ARCHIVOS[entorno][1]
    if not archivo.exists():
        raise ErrorArca(f"No hay CSR de {entorno}: llamá a iniciar_configuracion")
    csr = x509.load_pem_x509_csr(archivo.read_bytes())
    cn = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return {"entorno": entorno, "alias": cn[0].value if cn else None, "ruta": str(archivo),
            "texto": archivo.read_text().strip()}


# --- Certificados que devuelve ARCA ---

def _leer_certificado(texto: str | None, ruta: str | None) -> bytes:
    if bool(texto) == bool(ruta):
        raise ErrorArca("Pasá el certificado como texto o como ruta al archivo, una de las dos")
    if not ruta:
        return texto.strip().encode()
    archivo = Path(ruta).expanduser()
    if archivo.suffix.lower() not in (".crt", ".pem", ".cer"):
        raise ErrorArca("La ruta tiene que ser el certificado que descargaste de ARCA (.crt, .pem o .cer)")
    if not archivo.is_file():
        raise ErrorArca(f"No existe el archivo {archivo}")
    if archivo.stat().st_size > 20_000:
        raise ErrorArca("El archivo es demasiado grande para ser un certificado")
    return archivo.read_bytes()


def guardar_certificado(datos: Datos, entorno: str, texto: str | None = None, ruta: str | None = None) -> dict:
    if entorno not in ARCHIVOS:
        raise ErrorArca(f"Entorno no válido: {entorno} (homo o prod)")
    crudo = _leer_certificado(texto, ruta)
    clave_archivo = datos.certs / ARCHIVOS[entorno][0]
    if not clave_archivo.exists():
        raise ErrorArca("Todavía no hay clave para este entorno: llamá primero a iniciar_configuracion")
    pem, vence = validar_certificado(crudo, clave_archivo.read_bytes(), entorno, cuit=datos.cuit or None)
    cert = cargar_certificado(pem)

    destino = datos.certs / ARCHIVOS[entorno][2]
    if destino.exists() and destino.read_text() != pem:
        destino.rename(destino.with_suffix(f".crt.{datetime.now():%Y%m%d%H%M%S}.bak"))
    destino.write_text(pem)
    alias = alias_de(cert)

    perfil = datos.perfil()
    if entorno == "prod" and perfil is not None:
        perfil |= {"alias_certificado": alias, "vencimiento_certificado": vence.date().isoformat()}
        _escribir_perfil(datos, perfil)
    return {"entorno": entorno, "guardado": str(destino), "alias": alias, "emisor": emisor_de(cert),
            "vence": vence.date().isoformat(), "siguiente": etapa(datos)}


# --- Perfil ---

def _escribir_perfil(datos: Datos, perfil: dict):
    datos.raiz.mkdir(parents=True, exist_ok=True)
    (datos.raiz / "perfil.json").write_text(json.dumps(perfil, indent=2, ensure_ascii=False) + "\n")


def guardar_perfil(datos: Datos, cambios: dict) -> dict:
    """Actualiza perfil.json con los campos recibidos. Si no existe, parte de la plantilla."""
    desconocidas = set(cambios) - CLAVES_PERFIL
    if desconocidas:
        raise ErrorArca(f"Campos de perfil no válidos: {', '.join(sorted(desconocidas))} "
                        f"(válidos: {', '.join(sorted(CLAVES_PERFIL))})")
    condicion = cambios.get("condicion_iva_emisor")
    if condicion is not None and condicion not in ("monotributo", "responsable_inscripto"):
        raise ErrorArca("condicion_iva_emisor es monotributo o responsable_inscripto")
    for campo in ("punto_venta_prod", "punto_venta_prod_comunes"):
        if cambios.get(campo) is not None and not (isinstance(cambios[campo], int) and 0 < cambios[campo] < 100000):
            raise ErrorArca(f"{campo} es un número de punto de venta, o null si no tiene")
    perfil = datos.perfil()
    if perfil is None:
        perfil = json.loads(_plantilla("perfil.json"))
        # La plantilla trae datos de ejemplo: se vacía lo que la persona no haya dicho
        perfil |= {"nombre": "", "punto_venta_prod": None, "punto_venta_prod_comunes": None, "drive_folder_id": "",
                   "cliente_por_defecto": None}
        c = datos.certificado("prod")
        perfil |= {"alias_certificado": c.get("alias"), "vencimiento_certificado": c.get("vence")}
    for clave, valor in cambios.items():
        if isinstance(valor, dict) and isinstance(perfil.get(clave), dict):
            perfil[clave] = {**perfil[clave], **valor}
        else:
            perfil[clave] = valor
    _escribir_perfil(datos, perfil)
    return {"perfil": perfil, "siguiente": etapa(datos)}
