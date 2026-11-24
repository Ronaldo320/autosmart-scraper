# AutoSmart DB 🚗

**Tienda en línea de vehículos nuevos y usados en Colombia, con MongoDB.**

AutoSmart reúne en un solo catálogo los anuncios de **Autocosmos** y **CarroYa** (scraper con Playwright), permite comprarlos con un carrito y un pago simulado, administrar vehículos, clientes y órdenes (CRUD completo) y analizar las ventas con consultas del **framework de agregación** de MongoDB.

Proyecto final — Electiva IV (MongoDB), Fundación Universitaria Tecnológico Comfenalco.

## Funcionalidades

| Página | Qué hace |
|---|---|
| `/` Catálogo | Búsqueda, filtros (nuevo/usado, orden), panel "mejor oferta", comparador, carrito y checkout con pago simulado |
| `/vehiculo/<id>` | Detalle de un vehículo |
| `/admin` | CRUD de **vehículos**, **clientes** y **órdenes**: crear, listar con filtros y paginación, ver detalle, editar (incluye campos embebidos y arrays con `$push`/`$pull`) y eliminar con integridad referencial |
| `/reportes` | 5 consultas `aggregate` con su pregunta de negocio, filtros, tabla, gráfico, el pipeline exacto y los índices que usó MongoDB |
| `/dashboard` | KPIs y gráficos de ventas (calculados con `aggregate` + `$facet`) |

## Modelo de datos (base `AutoSmartDB`)

```
clientes                         ordenes                                   vehiculos
─────────                        ───────                                   ─────────
_id: ObjectId  ◄──────────────── cliente_id: ObjectId          ┌─────────► _id: ObjectId
nombre, documento (único)        numero_orden (único), fecha   │          title, marca, price
email, telefono                  cliente: {nombre, email, ...} │          condition (nuevo|usado)
direccion: {  ◄ EMBEBIDO         direccion_entrega: { ◄ EMBEBIDO          estado (disponible|vendido)
  direccion, ciudad,               direccion, ciudad, depto }  │          etiquetas: [string]  ◄ ARRAY
  departamento }                 items: [  ◄ ARRAY             │          orden_id: ObjectId → ordenes
fecha_registro                     { vehiculo_id: ObjectId ────┘          source, url (único), image,
                                     title, price, ... } ]                year, mileage_km, location
                                 cantidad, total, metodo_pago, estado (pagada|cancelada)
```

- **Embebido:** la dirección del cliente y la dirección de entrega de cada orden (se leen siempre junto con su documento; la de la orden es una copia histórica del momento de la compra).
- **Referencias (ObjectId):** `ordenes.cliente_id → clientes`, `ordenes.items.vehiculo_id → vehiculos`, `vehiculos.orden_id → ordenes` (clientes y vehículos tienen vida propia y crecen sin límite).
- **Arrays:** `ordenes.items` y `vehiculos.etiquetas`.
- **Validación:** las tres colecciones tienen `$jsonSchema` (ver `db.py`). La aplicación además valida cada formulario.
- **Índices:** 8 índices, cada uno justificado en `db.py` (`INDICES`), entre ellos los campos foráneos de los `$lookup` (`ordenes.cliente_id`, `ordenes.items.vehiculo_id`).

## Instalación

Requisitos: Python 3.10+, MongoDB 5.0+ (local o Atlas).

```bash
git clone https://github.com/Ronaldo320/autosmart-scraper.git
cd autosmart-scraper
python -m venv venv
venv\Scripts\activate          # Windows  (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt
python -m playwright install chromium
```

### Configuración segura (variables de entorno)

Las credenciales **no** están en el código. Copia la plantilla y complétala:

```bash
copy .env.example .env         # Linux/Mac: cp .env.example .env
```

```
MONGO_URI=mongodb://localhost:27017/
MONGO_DB=AutoSmartDB
SECRET_KEY=<genera una con: python -c "import secrets; print(secrets.token_hex(32))">
```

`.env` está en `.gitignore`, así que nunca se sube al repositorio.

### Ejecutar

```bash
python app.py              # migra datos viejos, aplica $jsonSchema e índices, y scrapea si hay < 1200 vehículos
python app.py --no-scrape  # solo la aplicación web
python app.py --scrape     # busca más vehículos aunque ya se haya llegado a la meta
```

Abre http://127.0.0.1:5000

### Datos de demostración para los reportes

```bash
python datos_demo.py            # 80 clientes y 220 órdenes en los últimos 12 meses (con vehículos reales del catálogo)
python datos_demo.py --borrar   # los elimina y libera los vehículos
```

## Estructura del código

| Archivo | Responsabilidad |
|---|---|
| `app.py` | Crea la app Flask, registra los módulos y prepara la base de datos al arrancar |
| `config.py` | Lee `.env` (credenciales) |
| `db.py` | Conexión, `$jsonSchema`, índices y migración de datos antiguos |
| `tienda.py` | Catálogo, carrito, checkout y dashboard |
| `admin.py` | API CRUD de vehículos, clientes y órdenes |
| `reportes.py` | Pipelines de agregación y página de reportes |
| `scraper.py` | Extracción de Autocosmos y CarroYa (procesos en paralelo) |
| `datos_demo.py` | Generador de datos de demostración |
| `templates/` | Páginas HTML (JavaScript sin frameworks, Chart.js para gráficos) |

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/vehicles` | Catálogo |
| GET/POST/DELETE | `/api/cart`, `/api/cart/<id>` | Carrito (sesión) |
| POST | `/api/checkout` | Compra: crea/actualiza el cliente y la orden |
| GET, POST | `/api/admin/vehiculos` | Listar (`q`, `marca`, `condition`, `estado`, `source`, `orden`, `pagina`) / crear |
| GET, PUT, DELETE | `/api/admin/vehiculos/<id>` | Detalle (`$lookup` a órdenes) / editar / eliminar (bloqueado si está en una orden) |
| POST, DELETE | `/api/admin/vehiculos/<id>/etiquetas[/<e>]` | `$push` / `$pull` de etiquetas |
| GET, POST | `/api/admin/clientes` | Listar (con `$lookup` a órdenes) / crear |
| GET, PUT, DELETE | `/api/admin/clientes/<id>` | Detalle / editar (incluye la dirección embebida) / eliminar (bloqueado si tiene órdenes) |
| GET | `/api/admin/ordenes` | Listar (`q`, `estado`) |
| GET, PUT, DELETE | `/api/admin/ordenes/<id>` | Detalle (`$lookup` a clientes y vehículos) / cambiar estado o dirección / eliminar (libera los vehículos) |
| DELETE | `/api/admin/ordenes/<id>/items/<vehiculo_id>` | `$pull` de un ítem y `$inc` del total |
| GET | `/api/reportes`, `/api/reportes/<id>` | Lista de reportes / ejecutar uno (devuelve resultados, pipeline e índices usados) |
| GET | `/api/dashboard` | Datos del dashboard |
