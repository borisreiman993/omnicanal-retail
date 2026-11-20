# Sistema de Gestión Omnicanal para Retail (ejecución local)

Aplicación web hecha con **Django (Python)**, backend y frontend en el mismo proyecto, con **PostgreSQL** como base de datos.
Implementa lo descrito en el informe técnico (Caso 8): cuatro perfiles (Cliente, Administrador, Vendedor, Bodeguero),
inventario por sucursal con reservas, venta presencial, compra digital con pago por Adapter, pedidos con estados,
devoluciones, avisos a Bodega y notificaciones por correo.

La tienda es **OmniSport** (artículos deportivos). El nombre y el eslogan se cambian en `.env` (`NOMBRE_TIENDA` y
`SLOGAN_TIENDA`), y los productos se administran desde el perfil Administrador (con fotos) o con un CSV (ver más abajo).

**Novedades de diseño:** logo y paleta de OmniSport, catálogo con fotos cuadradas pequeñas, filtros y orden, detalle de
producto, carrito con cantidad (+/−) y papelera, ícono de carrito con contador, menú que se contrae en pantallas chicas
y modo claro/oscuro (el botón de la luna, arriba a la derecha; recuerda tu elección).

---

## 1. Requisitos (ya los tienes)
- Python 3.12
- PostgreSQL 16 (servicio activo, puerto 5432) y la base de datos `omnicanal` creada
- Visual Studio Code

## 2. Puesta en marcha (PowerShell, paso a paso)

Abre la carpeta del proyecto en VS Code y abre una terminal desde el menú **Terminal → Nueva terminal** (debe quedar dentro de la carpeta donde está `manage.py`).

**Paso 1. Crear el entorno virtual**
```powershell
python -m venv .venv
```

**Paso 2. Activar el entorno virtual**
```powershell
.\.venv\Scripts\Activate.ps1
```
Si PowerShell dice que la ejecución de scripts está deshabilitada, ejecuta esto una sola vez y repite el paso 2:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```
Sabrás que está activo porque la línea de la terminal empieza con `(.venv)`.

**Paso 3. Instalar las dependencias**
```powershell
pip install -r requirements.txt
```

**Paso 4. Crear tu archivo de configuración**
```powershell
copy .env.example .env
```
Abre el archivo `.env` y cambia **solo** esta línea por la contraseña que elegiste al instalar PostgreSQL:
```
DB_PASSWORD=PON_AQUI_TU_CONTRASENA_DE_POSTGRES
```
(Sin comillas y sin espacios. El archivo `.env` nunca se sube a GitHub.)

**Paso 5. Crear las tablas en la base de datos**
```powershell
python manage.py migrate
```

**Paso 6. Cargar los datos de demostración** (3 sucursales, un usuario por perfil y el catálogo deportivo con stock)
```powershell
python manage.py cargar_datos_demo
python manage.py cargar_catalogo_deportivo --desactivar-ejemplos
```
El segundo comando carga los productos de `datos/productos_deportivos.csv` con stock inicial y oculta los
"Producto de ejemplo" genéricos. Se puede repetir sin duplicar nada ni pisar el stock que ya exista.

**Fotos de los productos por SKU:** copia las fotos a `datos/fotos/` con el SKU como nombre (`ZAP-001.jpg`,
`POL-001.png`, `BAL-001.webp`...; JPG, PNG o WEBP de hasta 2 MB) y vuelve a ejecutar
`python manage.py cargar_catalogo_deportivo --desactivar-ejemplos`. Asigna cada foto a su producto sin pisar las que ya
tienen foto (usa `--reemplazar-fotos` para cambiarlas). Esas fotos sí viajan por GitHub, así que quien clone el
proyecto las ve al ejecutar el comando.

**Paso 7. Levantar el servidor**
```powershell
python manage.py runserver
```
Abre en el navegador: http://127.0.0.1:8000

> Si prefieres no activar el entorno, puedes anteponer `.\.venv\Scripts\python` a cada comando.
> Ejemplo: `.\.venv\Scripts\python manage.py runserver`

## 3. Usuarios de demostración
Contraseña de todos: **Demo2026!** (solo para pruebas locales)

| Usuario | Perfil | Sucursal |
|---|---|---|
| `cliente_demo` | Cliente | — |
| `admin_demo` | Administrador | — |
| `vendedor_centro` / `vendedor_norte` | Vendedor | Centro / Norte |
| `bodeguero_centro` / `bodeguero_norte` | Bodeguero | Centro / Norte |

Los clientes también pueden crear su propia cuenta en **Crear cuenta**. Los perfiles internos NO se autoregistran:
se crean con un comando técnico:
```powershell
python manage.py crear_trabajador maria --rol VENDEDOR --sucursal "Sucursal Centro"
```
Para el monitoreo técnico y el administrador de Django crea un superusuario:
```powershell
python manage.py createsuperuser
```
(entra a `/monitoreo/` y `/admin-tecnico/`; el perfil "Administrador" comercial no tiene estos accesos).

## 4. Procesos programados (segunda terminal)
El informe prevé tareas periódicas: liberar reservas vencidas, reintentar notificaciones y revisar pagos de resultado incierto.
En una **segunda terminal** (con el entorno activado) déjalo corriendo mientras pruebas:
```powershell
python manage.py procesar_pendientes --cada 60
```
(`Ctrl + C` para detenerlo. Sin `--cada` se ejecuta una sola vez.)

## 5. Cómo probar los flujos
1. **Compra digital:** entra como `cliente_demo` → Catálogo → agrega productos → Carrito → Continuar → elige sucursal y retiro/despacho → en la pasarela simulada elige *Aprobar*, *Rechazar* o *Respuesta incierta*.
   - *Incierta:* la reserva se mantiene y la compra se confirma cuando corre el proceso programado (o con "Verificar nuevamente").
2. **Pedido:** entra como `bodeguero_centro` → Pedidos → avanza los estados. Para despacho debes registrar antes el operador logístico. Los correos aparecen en la terminal del servidor (modo consola).
3. **Venta presencial:** `vendedor_centro` → Nueva venta. Prueba una cantidad mayor al stock: se rechaza.
4. **Devolución:** `vendedor_centro` → Devoluciones → Registrar solicitud (con el N° de una venta) → evaluar. Luego `bodeguero_centro` → Recepciones.
5. **Productos y stock crítico:** `admin_demo`. El Bodeguero ajusta el stock de su sucursal.

## 6. Qué reemplaza a cada componente de Azure del diagrama
| Diagrama | Aquí |
|---|---|
| Azure App Service (Django) | `python manage.py runserver` |
| Azure Database for PostgreSQL | PostgreSQL local |
| Respaldos administrados | `python manage.py respaldar_bd` (usa `pg_dump`; carpeta `backups/`) y `restaurar_bd` |
| Azure Monitor | `logs/omnicanal.log`, tabla de actividad y la página `/monitoreo/` (superusuario) |
| Procesos programados | `python manage.py procesar_pendientes --cada 60` |
| SMTP externo | Correo en consola; configurable en `.env` |
| PayPal Sandbox + Adapter | Adapter con modo simulado (por defecto) y modo PayPal Sandbox (ver abajo) |

Respaldo y restauración (si `pg_dump` no se encuentra, revisa `PG_BIN` en `.env`):
```powershell
python manage.py respaldar_bd
python manage.py restaurar_bd backups\omnicanal_FECHA.dump --si-estoy-seguro
```

## 7. Productos, fotos y stock
- **Productos nuevos:** los crea el Administrador en *Productos → Nuevo producto*. El SKU lo escribe él (único); el ID interno
  se asigna solo. También se pueden cargar muchos juntos con un CSV de columnas `sku,nombre,descripcion,precio,umbral_stock_critico`:
```powershell
python manage.py cargar_productos_csv mis_productos.csv
```
- **Fotos:** el Administrador las sube al crear o editar un producto (JPG, PNG o WEBP, hasta 2 MB; mejor si son cuadradas, por
  ejemplo 800×800). Se guardan en la carpeta `media/`, que **no** se sube a GitHub. Sin foto se muestra un marcador.
- **Stock:** lo ingresa el Bodeguero de cada sucursal (*Inventario → Ajustar*), indicando el stock total que quedará y el motivo.

## 8. Requerimientos y dónde verlos
| RF | Dónde |
|---|---|
| RF-01 productos | Administrador → Productos |
| RF-02 ajuste de inventario | Bodeguero → Inventario → Ajustar (con motivo y movimiento trazable) |
| RF-03 disponibilidad | Catálogo (disponible = registrado − reservado) |
| RF-04 stock crítico | Umbral por producto; pantalla Stock crítico |
| RF-05 / RF-06 venta presencial y aviso | Vendedor → Nueva venta; Bodeguero → Avisos |
| RF-07 carrito | Cliente → Carrito (no reserva stock) |
| RF-08 / RF-09 compra, pago, retiro/despacho | Cliente → Compra → Pago |
| RF-10 estados del pedido | Bodeguero → Pedidos |
| RF-11 / RF-12 consulta y notificaciones | Cliente → Mis pedidos / Notificaciones |
| RF-13 operador logístico | Bodeguero → detalle del pedido (despacho) |
| RF-14 devoluciones | Vendedor → Devoluciones; Bodeguero → Recepciones |
| RF-17 / RF-18 acceso y perfiles | Login con bloqueo; permisos verificados en el servidor |
| RF-15 / RF-16 promociones | **No incluidos** (fuera de alcance según el informe) |

## 9. Parámetros del informe que implementa
- Reserva de stock: 15 minutos (`RESERVA_MINUTOS`).
- Bloqueo de cuenta: 5 intentos fallidos → 15 minutos.
- Sesión: expira a los 30 minutos de inactividad y exige volver a ingresar a las 8 horas.
- Concurrencia: `transaction.atomic()` + `select_for_update()` con orden estable.
- Finalización de la compra idempotente: repetirla no duplica ventas, pedidos, movimientos ni descuentos.

## 10. Modo PayPal Sandbox real (opcional)
En `.env`: `PAGO_PROVEEDOR=paypal_sandbox`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` (de developer.paypal.com) y
`PAGO_CUENTA_RECEPTORA` con el correo de tu cuenta Business de Sandbox. PayPal no acepta pesos chilenos:
el adaptador convierte según `PAYPAL_MONEDA` y `PAYPAL_CLP_POR_UNIDAD` (tasa de ejemplo que debes ajustar).
**Importante:** ese adaptador está escrito según la documentación de PayPal pero **no fue probado contra PayPal real**.

## 11. Pruebas automáticas (82)
```powershell
python manage.py test core
```
Crea una base temporal `test_omnicanal` (el usuario `postgres` tiene permiso) y borra los datos de prueba al terminar.
Incluyen pruebas de concurrencia sobre las últimas unidades de stock.

## 12. Estructura
```
manage.py            config/ (configuración)       core/ (la aplicación)
core/models.py       entidades del informe         core/services.py   reglas de negocio
core/pagos.py        Adapter de pagos              core/views.py      pantallas
core/templates/      frontend (HTML)               core/static/       estilos (CSS)
core/management/     comandos (demo, catálogo deportivo, procesos, respaldos, CSV)   core/tests.py
datos/               CSV del catálogo deportivo    media/ (fotos subidas, no va a GitHub)
```
