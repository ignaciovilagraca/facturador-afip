# Alta en ARCA paso a paso

<!-- seccion:introduccion -->

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

<!-- seccion:paso1 -->
### Paso 1: clave privada y pedido de certificado (CSR)

Lo hace el servidor con `iniciar_configuracion`: genera en la computadora de la persona una clave privada y un CSR por entorno, y escribe el CUIT y los datos del emisor. La clave privada nunca sale de la computadora ni se muestra. Los CSR son públicos: la herramienta los devuelve para pegarlos o subirlos en ARCA.

- El alias va **solo con letras y números** (sin guiones, espacios ni acentos); si no, ARCA lo rechaza con "El Nombre simbólico del DN sólo puede contener números y/o letras". Ejemplo: `facturador1a2b3c`.
- Los datos del emisor (razón social, domicilio comercial, condición frente al IVA, ingresos brutos, inicio de actividades) van impresos en el PDF de cada factura.

<!-- seccion:homologacion -->
### Paso 2: homologación (entorno de pruebas)

1. Entrá a [arca.gob.ar](https://www.arca.gob.ar) con clave fiscal.
2. Si no tenés el servicio "WSASS - Autogestión Certificados Homologación", adherilo:
   1. Entrá a "Administrador de Relaciones de Clave Fiscal".
   2. Elegí "Adherir servicio".
   3. Buscá ARCA → Servicios interactivos → "WSASS - Autogestión Certificados Homologación" y confirmá.
   4. Cerrá sesión y volvé a entrar para que aparezca.
3. En WSASS, elegí "Nuevo Certificado":
   1. En "Nombre simbólico del DN" poné el alias (el mismo `ALIAS` del CSR).
   2. En "Solicitud de certificado en formato PKCS#10" pegá el CSR de homologación que devolvió `iniciar_configuracion` (o `ver_csr`), incluidas las líneas `-----BEGIN CERTIFICATE REQUEST-----` y `-----END CERTIFICATE REQUEST-----`.
   3. Elegí "Crear DN y obtener certificado".
   4. WSASS no da un archivo: muestra el certificado en la pantalla. Copiá el texto, desde `-----BEGIN CERTIFICATE-----` hasta `-----END CERTIFICATE-----`, y pasáselo a Claude: lo guarda `guardar_certificado` con entorno `homo`, que verifica que corresponda a la clave. Ojo con no confundirlo con el CSR, que empieza con `-----BEGIN CERTIFICATE REQUEST-----`.
4. En WSASS, elegí "Crear autorización a servicio":
   1. Nombre simbólico del DN: tu alias.
   2. CUIT representada: tu CUIT.
   3. Servicio: `wsfe - Facturación Electrónica` para las Facturas A, B y C. Si también facturás al exterior, creá otra autorización con `wsfex - Facturación Electrónica de Exportación`.
   4. Confirmá con "Crear autorización de acceso".

En homologación no hace falta dar de alta puntos de venta: acepta cualquier número.

<!-- seccion:produccion_certificado -->
### 3.1 Obtener el certificado

1. Con clave fiscal, entrá a "Administración de Certificados Digitales". Si no aparece, adherilo como en el paso 2.2, buscando ARCA → Servicios interactivos → "Administración de Certificados Digitales".
2. Elegí tu CUIT y después "Agregar alias".
3. Poné el alias (solo letras y números), subí el archivo del CSR de producción (`certs/afip_prod.csr` en la carpeta de datos; `ver_csr` muestra la ruta) y confirmá con "Agregar alias".
4. En la lista de alias, tocá **"Ver"** en la fila de tu alias.
5. En la pantalla siguiente, tocá **"Descargar"** en el certificado. Baja un archivo `.crt`.
6. Decile a Claude dónde quedó el archivo (por ejemplo, en Descargas): lo guarda `guardar_certificado` con entorno `prod` y la ruta, y verifica que corresponda a la clave.

El certificado de producción lo emite "Computadores" de AFIP (el de homologación, "Computadores Test"). ARCA usa como CN el alias que escribiste en la pantalla, aunque el CSR tenga otro.

<!-- seccion:produccion_autorizacion -->
### 3.2 Autorizar el certificado para el servicio

1. Entrá a "Administrador de Relaciones de Clave Fiscal".
2. Elegí "Nueva Relación".
3. En "Servicio", tocá "Buscar" y elegí ARCA → **WebServices** → "Facturación Electrónica" (Facturas A, B y C). No uses la rama "Servicios interactivos": da el error "El servicio debe ser delegable".
4. En "Representante", tocá "Buscar" (no escribas un CUIT en el campo), marcá **"Computador Fiscal"** y elegí tu alias en el desplegable.
5. Confirmá. Si te lo pide, generá e imprimí el formulario F.3283.

Si aparece "El dador de la autorización no debe ser igual al autorizado", en "Representante" quedó tu propio CUIT como persona: tiene que ser el Computador Fiscal (el certificado).

Si también facturás al exterior, creá otra relación igual en Administrador de Relaciones de Clave Fiscal → Nueva Relación, con ARCA → WebServices → "Facturación Electrónica de Exportación" y el mismo Computador Fiscal. ARCA puede tardar unos minutos en aplicar una relación nueva.

<!-- seccion:punto_de_venta -->
### 3.3 Dar de alta el punto de venta

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
| 5 | Factura Electrónica - Monotributo - Web Services | Factura C |
| 4 | Comprobantes de Exportación - Web Services | Factura E |

<!-- seccion:lista_de_control -->
### 3.4 Lista de control

Para cada servicio que vayas a usar (`wsfe`, `wsfex` o los dos):

- [ ] Certificado de producción guardado con `guardar_certificado` (paso 3.1).
- [ ] Relación del alias como Computador Fiscal con el servicio (paso 3.2).
- [ ] Punto de venta del sistema "... - Web Services" que corresponde (paso 3.3).
- [ ] `probar_conexion` en producción muestra el punto de venta y el último número (paso 4).

<!-- seccion:verificar_y_errores -->
### Paso 4: verificar

Llamá a `estado_configuracion` (muestra qué falta: CUIT, certificados, perfil) y a `probar_conexion` para cada entorno (`homo` y `prod`) y servicio (`wsfe` para A, B y C; `wsfex` para la E).

Son consultas de solo lectura: no emiten nada. Tiene que mostrar el servicio OK, el login en WSAA y, en producción, tu punto de venta con `N` (no bloqueado) y el último número emitido (0 si es nuevo). Errores frecuentes:

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
| El CEE ya posee un TA valido para el acceso al WSN solicitado | ARCA ya entregó un ticket de acceso para ese certificado y servicio, y dura 12 horas; no da otro hasta que venza. Pasa si se borra `certs/ta_*.json` o si otro programa usa el mismo certificado con otra carpeta de datos. Esperá a que venza; no borres esos archivos. |
| 1607: Campo Pto_venta no es valido | El punto de venta no es del sistema "Comprobantes de Exportación - Web Services". |
| Sin puntos de venta en `probar_conexion` de producción con `wsfe` | No hay punto de venta de web services para A, B y C, o todavía no se propagó el alta. |
| 1500 (fecha) | La fecha de emisión está fuera de lo que acepta ARCA: 5 días antes o después de hoy (10 para servicios en A, B y C). |
| 1535 (Factura E) o 10016 (A, B y C): fecha anterior al último comprobante | En cada punto de venta las fechas no pueden retroceder. En homologación suele ser por pruebas viejas: repetí con otro `punto_venta_homo`. |
| 1674 / 10036: fecha de pago anterior a la emisión | La fecha de pago (o de vencimiento) tiene que ser igual o posterior a la de emisión. |
| 2053: cotización no válida | La cotización tiene que ser la del día anterior a la fecha del comprobante; el servidor ya la pide así. |
| Aprobada con la observación 10238 (CUIT receptora inexistente) | ARCA emitió la factura igual. En producción hay que anularla con nota de crédito. Revisá el CUIT del receptor antes de emitir. |
| DH_KEY_TOO_SMALL | El servidor de producción usa una clave Diffie-Hellman de 1024 bits; el servidor ya lo resuelve. |

<!-- seccion:renovacion -->
### Renovación

Los certificados vencen a los 2 años (`estado_configuracion` muestra la fecha y cuántos días faltan). Para renovarlos alcanza con pedir un certificado nuevo para **el mismo alias y la misma clave**: las autorizaciones y relaciones son del alias, así que siguen valiendo.

- **Homologación:** en WSASS → "Nuevo Certificado", poné el mismo alias como nombre simbólico del DN, pegá el mismo CSR (`ver_csr`) y guardá el certificado nuevo con `guardar_certificado`.
- **Producción:** en "Administración de Certificados Digitales", elegí tu CUIT, tocá "Ver" en la fila del alias y después "Agregar certificado"; subí el mismo CSR de producción. En la pantalla del alias vas a ver dos certificados: tocá "Descargar" en el nuevo (el de vencimiento más lejano) y guardalo con `guardar_certificado`.

Si creés que la clave privada quedó expuesta, no renueves: generá una clave nueva con otro alias y hacé todo el alta de nuevo (certificado, autorizaciones y relaciones).
