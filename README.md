# facturador-afip

Emite facturas electrónicas en ARCA, ex AFIP:

- **Facturas A, B y C** por el web service WSFE.
- **Factura E** (exportación, para clientes del exterior) por el web service WSFEX.

Genera el PDF con el mismo diseño que "Comprobantes en línea" (la Factura C es idéntica a la de ARCA; la A y la B usan la misma base).

Obtiene el CAE, lleva la numeración y guarda cada factura emitida.

Requiere Python 3 y `openssl`. La emisión no usa dependencias externas; el PDF usa `reportlab` y `qrcode` (ver `requirements.txt`).

## Índice

- [Archivos](#archivos)
- [Configuración](#configuración)
  - [0. Entorno de Python](#0-entorno-de-python)
  - [1. Certificados y alta en ARCA](#1-certificados-y-alta-en-arca)
  - [2. .env](#2-env)
  - [3. Hook de git](#3-hook-de-git)
- [Uso](#uso)
  - [Campos del JSON (Facturas A, B y C)](#campos-del-json-facturas-a-b-y-c)
  - [Notas de crédito](#notas-de-crédito)
  - [Campos del JSON (Factura E)](#campos-del-json-factura-e)
- [Alta en ARCA paso a paso](#alta-en-arca-paso-a-paso)
  - [Requisitos](#requisitos)
  - [Paso 1: generar la clave privada y el pedido de certificado (CSR)](#paso-1-generar-la-clave-privada-y-el-pedido-de-certificado-csr)
  - [Paso 2: homologación (entorno de pruebas)](#paso-2-homologación-entorno-de-pruebas)
  - [Paso 3: producción](#paso-3-producción)
    - [3.1 Obtener el certificado](#31-obtener-el-certificado)
    - [3.2 Autorizar el certificado para el servicio](#32-autorizar-el-certificado-para-el-servicio)
    - [3.3 Dar de alta el punto de venta](#33-dar-de-alta-el-punto-de-venta)
    - [3.4 Lista de control](#34-lista-de-control)
  - [Paso 4: verificar](#paso-4-verificar)
  - [Renovación](#renovación)
- [Skill para Claude Code](#skill-para-claude-code)
- [Seguridad](#seguridad)
- [Notas](#notas)

## Archivos

| Archivo | Qué hace |
|---|---|
| `afip.py` | Login en WSAA (con caché del ticket por servicio) y llamadas SOAP a WSFEX y WSFE |
| `facturar.py` | Emite una factura a partir de un JSON: Factura E, o A, B o C si el JSON tiene `"tipo"` |
| `comun.py` | Lógica de las Facturas A, B y C |
| `probar_conexion.py` | Prueba de solo lectura: estado del servicio, login, puntos de venta y último número |
| `pdf.py` | Genera el PDF de una factura emitida a partir de su JSON |
| `parametros.py` | Busca códigos de país, CUIT genérico por país y monedas |
| `ejemplo_factura.json` | Formato de una Factura E, con un cliente ficticio |
| `ejemplo_factura_comun.json` | Formato de una Factura C, con un cliente ficticio |
| `ejemplo_nota_credito.json` | Formato de una nota de crédito C que anula una Factura C |

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
.venv/bin/python probar_conexion.py homo wsfe     # Facturas A, B y C
.venv/bin/python probar_conexion.py homo          # Factura E (wsfex)
.venv/bin/python probar_conexion.py prod wsfe
.venv/bin/python probar_conexion.py prod
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

### Campos del JSON (Facturas A, B y C)

El JSON lleva `"tipo"`: `"C"` si sos monotributista; `"A"` (a responsables inscriptos) o `"B"` (al resto) si sos responsable inscripto. Ver `ejemplo_factura_comun.json`.

| Campo | Obligatorio | Descripción |
|---|---|---|
| `tipo` | sí | `A`, `B` o `C` |
| `concepto` | no | `1` productos, `2` servicios (por defecto), `3` productos y servicios |
| `receptor.doc_tipo` | no | `CUIT`, `CUIL`, `DNI` o `CF` (consumidor final sin identificar, por defecto). La A exige `CUIT` |
| `receptor.doc_nro` | según `doc_tipo` | Número de documento, sin guiones |
| `receptor.condicion_iva` | no | Condición frente al IVA del receptor: `1` responsable inscripto, `4` exento, `5` consumidor final, `6` monotributo, `7` no categorizado, `8` proveedor del exterior, `9` cliente del exterior, `10` IVA liberado, `13` monotributista social, `15` IVA no alcanzado, `16` monotributo trabajador independiente promovido. Por defecto `1` en la A y `5` en B y C |
| `receptor.nombre`, `receptor.domicilio` | no | Van en el PDF; WSFE no los recibe |
| `condicion_venta` | no | Texto del PDF: `Contado` por defecto, o `Transferencia Bancaria`, `Cuenta Corriente`, etc. |
| `moneda` | no | `PES` por defecto; `DOL`, `060` (euro), etc. |
| `cancela_misma_moneda` | no | Con moneda extranjera: `S` si se cobra en esa misma moneda, `N` (por defecto) si no |
| `cotizacion` | no | Por defecto, la oficial de ARCA del día anterior a `fecha` |
| `fecha` | no | `AAAA-MM-DD`; por defecto, hoy |
| `servicio_desde`, `servicio_hasta` | no | Período facturado (conceptos 2 y 3); por defecto, el mes de `fecha` |
| `fecha_vto_pago` | no | Vencimiento del pago (conceptos 2 y 3); por defecto, `fecha`. No puede ser anterior |
| `punto_venta` | no | Por defecto, el primer punto de venta activo de WSFE |
| `items[]` | sí | `descripcion`, `precio` y, opcionales, `cantidad` y `bonificacion`. En A y B, `precio` es el neto sin IVA y cada ítem lleva `iva` (`21` por defecto; también `0`, `2.5`, `5`, `10.5`, `27`). En C, `precio` es el final |

WSFE no recibe el detalle de ítems, solo los totales: el script calcula el neto, el IVA por alícuota y el total, y guarda los ítems en el JSON. Cada factura aprobada queda en `facturas/<entorno>/<tipo>-PPPPP-NNNNNNNN.json`, con su PDF al lado (`<CUIT>_011_<PPPPP>_<NNNNNNNN>.pdf` para la C).

**Aprobada con observaciones:** ARCA puede aprobar una factura y a la vez avisar que hay que anularla (por ejemplo, si la CUIT del receptor no existe). El script lo muestra destacado; en producción, eso obliga a emitir una nota de crédito. Revisá bien el documento del receptor antes de emitir.

### Notas de crédito

Una factura emitida no se puede borrar: se anula, total o parcialmente, con una nota de crédito de la misma letra (A, B o C). Es el mismo JSON de una factura común con un campo más, `nota_credito_de`, que indica la factura original. Ver `ejemplo_nota_credito.json`.

| Campo | Obligatorio | Descripción |
|---|---|---|
| `nota_credito_de.punto_venta` | sí | Punto de venta de la factura original |
| `nota_credito_de.numero` | sí | Número de la factura original |
| `nota_credito_de.fecha` | sí | Fecha de emisión de la factura original, `AAAA-MM-DD` |

- El resto de los campos son los de la factura: para anularla entera se repiten el receptor, el concepto, el período y los ítems. Para una anulación parcial, los ítems llevan solo el monto que se acredita.
- `fecha` es la de la nota de crédito, no la de la factura; tiene los mismos límites de ARCA.
- ARCA la informa como comprobante asociado a la factura. Los tipos son 3 (A), 8 (B) y 13 (C), con su propia numeración por punto de venta.
- Queda en `facturas/<entorno>/NC-<tipo>-PPPPP-NNNNNNNN.json`, con su PDF al lado (`<CUIT>_013_<PPPPP>_<NNNNNNNN>.pdf` para la C).

### Campos del JSON (Factura E)

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

Los pasos son los mismos para las Facturas A, B y C (servicio `wsfe`) y para la Factura E (servicio `wsfex`); cambian el servicio que se autoriza y el tipo de punto de venta. Si vas a emitir los dos tipos, autorizá los dos servicios.

| | Facturas A, B o C | Factura E (exportación) |
|---|---|---|
| Servicio web | `wsfe` | `wsfex` |
| Nombre en el Administrador de Relaciones (ARCA → WebServices) | Facturación Electrónica | Facturación Electrónica de Exportación |
| Sistema del punto de venta | Factura Electrónica - Monotributo - Web Services (monotributistas) o RECE para aplicativo y web services (responsables inscriptos) | Comprobantes de Exportación - Web Services |

Dos cosas que confunden la primera vez:

- **Lo que se autoriza es un "Computador Fiscal", no una persona.** El certificado representa a tu programa; en ARCA se identifica por el alias que le pusiste. Por eso en las relaciones el representante es el Computador Fiscal con ese alias, nunca tu CUIT.
- **Hay dos ramas parecidas en el buscador de servicios de ARCA: "Servicios interactivos" (los que usás desde la web de ARCA) y "WebServices" (los que usa un programa).** Para autorizar el certificado siempre es **WebServices**. Si elegís el interactivo, ARCA responde "El servicio debe ser delegable".

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
   4. WSASS no da un archivo: muestra el certificado en la pantalla. Copiá el texto, desde `-----BEGIN CERTIFICATE-----` hasta `-----END CERTIFICATE-----`, y guardalo como `certs/afip_homo.crt`. Ojo con no confundirlo con el CSR, que empieza con `-----BEGIN CERTIFICATE REQUEST-----`.
4. En WSASS, elegí "Crear autorización a servicio":
   1. Nombre simbólico del DN: tu alias.
   2. CUIT representada: tu CUIT.
   3. Servicio: `wsfe - Facturación Electrónica` para las Facturas A, B y C. Si también facturás al exterior, creá otra autorización con `wsfex - Facturación Electrónica de Exportación`.
   4. Confirmá con "Crear autorización de acceso".

En homologación no hace falta dar de alta puntos de venta: acepta cualquier número.

### Paso 3: producción

#### 3.1 Obtener el certificado

1. Con clave fiscal, entrá a "Administración de Certificados Digitales". Si no aparece, adherilo como en el paso 2.2, buscando ARCA → Servicios interactivos → "Administración de Certificados Digitales".
2. Elegí tu CUIT y después "Agregar alias".
3. Poné el alias (solo letras y números), subí el archivo `certs/afip_prod.csr` y confirmá con "Agregar alias".
4. En la lista de alias, tocá **"Ver"** en la fila de tu alias.
5. En la pantalla siguiente, tocá **"Descargar"** en el certificado. Baja un archivo `.crt`.
6. Guardalo como `certs/afip_prod.crt`.

El certificado de producción lo emite "Computadores" de AFIP (el de homologación, "Computadores Test"). ARCA usa como CN el alias que escribiste en la pantalla, aunque el CSR tenga otro.

#### 3.2 Autorizar el certificado para el servicio

1. Entrá a "Administrador de Relaciones de Clave Fiscal".
2. Elegí "Nueva Relación".
3. En "Servicio", tocá "Buscar" y elegí ARCA → **WebServices** → "Facturación Electrónica" (Facturas A, B y C). No uses la rama "Servicios interactivos": da el error "El servicio debe ser delegable".
4. En "Representante", tocá "Buscar" (no escribas un CUIT en el campo), marcá **"Computador Fiscal"** y elegí tu alias en el desplegable.
5. Confirmá. Si te lo pide, generá e imprimí el formulario F.3283.

Si aparece "El dador de la autorización no debe ser igual al autorizado", en "Representante" quedó tu propio CUIT como persona: tiene que ser el Computador Fiscal (el certificado).

Si también facturás al exterior, creá otra relación igual en Administrador de Relaciones de Clave Fiscal → Nueva Relación, con ARCA → WebServices → "Facturación Electrónica de Exportación" y el mismo Computador Fiscal. ARCA puede tardar unos minutos en aplicar una relación nueva.

#### 3.3 Dar de alta el punto de venta

Se necesita uno por tipo de factura: uno para las Facturas A, B y C y, si facturás al exterior, otro para la Factura E.

1. Con clave fiscal, entrá a "Administración de puntos de venta y domicilios". Si no aparece, adherilo como en el paso 2.2, buscando ARCA → Servicios interactivos → "Administración de puntos de venta y domicilios".
2. Se abre "PVE - Gestión de puntos de venta". Elegí tu CUIT y, en el menú principal, "A/B/M de Puntos de venta / emisión".
3. Vas a ver el listado con las columnas Número, Nombre de Fantasía, Sistema y Baja. Revisá qué números ya usás y cuáles son de web services (el sistema termina en "Web Services").
4. Tocá "Agregar..." y completá:
   1. **Número**: uno que no uses.
   2. **Nombre de Fantasía**: opcional; es el nombre comercial que aparece en tus facturas.
   3. **Sistema**:
      - Facturas A, B y C, si sos monotributista: "Factura Electrónica - Monotributo - Web Services".
      - Facturas A, B y C, si sos responsable inscripto: "RECE para aplicativo y web services".
      - Factura E: "Comprobantes de Exportación - Web Services".
   4. **Domicilio**: elegí uno de los domicilios que tenés declarados en ARCA.
5. Confirmá. El punto de venta nuevo aparece en el listado.

Tené en cuenta:
- Los sistemas "Factura en Línea" (por ejemplo "Factura en Línea - Monotributo" o "Comprobantes de Exportación - Factura en Línea") son de "Comprobantes en línea" y **no sirven para web services**.
- El sistema de un punto de venta no se puede cambiar: si elegiste mal, dalo de baja con "Baja" y creá otro.
- Cada punto de venta tiene su propia numeración, que empieza en 1.
- El alta puede tardar unos minutos en verse desde el web service.

Con el ejemplo de la tabla de arriba, un monotributista que factura al exterior y en Argentina termina con algo así:

| Número | Sistema | Uso |
|---|---|---|
| 5 | Factura Electrónica - Monotributo - Web Services | Factura C con este proyecto |
| 4 | Comprobantes de Exportación - Web Services | Factura E con este proyecto |

#### 3.4 Lista de control

Para cada servicio que vayas a usar (`wsfe`, `wsfex` o los dos):

- [ ] Certificado de producción descargado en `certs/afip_prod.crt` (paso 3.1).
- [ ] Relación del alias como Computador Fiscal con el servicio (paso 3.2).
- [ ] Punto de venta del sistema "... - Web Services" que corresponde (paso 3.3).
- [ ] `probar_conexion.py prod` (y `prod wsfe`) muestra el punto de venta y el último número (paso 4).

### Paso 4: verificar

```bash
.venv/bin/python probar_conexion.py homo wsfe     # Facturas A, B y C
.venv/bin/python probar_conexion.py homo          # Factura E
.venv/bin/python probar_conexion.py prod wsfe
.venv/bin/python probar_conexion.py prod
```

Son consultas de solo lectura: no emiten nada. Tiene que mostrar el servicio OK (`FEXDummy` o `FEDummy`), el login en WSAA y, en producción, tu punto de venta con `N` (no bloqueado) y el último número emitido (0 si es nuevo). Errores frecuentes:

**Durante el alta en ARCA:**

| Mensaje | Qué pasó y qué hacer |
|---|---|
| El Nombre simbólico del DN sólo puede contener números y/o letras | El alias tiene guiones, espacios o acentos. Usá solo letras y números, y generá el CSR con ese mismo alias. |
| El dador de la autorización no debe ser igual al autorizado | En "Representante" quedó tu CUIT como persona. Tocá "Buscar", marcá "Computador Fiscal" y elegí el alias. |
| El servicio debe ser delegable | Elegiste el servicio en "Servicios interactivos". Buscalo en ARCA → **WebServices**. |
| Pegaste algo que empieza con `BEGIN CERTIFICATE REQUEST` como certificado | Eso es el CSR (el pedido). El certificado lo da ARCA: en homologación, el texto que muestra WSASS; en producción, el archivo que bajás con Ver → Descargar. |

**Al conectarse o emitir:**

| Error | Qué pasó y qué hacer |
|---|---|
| Computador no autorizado a acceder al servicio | Falta la autorización del paso 2.4 (homologación) o la relación del paso 3.2 (producción) para ese servicio: `wsfe` para A, B y C, `wsfex` para la E. |
| El CEE ya posee un TA valido para el acceso al WSN solicitado | ARCA ya entregó un ticket de acceso para ese certificado y servicio, y dura 12 horas; no da otro hasta que venza. Pasa si se borra `certs/ta_*.json` o si otro programa usa el mismo certificado. Esperá a que venza; no borres esos archivos. |
| 1607: Campo Pto_venta no es valido | El punto de venta no es del sistema "Comprobantes de Exportación - Web Services". |
| `Puntos de venta: ninguno` en `prod wsfe` | No hay punto de venta de web services para A, B y C, o todavía no se propagó el alta. |
| 1500 (fecha) | La fecha de emisión está fuera de lo que acepta ARCA: 5 días antes o después de hoy (10 para servicios en A, B y C). |
| 1535 (Factura E) o 10016 (A, B y C): fecha anterior al último comprobante | En cada punto de venta las fechas no pueden retroceder. En homologación suele ser por pruebas viejas: probá con `--pto` y otro número. |
| 1674 / 10036: fecha de pago anterior a la emisión | La fecha de pago (o de vencimiento) tiene que ser igual o posterior a la de emisión. |
| 2053: cotización no válida | La cotización tiene que ser la del día anterior a la fecha del comprobante; el script ya la pide así. |
| Aprobada con la observación 10238 (CUIT receptora inexistente) | ARCA emitió la factura igual. En producción hay que anularla con nota de crédito. Revisá el CUIT del receptor antes de emitir. |
| DH_KEY_TOO_SMALL | El servidor de producción usa una clave Diffie-Hellman de 1024 bits; `afip.py` ya lo resuelve. |

### Renovación

Los certificados vencen a los 2 años (la fecha está en el certificado: `openssl x509 -in certs/afip_prod.crt -noout -enddate`). Para renovarlos alcanza con pedir un certificado nuevo para **el mismo alias y la misma clave**: las autorizaciones y relaciones son del alias, así que siguen valiendo.

- **Homologación:** en WSASS → "Nuevo Certificado", poné el mismo alias como nombre simbólico del DN, pegá el mismo CSR (`certs/afip.csr`) y guardá el certificado que muestra como `certs/afip_homo.crt`.
- **Producción:** en "Administración de Certificados Digitales", elegí tu CUIT, tocá "Ver" en la fila del alias y después "Agregar certificado"; subí el mismo `certs/afip_prod.csr`. En la pantalla del alias vas a ver dos certificados: tocá "Descargar" en el nuevo (el de vencimiento más lejano) y reemplazá `certs/afip_prod.crt`.

Si creés que la clave privada quedó expuesta, no renueves: generá una clave nueva con otro alias y hacé todo el alta de nuevo (certificado, autorizaciones y relaciones).

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

La skill también sabe guiarte en el alta en ARCA: si te perdés en algún paso o aparece un error, contale en qué pantalla estás o pegale el mensaje, y te dice qué hacer.

## Seguridad

- `certs/`, `facturas/`, `.env`, `perfil.json` y cualquier `*.json` salvo los ejemplos están en `.gitignore`.
- `.githooks/pre-commit` cancela el commit si incluye esos archivos, una clave, un certificado o el CUIT configurado en `.env`, aunque se fuerce el `git add`.
- Los tickets de WSAA (`certs/ta_*.json`) se guardan con permisos 600 y duran 12 horas.

## Notas

- El servidor de producción de WSFEX negocia una clave Diffie-Hellman de 1024 bits, que OpenSSL 3 rechaza. `afip.py` baja el nivel de seguridad de TLS a `SECLEVEL=1` solo para estas conexiones y sigue verificando el certificado del servidor.
- ARCA acepta como fecha de emisión desde 5 días antes hasta 5 días después de hoy en la Factura E (error 1500). En las A, B y C, 5 días para productos y 10 para servicios.
- En cada punto de venta las fechas no pueden retroceder: una factura no puede tener fecha anterior a la última emitida.
- Homologación no tiene puntos de venta dados de alta; el script usa el 1.
