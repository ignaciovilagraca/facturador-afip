"""Carpeta de datos del usuario: .env, certs/, perfil.json y facturas/.

La usan la terminal y el servidor MCP; conviene que compartan la misma carpeta: así comparten la caché de tickets de WSAA (ARCA no da un ticket nuevo mientras el anterior siga vigente) y la
lista de facturas emitidas.

    <carpeta>/
      .env                 AFIP_CUIT y datos del emisor para el PDF
      perfil.json          preferencias: puntos de venta, cliente por defecto, formato, fechas
      certs/               afip.key, afip_homo.crt, afip_prod.key, afip_prod.crt y la caché ta_*.json
      facturas/homo/       validaciones en homologación (sin valor fiscal)
      facturas/prod/       facturas emitidas: JSON y PDF
      facturas/mcp/        borradores del servidor MCP
"""
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cryptography import x509

from . import pdf
from .arca import ENTORNOS, Credencial, ErrorArca, descripciones_parametros, login

log = logging.getLogger("facturador_afip")

VARIABLE_CARPETA = "FACTURADOR_AFIP_DIR"
CARPETA_POR_DEFECTO = Path.home() / ".facturador-afip"
ID_BORRADOR = re.compile(r"^[A-Za-z0-9-]{1,80}$")
NOMBRE_REGISTRO = re.compile(r"^[A-Za-z0-9_.-]{1,120}\.json$")


def leer_env(archivo: Path) -> dict:
    valores = {}
    if archivo.exists():
        for linea in archivo.read_text().splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                clave, valor = linea.split("=", 1)
                valores[clave.strip()] = valor.strip()
    return valores


def huella(factura: dict) -> str:
    """Hash del JSON de la factura: garantiza que producción emita exactamente lo que se validó."""
    return hashlib.sha256(json.dumps(factura, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class TicketsEnArchivo:
    """Caché de tickets de WSAA en certs/ta_<servicio>_<entorno>.json, compartida con facturador-afip."""

    def __init__(self, certs: Path):
        self.certs = certs

    def leer(self, env, servicio):
        archivo = self.certs / f"ta_{servicio}_{env}.json"
        return json.loads(archivo.read_text()) if archivo.exists() else None

    def guardar(self, env, servicio, ta):
        archivo = self.certs / f"ta_{servicio}_{env}.json"
        archivo.write_text(json.dumps(ta))
        archivo.chmod(0o600)


class Datos:
    def __init__(self, raiz: Path):
        self.raiz = Path(raiz).expanduser()
        self.certs = self.raiz / "certs"
        self.facturas = self.raiz / "facturas"
        self.borradores = self.facturas / "mcp"

    @classmethod
    def desde_entorno(cls):
        return cls(Path(os.environ.get(VARIABLE_CARPETA) or CARPETA_POR_DEFECTO))

    # --- Configuración ---

    @property
    def env(self) -> dict:
        # Se relee en cada uso: si el usuario edita .env, no hace falta reiniciar el servidor
        return leer_env(self.raiz / ".env")

    @property
    def cuit(self) -> str:
        return self.env.get("AFIP_CUIT", "").replace("-", "").strip()

    def emisor(self) -> dict:
        env = self.env
        return {
            "cuit": self.cuit,
            "razon_social": env.get("AFIP_RAZON_SOCIAL"),
            "domicilio_comercial": env.get("AFIP_DOMICILIO_COMERCIAL"),
            "condicion_iva": env.get("AFIP_CONDICION_IVA"),
            "ingresos_brutos": env.get("AFIP_INGRESOS_BRUTOS"),
            "inicio_actividades": env.get("AFIP_INICIO_ACTIVIDADES"),
        }

    def total_maximo_ars(self) -> Decimal | None:
        """Tope opcional por comprobante, en pesos (FACTURADOR_TOTAL_MAXIMO_ARS en .env)."""
        valor = self.env.get("FACTURADOR_TOTAL_MAXIMO_ARS", "").replace(".", "").replace(",", ".").strip()
        if not valor:
            return None
        try:
            return Decimal(valor)
        except InvalidOperation as e:
            raise ErrorArca(f"FACTURADOR_TOTAL_MAXIMO_ARS no es un número: {valor}") from e

    def perfil(self) -> dict | None:
        archivo = self.raiz / "perfil.json"
        return json.loads(archivo.read_text()) if archivo.exists() else None

    def credencial(self, env) -> Credencial:
        guia = "Llamá a estado_configuracion: dice el siguiente paso del alta."
        if not self.cuit:
            raise ErrorArca(f"El facturador todavía no está configurado (no hay CUIT en {self.raiz / '.env'}). {guia}")
        cfg = ENTORNOS[env]
        try:
            return Credencial(self.cuit, (self.certs / cfg["cert"]).read_bytes(), (self.certs / cfg["key"]).read_bytes())
        except FileNotFoundError as e:
            raise ErrorArca(f"Falta el certificado o la clave de {env} ({e.filename}). {guia}") from e

    def login(self, env, servicio):
        return login(env, servicio, self.credencial(env), TicketsEnArchivo(self.certs), log=log.info)

    def certificado(self, env) -> dict:
        archivo = self.certs / ENTORNOS[env]["cert"]
        if not archivo.exists():
            return {"existe": False}
        try:
            cert = x509.load_pem_x509_certificate(archivo.read_bytes())
        except ValueError:
            return {"existe": True, "valido": False}
        cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        vence = cert.not_valid_after_utc
        return {"existe": True, "valido": True, "alias": cn[0].value if cn else None,
                "vence": vence.date().isoformat(), "dias_para_vencer": (vence - datetime.now(timezone.utc)).days}

    # --- Comprobantes emitidos ---

    def carpeta(self, env) -> Path:
        if env not in ENTORNOS:
            raise ErrorArca(f"Entorno no válido: {env}")
        return self.facturas / env

    def registro(self, env, archivo) -> Path:
        if not NOMBRE_REGISTRO.match(archivo):
            raise ErrorArca(f"Nombre de archivo no válido: {archivo}")
        ruta = self.carpeta(env) / archivo
        if not ruta.exists():
            raise ErrorArca(f"No existe {ruta}")
        return ruta

    def guardar_comprobante(self, salida: dict, nombre: str) -> tuple[Path, Path | None]:
        """Guarda el JSON de la factura aprobada y genera el PDF al lado. Devuelve (json, pdf o None)."""
        destino = self.carpeta(salida["entorno"]) / nombre
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists():
            # Nunca se pisa el registro de una factura: si pasa, algo está muy mal
            raise ErrorArca(f"Ya existe {destino}; no se sobrescribe")
        destino.write_text(json.dumps(salida, indent=2, ensure_ascii=False))
        try:
            return destino, self.generar_pdf(salida, destino)
        except Exception:  # noqa: BLE001 - la factura ya está emitida y guardada; el PDF se regenera después
            log.exception("No se pudo generar el PDF de %s", destino)
            return destino, None

    def generar_pdf(self, salida: dict, registro: Path) -> Path:
        if not salida.get("emisor"):
            # JSON viejos de facturador-afip: solo guardaban el CUIT; el resto sale del .env
            salida["emisor"] = {**self.emisor(), "cuit": salida.get("emisor_cuit") or self.cuit}
        if salida.get("tipo", "E") == "E" and not salida.get("descripciones"):
            # JSON viejos: las tablas son las mismas en homologación
            salida["descripciones"] = descripciones_parametros("homo", self.login("homo", "wsfex"), salida["factura"])
            registro.write_text(json.dumps(salida, indent=2, ensure_ascii=False))
        return pdf.generar_desde(salida, registro.with_name(pdf.nombre_archivo(salida)))

    # --- Borradores ---

    def _ruta_borrador(self, borrador_id) -> Path:
        if not ID_BORRADOR.match(borrador_id or ""):
            raise ErrorArca(f"Id de borrador no válido: {borrador_id}")
        return self.borradores / f"{borrador_id}.json"

    def crear_borrador(self, factura: dict, homologacion: dict) -> dict:
        tipo = str(factura.get("tipo", "E")).upper()
        fecha = factura.get("fecha") or datetime.now().date().isoformat()
        prefijo = f"NC-{tipo}" if factura.get("nota_credito_de") else tipo
        borrador = {
            "id": f"{prefijo}-{fecha}-{uuid.uuid4().hex[:6]}",
            "creado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "estado": "validado",
            "factura": factura,
            "huella": huella(factura),
            "homologacion": homologacion,
            "emision": None,
        }
        self.guardar_borrador(borrador)
        return borrador

    def leer_borrador(self, borrador_id) -> dict:
        ruta = self._ruta_borrador(borrador_id)
        if not ruta.exists():
            raise ErrorArca(f"No existe el borrador {borrador_id}")
        return json.loads(ruta.read_text())

    def guardar_borrador(self, borrador: dict):
        ruta = self._ruta_borrador(borrador["id"])
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_suffix(".tmp")
        temporal.write_text(json.dumps(borrador, indent=2, ensure_ascii=False))
        temporal.replace(ruta)

    def listar_borradores(self) -> list[dict]:
        if not self.borradores.exists():
            return []
        return [json.loads(p.read_text()) for p in sorted(self.borradores.glob("*.json"))]

    def descartar_borrador(self, borrador_id):
        borrador = self.leer_borrador(borrador_id)
        if borrador["estado"] == "emitiendo":
            raise ErrorArca("El borrador quedó a mitad de una emisión: primero verificá con emitir_en_produccion, "
                            "que consulta a ARCA si se emitió")
        self._ruta_borrador(borrador_id).unlink()
        return borrador
