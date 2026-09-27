---
name: facturar
description: Prepara facturas electrónicas de ARCA/AFIP con el proyecto facturador-afip (Factura E de exportación por WSFEX y facturas comunes A, B y C por WSFE) y las valida en homologación; la emisión en producción la hace el usuario y solo con su aprobación expresa. Después sube el PDF a Drive si está configurado. Usala siempre que pida crear, hacer, emitir o preparar una factura, facturarle a un cliente, cobrar un trabajo al exterior, sacar un CAE, repetir la factura del mes, o cuando pase un invoice (PDF o imagen) para crear "la equivalente" en ARCA, aunque no diga "Factura E", "ARCA" ni "AFIP".
---

# Facturar (Factura E y facturas comunes)

El usuario factura con este proyecto: al exterior con Factura E (tipo 19, WSFEX) y en Argentina con facturas comunes A, B o C (WSFE). Esta guía describe la Factura E; las diferencias de las comunes están en [Facturas comunes](#facturas-comunes-a-b-y-c). Todo el trabajo pasa por `facturar.py`, que resuelve login, numeración, cotización, envío a ARCA y el PDF. Tu trabajo es armar bien los datos, que el usuario los confirme, validarlos en homologación y dejarle el comando de producción.

## Antes de empezar: proyecto y perfil

**Carpeta del proyecto.** Esta skill vive en `<proyecto>/.claude/skills/facturar/SKILL.md`. Si está instalada con un enlace en `~/.claude/skills/facturar`, la carpeta real sale de:

```bash
dirname "$(dirname "$(dirname "$(dirname "$(readlink -f ~/.claude/skills/facturar/SKILL.md)")")")"
```

Todos los comandos de abajo se corren desde esa carpeta (`cd <proyecto> && ...`).

**Perfil.** Leé `<proyecto>/perfil.json`. Tiene los datos de cada persona, que no se suben al repo:

| Campo | Para qué |
|---|---|
| `nombre` | Cómo dirigirte al usuario |
| `punto_venta_prod` | Punto de venta de producción para Factura E ("Comprobantes de Exportación - Web Services") |
| `punto_venta_prod_comunes` | Punto de venta de producción para facturas comunes; `null` si no tiene |
| `condicion_iva_emisor` | `monotributo` o `responsable_inscripto`: define si las comunes son C, o A/B |
| `alias_certificado`, `vencimiento_certificado` | Para diagnosticar errores de autorización y avisar del vencimiento |
| `drive_folder_id` | Carpeta de Drive donde subir los PDF. Vacío: no se sube nada |
| `formato` | `un_solo_item`, `descripcion`, `idioma` y `forma_pago` por defecto |
| `fechas` | Reglas para `fecha` y `fecha_pago` (ver paso 1) |
| `cliente_por_defecto` | Cliente cuando el usuario no nombra otro; `notas` tiene aclaraciones que hay que respetar |

Si `perfil.json` no existe, decile que copie `perfil.example.json` a `perfil.json` y lo complete, y ayudalo a hacerlo. Sin perfil no sigas. Si falta `.env`, los certificados en `certs/` o el entorno `.venv`, mandalo a la sección Configuración del README.

Si faltan menos de 60 días para `vencimiento_certificado`, avisale al principio.

## Regla principal: nunca se emite en producción sin aprobación expresa del usuario

Una factura emitida en producción es un comprobante fiscal real ante ARCA. No se puede borrar: solo se anula con una nota de crédito, que también queda registrada. Por eso:

- **Nunca corras `facturar.py --prod`.** Ni con aprobación, ni aunque el usuario te lo pida, ni con otra herramienta, script o entorno. El comando de producción lo ejecuta siempre el usuario en su terminal, donde el script le muestra el resumen y le pide escribir "si".
- **Confirmar los datos no es aprobar la emisión.** Cuando el usuario dice que los datos están OK, eso te habilita solo a escribir el borrador y validarlo en homologación. Para pasarle el comando de producción tiene que aprobar expresamente la emisión de esa factura en particular, por ejemplo "emitila" o "pasame el de prod". Un "ok", "dale" o "sí" en respuesta a la confirmación de datos no alcanza. Si hay cualquier duda, preguntá: "¿La emitís en producción?".
- **Cada factura necesita su propia aprobación.** Aprobar una no aprueba las siguientes, ni la misma factura si después cambió algún dato.
- **Homologación sí la podés correr vos**, después de que el usuario confirme los datos. No tiene valor fiscal. Cuando le cuentes el resultado, aclarale siempre que fue en homologación y que no se emitió nada real.
- **No dejes nada listo para emitir sin aprobación.** Si el usuario no aprueba la emisión o dice que no emitas, borrá el borrador de `facturas/borradores/`. Así no queda un archivo que se pueda correr con `--prod` por error.

## Por qué el resto del flujo es así

- **El usuario confirma los datos antes de que corras nada.** Un error en el monto, el mes o el cliente se paga caro, así que primero le mostrás lo que armaste y esperás su OK, aunque todo parezca obvio.
- **Los datos no se commitean.** Los JSON de facturas y el perfil tienen datos personales y de clientes. `.gitignore` ignora todo `*.json` salvo los ejemplos, y todo `facturas/`, y un hook pre-commit bloquea el CUIT, las claves y los certificados. Guardá los borradores en `facturas/borradores/`, nunca en la raíz ni en otra carpeta versionada, y no fuerces `git add` sobre esos archivos.

## Pasos

### 1. Juntar los datos

Primero definí el tipo: si el cliente está en el exterior es Factura E (seguí esta guía); si está en Argentina es una factura común (seguí además [Facturas comunes](#facturas-comunes-a-b-y-c)). Si no está claro, preguntá.

Los datos pueden venir de tres lugares. Usá el que corresponda.

#### a) El usuario pasa un invoice (PDF o imagen)

Suele ser el invoice que le genera su cliente o la plataforma que le paga. Leelo y armá la factura equivalente:

| En el invoice | En el JSON |
|---|---|
| Seller / Tax ID | No va al JSON. Verificá que el Tax ID sea el CUIT del usuario (`AFIP_CUIT` en `.env`); si no coincide, avisale. |
| Buyer (nombre, dirección, Tax ID) | `cliente.nombre`, `cliente.domicilio`, `cliente.id_impositivo`. Si es el cliente por defecto del perfil, usá los datos del perfil y avisá si el invoice trae otros. |
| Líneas de ITEM / AMOUNT | Si `formato.un_solo_item` es `true`, se suman en un único ítem con `formato.descripcion`. Si no, cada línea es un ítem con su descripción. |
| Moneda de los montos | `moneda`: USD → `DOL`, EUR → `060` |
| Issue date, payment date | Sirven para saber el mes trabajado. Las fechas de la Factura E salen de las reglas de `fechas`, no del invoice. |

Controlá que la suma de las líneas dé el TOTAL del invoice. Si no coincide, o si el invoice tiene impuestos distintos de 0, frená y preguntá.

#### b) El usuario no nombra cliente

"Haceme la factura del mes", "facturá septiembre": la factura es para `cliente_por_defecto`. Pedile el monto si no lo dio.

#### c) Otro cliente

Buscá facturas anteriores al mismo cliente en `facturas/prod/*.json` y `facturas/borradores/*.json` (el campo `factura` de las emitidas tiene el JSON original) y reutilizá sus datos. Si es nuevo, pedí lo que falte de la tabla de campos.

#### Formato

Tomalo de `perfil.formato`, salvo que el usuario pida otra cosa para esta factura:
- Con `un_solo_item`, la factura lleva una sola línea: `descripcion` del perfil, `cantidad` 1, `unidad` 7 (unidades) y `precio` igual al total. No se le agrega el mes a la descripción.
- `idioma` y `forma_pago` del perfil.
- Sin `obs`, salvo que el usuario lo pida.
- Respetá las `notas` del cliente por defecto.

#### Fechas

Según `perfil.fechas`:

- `emision`:
  - `ultimo_dia_mes_trabajado`: el último día del mes trabajado. El mes trabajado es el que el usuario nombre o el que surge del invoice. Si no hay ninguna de las dos referencias, es el mes en curso cuando se factura en los últimos días del mes, y el mes anterior cuando se factura en los primeros días del siguiente.
  - `hoy`: la fecha de hoy.
- `pago`:
  - `primer_dia_habil_mes_siguiente`: el primer día hábil del mes siguiente al de `fecha`. Saltá sábados y domingos, y también el 1 de enero y el 1 de mayo, que son los feriados nacionales que caen en día 1. Ejemplos: agosto 2026 → 2026-09-01 (martes); octubre 2026 → 2026-11-02 (el 1 es domingo); diciembre 2026 → 2027-01-04 (el 1 es feriado y el 2 y 3 son fin de semana).
  - `igual_emision`: la misma que `fecha`.

**Límites de ARCA:** `fecha` tiene que estar entre 5 días antes y 5 días después de hoy (error 1500). Además, en cada punto de venta las fechas no pueden retroceder: una factura no puede tener fecha anterior a la última emitida. Si la regla da una fecha fuera de ese rango (típico cuando se factura un mes ya pasado), no la cambies por tu cuenta: en la confirmación decile al usuario qué fecha pide la regla, cuál es el rango válido hoy, y proponé la fecha permitida más cercana. `fecha_pago` no tiene ese límite, pero no puede ser anterior a `fecha` (error 1674); si con la fecha nueva queda antes, proponé también moverla.

#### Tabla de campos

| Dato | Campo | Por defecto |
|---|---|---|
| Nombre o razón social del cliente | `cliente.nombre` | obligatorio |
| País del cliente | `pais_destino` | obligatorio |
| Persona física o jurídica | define `cliente.cuit_pais` | jurídica si es una empresa |
| ID fiscal del cliente (EIN, VAT, etc.) | `cliente.id_impositivo` | opcional si hay `cuit_pais` |
| Domicilio del cliente | `cliente.domicilio` | obligatorio, ARCA lo exige |
| Descripción | `items[].descripcion` | `perfil.formato.descripcion` |
| Monto | `items[].precio` | obligatorio |
| Moneda | `moneda` | `DOL` |
| Idioma | `idioma` | `perfil.formato.idioma` |
| Fecha | `fecha` | según `perfil.fechas.emision` |
| Fecha de pago | `fecha_pago` | según `perfil.fechas.pago` |
| Forma de pago | `forma_pago` | `perfil.formato.forma_pago` |

Códigos de país y CUIT genérico del país del cliente:

```bash
cd <proyecto> && .venv/bin/python parametros.py paises "estados unidos"
cd <proyecto> && .venv/bin/python parametros.py cuit-pais "estados unidos"
```

Los más comunes:
- Estados Unidos: país 212; CUIT país 55000002126 si es empresa, 50000002124 si es persona física.
- Reino Unido: país 426; CUIT país 55000004269 si es empresa, 50000004267 si es persona física.
- España: país 410. Para otros países, usá `parametros.py`.

### 2. Confirmar con el usuario

Antes de escribir el borrador o correr cualquier cosa, mostrale los datos y preguntale si están OK. Usá este formato:

```
Factura E para confirmar

Cliente:      Acme Inc (ID Impositivo 00-0000000)
              123 Main St, New York, NY 10001, USA
Ítem:         Consultoría de software   1 x USD 6,031.45
Total:        USD 6,031.45
Fecha:        2026-08-31
Vencimiento:  2026-09-01
Forma pago:   Transferencia bancaria - Moneda Extranjera
```

Si el total sale de sumar varias líneas de un invoice, mostrá la suma debajo, por ejemplo: "5,917.45 (Software Engineer) + 114.00 (Work from home)".

Debajo, en una línea cada uno, agregá solo si aplica: una fecha fuera del rango de ARCA con tu propuesta, un total que no coincide con el invoice, o un dato que tuviste que suponer.

Cerrá con una pregunta que deje claro qué se aprueba: "¿Están OK los datos? Si me confirmás, la valido en homologación (sin valor fiscal); producción la emitís vos después." Esperá su respuesta. Si corrige algo, volvé a mostrar la versión completa corregida antes de seguir.

### 3. Escribir el borrador

Con el OK, guardalo en `<proyecto>/facturas/borradores/<alias-cliente>-<AAAA-MM>.json`, con el formato de `ejemplo_factura.json`:

```json
{
  "tipo_expo": 2,
  "pais_destino": 212,
  "cliente": { "...": "datos del cliente" },
  "moneda": "DOL",
  "idioma": 1,
  "forma_pago": "Transferencia bancaria - Moneda Extranjera",
  "fecha": "2026-08-31",
  "fecha_pago": "2026-09-01",
  "items": [
    {"codigo": "SERV", "descripcion": "Consultoría de software", "cantidad": 1, "unidad": 7, "precio": 6031.45}
  ]
}
```

- `tipo_expo` es 2 (servicios) salvo que el usuario diga que exporta bienes (1) u "otros" (4). Con bienes, el script también necesita `permiso_existente` e `incoterms`.
- Poné siempre `fecha` y `fecha_pago` explícitas. Si faltan, el script usa hoy y hoy + 30 días. `cotizacion` va solo si el usuario pide una distinta de la oficial.
- No pongas `punto_venta` en el JSON: en producción el script usa el primer punto de venta activo de web services, que debería ser `perfil.punto_venta_prod`.

### 4. Validar en homologación

```bash
cd <proyecto> && .venv/bin/python facturar.py facturas/borradores/<archivo>.json
```

Si responde APROBADA, el JSON está bien. El CAE de homologación no tiene valor fiscal. Si responde RECHAZADA, leé el error:
- **Fecha anterior a la última del punto de venta:** es un efecto de pruebas anteriores en homologación, no un problema real. Repetí con otro punto de venta, por ejemplo `--pto 3` o uno más alto; en homologación cualquier número sirve. No uses `--pto` en producción.
- **Error 1500 (fecha fuera de rango):** volvé al paso 2 con la propuesta de fecha.
- **Error 1674 (fecha de pago anterior a la emisión):** volvé al paso 2 y proponé una `fecha_pago` igual o posterior a `fecha`.
- **Error 2053 (cotización no válida):** el script ya pide la cotización del día anterior a `fecha`. Si igual aparece, el mensaje trae la cotización que ARCA espera; ponela en `cotizacion` y avisale al usuario.
- **Falta de datos:** falta el domicilio, faltan a la vez `cuit_pais` e `id_impositivo`, o el código de país es inválido. Corregilo y, si cambia algo visible para el usuario, confirmalo de nuevo.

Las cotizaciones de homologación son de prueba y no se parecen a las reales; no las uses como referencia.

### 5. Pedir la aprobación de producción

Decile que homologación la aprobó, recordale que todavía no se emitió nada real y preguntale si la emite en producción. Esperá su respuesta.

Solo si aprueba expresamente la emisión (ver la regla principal), dale el comando en su propio bloque para que lo corra él:

```bash
cd <proyecto> && .venv/bin/python facturar.py facturas/borradores/<archivo>.json --prod
```

No lo corras vos. Si te pide que lo corras, explicale que la regla es que producción la ejecuta él, y no le busques la vuelta. Si no aprueba, borrá el borrador.

### 6. Después de que la emita

Cuando el usuario diga que la emitió o pegue la salida, leé el `facturas/prod/E-PPPPP-NNNNNNNN.json` más nuevo y confirmale el número, el CAE y el vencimiento del CAE.

El script genera el PDF al lado del JSON, con el mismo diseño que "Comprobantes en línea" de ARCA (solo ORIGINAL) y el nombre que usa ARCA: `facturas/prod/<CUIT>_019_<PPPPP>_<NNNNNNNN>.pdf`. Si la salida dice que no se pudo generar, generalo a partir del JSON (esto no toca ARCA producción, solo lee el archivo local):

```bash
cd <proyecto> && .venv/bin/python pdf.py facturas/prod/E-PPPPP-NNNNNNNN.json
```

El JSON es el registro de la factura: no lo borres ni lo modifiques.

### 7. Subir el PDF a Drive

Solo si `perfil.drive_folder_id` no está vacío. Usá el conector de Google Drive; si sus herramientas aparecen como diferidas, cargalas antes con ToolSearch (`create_file`, `search_files`). Si no hay conector de Drive, decile al usuario dónde quedó el PDF y que lo suba él.

1. Buscá si ya está subido, para no duplicarlo: `search_files` con `parentId = '<drive_folder_id>' and title = '<nombre del PDF>'`. Si ya existe, no lo subas de nuevo y avisale.
2. Obtené el contenido en base64: `base64 -i facturas/prod/<nombre>.pdf | tr -d '\n'`.
3. `create_file` con `title` igual al nombre del PDF, `parentId` igual a `drive_folder_id`, `contentMimeType` `application/pdf`, `disableConversionToGoogleType` `true` y `base64Content` con lo del paso 2.
4. Confirmale el número de factura, el CAE y el link del archivo en Drive.

Subí solo PDFs de producción. Los de homologación no son facturas reales y nunca van a esa carpeta.

## Facturas comunes (A, B y C)

Mismo flujo y misma regla de producción que la Factura E, con estas diferencias:

**Tipo.** Según `perfil.condicion_iva_emisor`: `monotributo` emite siempre **C**; `responsable_inscripto` emite **A** a responsables inscriptos y **B** al resto (consumidores finales, monotributistas, exentos).

**Datos (paso 1).** El JSON sigue `ejemplo_factura_comun.json`; los campos están en la sección "Facturas comunes" del README. Juntá:
- Receptor: nombre, documento (`CUIT`, `CUIL`, `DNI`, o `CF` para consumidor final sin identificar) y condición frente al IVA (`1` RI, `4` exento, `5` consumidor final, `6` monotributo; la lista completa está en el README). La A exige CUIT.
- `concepto`: `2` servicios salvo que venda productos (`1`) o ambos (`3`).
- Con servicios: `servicio_desde`, `servicio_hasta` (por defecto el mes de `fecha`) y `fecha_vto_pago` (no puede ser anterior a `fecha`).
- Ítems: en C, `precio` es el final. En A y B, `precio` es el neto sin IVA y cada ítem lleva `iva` (21 por defecto). Si el usuario te da un monto "con IVA incluido", calculá el neto y mostralo en la confirmación.
- `receptor.nombre` y `receptor.domicilio` van en el PDF aunque ARCA no los reciba; pedilos. `condicion_venta` es `Contado` salvo que diga otra cosa.
- `moneda` es `PES` salvo que diga otra cosa; con moneda extranjera preguntá si se cobra en esa misma moneda (`cancela_misma_moneda` `S` o `N`).
- `perfil.formato` y `perfil.fechas` son para la Factura E; en las comunes usalos solo si el usuario lo pide.
- ARCA acepta `fecha` hasta 5 días antes o después de hoy con productos, y hasta 10 con servicios.

**Confirmación (paso 2).** Mostrá tipo (A, B o C), receptor con documento y condición frente al IVA, concepto, período, ítems, y en A y B el neto, el IVA por alícuota y el total.

**Documento del receptor.** Revisalo con cuidado. ARCA puede **aprobar** una factura con una CUIT inexistente y a la vez observar que hay que anularla con nota de crédito. Si en homologación la factura sale aprobada con observaciones, tratalo como un error: mostrale las observaciones al usuario y no sigas a producción hasta resolverlas.

**Homologación (paso 4).** Mismo comando; `facturar.py` detecta el `tipo`. La prueba de conexión es `.venv/bin/python probar_conexion.py homo wsfe`. Si el login falla con "Computador no autorizado", falta autorizar `wsfe` en WSASS.

**Producción (paso 5).** Si `perfil.punto_venta_prod_comunes` es `null`, el usuario todavía no tiene punto de venta para comunes: explicale que tiene que darlo de alta (README, paso 3.3) y autorizar `wsfe` en el Administrador de Relaciones (paso 3.2) antes de emitir. No hay otra diferencia: vos no corrés producción.

**Después (pasos 6 y 7).** Igual que la Factura E: confirmale el número, el CAE y el vencimiento a partir de `facturas/prod/<tipo>-PPPPP-NNNNNNNN.json`, y subí el PDF (`<CUIT>_011_<PPPPP>_<NNNNNNNN>.pdf` para la C) a la misma carpeta de Drive. Si falta el PDF, `pdf.py` lo regenera a partir de ese JSON.

## Si algo falla

- **"Computador no autorizado a acceder al servicio":** falta la relación del alias `perfil.alias_certificado` con wsfex en el Administrador de Relaciones (producción) o en WSASS (homologación).
- **Error 1607 (punto de venta):** el punto de venta no es de tipo "Comprobantes de Exportación - Web Services".
- **`DH_KEY_TOO_SMALL`:** `afip.py` ya lo resuelve con `SECLEVEL=1`. Si reaparece, revisá que no se haya perdido ese ajuste.
- **"El CEE ya posee un TA valido":** borrá el `certs/ta_wsfex_<entorno>.json` solo si el archivo no se puede leer. Si no, esperá a que venza.
- **Certificados:** vencen en `perfil.vencimiento_certificado`. Para renovarlos hay que generar un CSR nuevo con la misma clave y subirlo en ARCA (ver README).
