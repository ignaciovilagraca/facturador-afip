# facturador-afip

Emite facturas de exportación (Factura E) en ARCA, ex AFIP, usando el web service WSFEX. Obtiene el CAE, lleva la numeración, guarda cada factura emitida y genera su PDF con el mismo diseño que "Comprobantes en línea".

Requiere Python 3 y `openssl`. La emisión no usa dependencias externas; el PDF usa `reportlab` y `qrcode` (ver `requirements.txt`).

## Archivos

| Archivo | Qué hace |
|---|---|
| `afip.py` | Login en WSAA (con caché del ticket) y llamadas SOAP a WSFEX |
| `facturar.py` | Emite una Factura E a partir de un JSON |
| `probar_conexion.py` | Prueba de solo lectura: estado del servicio, login, puntos de venta y último número |
| `pdf.py` | Genera el PDF de una factura emitida a partir de su JSON |
| `parametros.py` | Busca códigos de país, CUIT genérico por país y monedas |
| `ejemplo_factura.json` | Formato de una factura, con un cliente ficticio |

## Configuración

### 0. Entorno de Python

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 1. Certificados y alta en ARCA

El proyecto usa un par de clave y certificado por entorno, dentro de `certs/`:

| Entorno | Clave | Certificado |
|---|---|---|
| Homologación (pruebas) | `certs/afip.key` | `certs/afip_homo.crt` |
| Producción | `certs/afip_prod.key` | `certs/afip_prod.crt` |

El paso a paso completo está en [Alta en ARCA paso a paso](#alta-en-arca-paso-a-paso).

### 2. `.env`

```bash
cp .env.example .env
```

Completar `AFIP_CUIT` con el CUIT del emisor, sin guiones. Los demás campos (razón social, domicilio comercial, condición frente al IVA, Ingresos Brutos, inicio de actividades) son opcionales: se guardan en el JSON de cada factura emitida para poder generar el PDF más adelante.

### 3. Hook de git

```bash
git config core.hooksPath .githooks
```

Activa un control antes de cada commit que lo cancela si incluye certificados, claves, facturas o el CUIT (ver [Seguridad](#seguridad)).

## Uso

Primero conviene probar la conexión:

```bash
python3 probar_conexion.py homo
python3 probar_conexion.py prod
```

Para emitir una factura, se copia el ejemplo a `facturas/borradores/`, se completa y se envía:

```bash
.venv/bin/python facturar.py facturas/borradores/cliente-2026-09.json          # homologación
.venv/bin/python facturar.py facturas/borradores/cliente-2026-09.json --prod   # producción
```

Con `--pto N` se usa otro punto de venta. Sirve en homologación cuando pruebas anteriores dejaron una fecha posterior en el punto de venta 1.

En producción, el script muestra un resumen y pide escribir `si` antes de emitir. Cada factura aprobada se guarda en `facturas/<entorno>/E-PPPPP-NNNNNNNN.json` con el CAE, la cotización, las fechas, los datos del emisor y el JSON original.

Al lado del JSON se genera el PDF, con el nombre que usa ARCA (`<CUIT>_019_<PPPPP>_<NNNNNNNN>.pdf`): solo el ORIGINAL, con el QR según la especificación de ARCA. Para regenerarlo:

```bash
.venv/bin/python pdf.py facturas/prod/E-00004-00000001.json
```

### Campos del JSON

| Campo | Obligatorio | Descripción |
|---|---|---|
| `tipo_expo` | no | `2` servicios (por defecto), `1` bienes, `4` otros |
| `pais_destino` | sí | Código de país (`parametros.py paises <texto>`) |
| `cliente.nombre` | sí | Nombre o razón social |
| `cliente.cuit_pais` | uno de los dos | CUIT genérico del país (`parametros.py cuit-pais <texto>`) |
| `cliente.id_impositivo` | uno de los dos | ID fiscal del cliente en su país |
| `cliente.domicilio` | sí | Domicilio del cliente |
| `moneda` | no | `DOL` por defecto; `PES`, `060` (euro), etc. |
| `cotizacion` | no | Por defecto, la oficial de ARCA del día anterior a `fecha` (o la última publicada) |
| `idioma` | no | `1` español (por defecto), `2` inglés, `3` portugués |
| `fecha` | no | `AAAA-MM-DD`; por defecto, hoy |
| `fecha_pago` | no | `AAAA-MM-DD`; por defecto, hoy + 30 días (solo servicios y otros) |
| `forma_pago`, `obs`, `obs_comerciales` | no | Texto libre |
| `punto_venta` | no | Por defecto, el primer punto de venta activo de WSFEX |
| `items[]` | sí | `descripcion`, `precio` y, opcionales, `cantidad`, `unidad` (7 = unidades), `codigo`, `bonificacion` |

El total se calcula sumando los ítems.

## Alta en ARCA paso a paso

Para usar los web services de facturación hay que: generar una clave y un pedido de certificado, obtener el certificado en ARCA, autorizarlo para el servicio y, en producción, dar de alta un punto de venta para web services. Se hace una vez por entorno.

**Este proyecto emite solo Factura E (exportación, servicio `wsfex`).** Los pasos para facturas comunes (A, B y C, servicio `wsfe`) están para quien quiera habilitarlas, pero el código para emitirlas no está incluido.

| | Exportación (Factura E) | Comunes (Factura A, B o C) |
|---|---|---|
| Servicio web | `wsfex` | `wsfe` |
| Nombre en el Administrador de Relaciones | Facturación Electrónica de Exportación | Facturación Electrónica |
| Sistema del punto de venta | Comprobantes de Exportación - Web Services | Factura Electrónica - Monotributo - Web Services (monotributistas) o RECE para aplicativo y web services (responsables inscriptos) |

ARCA cambia seguido los nombres y la ubicación de los menús. Si alguno no coincide exactamente, buscalo por palabras clave ("certificados", "relaciones", "puntos de venta").

### Requisitos

- Clave fiscal nivel 3 o superior.
- `openssl` instalado (viene con macOS y la mayoría de las distribuciones de Linux).

### Paso 1: generar la clave privada y el pedido de certificado (CSR)

Se hace en tu computadora. La clave privada no se sube nunca a ningún lado. Conviene una clave por entorno:

```bash
mkdir -p certs
openssl genrsa -out certs/afip.key 2048        # homologación
openssl genrsa -out certs/afip_prod.key 2048   # producción
```

Un CSR por clave. Reemplazá `NOMBRE`, `ALIAS` y el CUIT:

```bash
openssl req -new -key certs/afip.key \
  -subj "/C=AR/O=NOMBRE/CN=ALIAS/serialNumber=CUIT 20XXXXXXXXX" \
  -out certs/afip.csr
openssl req -new -key certs/afip_prod.key \
  -subj "/C=AR/O=NOMBRE/CN=ALIAS/serialNumber=CUIT 20XXXXXXXXX" \
  -out certs/afip_prod.csr
```

- `NOMBRE` es tu nombre o razón social.
- `ALIAS` es un nombre para el certificado, **solo letras y números** (sin guiones ni espacios); si no, ARCA lo rechaza con "El Nombre simbólico del DN sólo puede contener números y/o letras". Ejemplo: `facturador1a2b3c`.
- El CUIT va sin guiones, precedido de `CUIT ` con un espacio.

### Paso 2: homologación (entorno de pruebas)

1. Entrá a [arca.gob.ar](https://www.arca.gob.ar) con clave fiscal.
2. Si no tenés el servicio "WSASS - Autogestión Certificados Homologación", adherilo:
   1. Entrá a "Administrador de Relaciones de Clave Fiscal".
   2. Elegí "Adherir servicio".
   3. Buscá ARCA → Servicios interactivos → "WSASS - Autogestión Certificados Homologación" y confirmá.
   4. Cerrá sesión y volvé a entrar para que aparezca.
3. En WSASS, elegí "Nuevo Certificado":
   1. En "Nombre simbólico del DN" poné el alias (el mismo `ALIAS` del CSR).
   2. En "Solicitud de certificado en formato PKCS#10" pegá el contenido completo de `certs/afip.csr`, incluidas las líneas `-----BEGIN CERTIFICATE REQUEST-----` y `-----END CERTIFICATE REQUEST-----`.
   3. Elegí "Crear DN y obtener certificado".
   4. Copiá el certificado que aparece, desde `-----BEGIN CERTIFICATE-----` hasta `-----END CERTIFICATE-----`, en `certs/afip_homo.crt`.
4. En WSASS, elegí "Crear autorización a servicio":
   1. Nombre simbólico del DN: tu alias.
   2. CUIT representada: tu CUIT.
   3. Servicio: `wsfex - Facturación Electrónica de Exportación` para Factura E, o `wsfe - Facturación Electrónica` para facturas comunes. Si vas a usar los dos, creá una autorización para cada uno.
   4. Confirmá con "Crear autorización de acceso".

En homologación no hace falta dar de alta puntos de venta: acepta cualquier número.

### Paso 3: producción

#### 3.1 Obtener el certificado

1. Con clave fiscal, entrá a "Administración de Certificados Digitales". Si no aparece, adherilo como en el paso 2.2, buscando ARCA → Servicios interactivos → "Administración de Certificados Digitales".
2. Elegí tu CUIT y después "Agregar alias".
3. Poné el alias (solo letras y números), subí `certs/afip_prod.csr` y confirmá con "Agregar alias".
4. Entrá al alias con "Ver", y en la lista de certificados descargá el certificado (`.crt`).
5. Guardalo como `certs/afip_prod.crt`.

El certificado de producción lo emite "Computadores" de AFIP (el de homologación, "Computadores Test"). ARCA usa como CN el alias que escribiste en la pantalla, aunque el CSR tenga otro.

#### 3.2 Autorizar el certificado para el servicio

1. Entrá a "Administrador de Relaciones de Clave Fiscal".
2. Elegí "Nueva Relación".
3. En "Servicio", tocá "Buscar" y elegí ARCA → WebServices → "Facturación Electrónica de Exportación" (Factura E) o "Facturación Electrónica" (comunes).
4. En "Representante", tocá "Buscar" (no escribas un CUIT en el campo), marcá **"Computador Fiscal"** y elegí tu alias en el desplegable.
5. Confirmá. Si te lo pide, generá e imprimí el formulario F.3283.

Si aparece "El dador de la autorización no debe ser igual al autorizado", en "Representante" quedó tu propio CUIT como persona: tiene que ser el Computador Fiscal (el certificado).

Para usar exportación y comunes, repetí la relación para cada servicio con el mismo alias.

#### 3.3 Dar de alta el punto de venta

1. Entrá a "Administración de puntos de venta y domicilios".
2. Elegí tu CUIT y después "A/B/M de Puntos de Venta" → "Agregar".
3. Poné un número que no uses.
4. En "Sistema" elegí:
   - Factura E: "Comprobantes de Exportación - Web Services".
   - Comunes, si sos monotributista: "Factura Electrónica - Monotributo - Web Services".
   - Comunes, si sos responsable inscripto: "RECE para aplicativo y web services".
5. Elegí el domicilio y confirmá.

Los puntos de venta de "Comprobantes en línea" no sirven para web services, y el sistema de un punto de venta existente no se puede cambiar: hay que crear uno nuevo. Cada punto de venta tiene su propia numeración. El alta puede tardar unos minutos en verse desde el web service.

### Paso 4: verificar

```bash
.venv/bin/python probar_conexion.py homo
.venv/bin/python probar_conexion.py prod
```

Tiene que mostrar `FEXDummy` OK, el login en WSAA y, en producción, tu punto de venta de exportación con `N` (no bloqueado). Errores comunes:

| Error | Causa |
|---|---|
| Computador no autorizado a acceder al servicio | Falta la autorización del paso 2.4 (homologación) o la relación del paso 3.2 (producción), o se hizo para `wsfe` en lugar de `wsfex`. |
| 1607: Campo Pto_venta no es valido | El punto de venta no es del sistema "Comprobantes de Exportación - Web Services". |
| DH_KEY_TOO_SMALL | El servidor de producción usa una clave Diffie-Hellman de 1024 bits; `afip.py` ya lo resuelve. |

### Renovación

Los certificados vencen a los 2 años. Para renovarlos, generá un CSR nuevo (puede ser con la misma clave) y repetí el paso 2.3 o 3.1 con el mismo alias. No hace falta repetir la autorización ni la relación.

## Skill para Claude Code

`.claude/skills/facturar/` tiene una skill para que Claude Code prepare las facturas: arma los datos (también a partir del PDF de un invoice), te los hace confirmar, valida en homologación y te da el comando de producción. Nunca emite en producción por su cuenta: ese comando lo corrés vos. Si configurás una carpeta de Drive, después sube el PDF.

1. Copiá el perfil de ejemplo y completalo con tus datos, tu cliente habitual y cómo querés las fechas:

   ```bash
   cp perfil.example.json perfil.json
   ```

   `perfil.json` no se sube al repo.

2. La skill se carga sola cuando abrís Claude Code dentro de esta carpeta. Para usarla desde cualquier carpeta, enlazala en tus skills personales:

   ```bash
   ln -s "$PWD/.claude/skills/facturar" ~/.claude/skills/facturar
   ```

3. Pedile a Claude algo como "haceme la factura de septiembre" o pasale el PDF del invoice.

## Seguridad

- `certs/`, `facturas/`, `.env`, `perfil.json` y cualquier `*.json` salvo los ejemplos están en `.gitignore`.
- `.githooks/pre-commit` cancela el commit si incluye esos archivos, una clave, un certificado o el CUIT configurado en `.env`, aunque se fuerce el `git add`.
- Los tickets de WSAA (`certs/ta_*.json`) se guardan con permisos 600 y duran 12 horas.

## Notas

- El servidor de producción de WSFEX negocia una clave Diffie-Hellman de 1024 bits, que OpenSSL 3 rechaza. `afip.py` baja el nivel de seguridad de TLS a `SECLEVEL=1` solo para estas conexiones y sigue verificando el certificado del servidor.
- ARCA acepta como fecha de emisión desde 5 días antes hasta 5 días después de hoy (error 1500).
- En cada punto de venta las fechas no pueden retroceder: una factura no puede tener fecha anterior a la última emitida.
- Homologación no tiene puntos de venta dados de alta; el script usa el 1.
