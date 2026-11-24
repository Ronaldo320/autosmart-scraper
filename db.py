"""
Acceso a MongoDB: conexión, modelo de datos ($jsonSchema), índices y migración.

Modelo (base de datos AutoSmartDB):

  vehiculos  ─┐   catálogo (scraper + altas manuales)
              │   · etiquetas: [string]              ← ARRAY
              │   · orden_id: ObjectId → ordenes      ← REFERENCIA (cuando se vende)
  clientes   ─┤   compradores
              │   · direccion: {direccion, ciudad, departamento}   ← EMBEBIDO
  ordenes    ─┘   compras
                  · cliente_id: ObjectId → clientes   ← REFERENCIA
                  · direccion_entrega: {...}           ← EMBEBIDO (copia al momento de comprar)
                  · items: [{vehiculo_id: ObjectId → vehiculos, title, price, ...}]  ← ARRAY de embebidos con REFERENCIA
"""
import re
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import OperationFailure

import config

_cliente = None


def get_db():
    """Un solo MongoClient por proceso (el scraper corre en procesos aparte)."""
    global _cliente
    if _cliente is None:
        _cliente = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=8000)
    return _cliente[config.MONGO_DB]


def reiniciar_conexion():
    """Se llama al iniciar un proceso hijo: cada proceso abre su propia conexión."""
    global _cliente
    _cliente = None


def vehiculos():
    return get_db()["vehiculos"]


def clientes():
    return get_db()["clientes"]


def ordenes():
    return get_db()["ordenes"]


# ---------------------------------------------------------------- utilidades
def a_object_id(valor):
    try:
        return ObjectId(str(valor))
    except (InvalidId, TypeError):
        return None


def formato_cop(n):
    return "$ " + f"{int(n or 0):,}".replace(",", ".")


MARCAS_COMPUESTAS = ["Mercedes-Benz", "Mercedes Benz", "Land Rover", "Great Wall", "Alfa Romeo",
                     "Aston Martin", "Rolls Royce", "Rolls-Royce", "Range Rover", "Lynk & Co"]


SIGLAS = {"BMW", "MG", "BYD", "JAC", "DS", "RAM", "JMC", "FAW", "BAIC", "DFSK", "GMC", "KTM", "GAC", "SEAT"}


def normalizar_marca(texto):
    """Escritura única de cada marca ('kia', 'KIA' → 'Kia'; 'bmw' → 'BMW') para que se agrupen bien."""
    t = re.sub(r"\s+", " ", str(texto or "")).strip()
    if not t:
        return ""
    for m in MARCAS_COMPUESTAS:
        if t.lower() == m.lower():
            return "Mercedes-Benz" if m.lower().startswith("mercedes") else m
    return t.upper() if t.upper() in SIGLAS else "-".join(p.capitalize() for p in t.split("-"))


def inferir_marca(titulo):
    """Marca a partir del título: marca compuesta conocida o la primera palabra."""
    t = re.sub(r"^\s*foto\s+", "", titulo or "", flags=re.I).strip()
    for m in MARCAS_COMPUESTAS:
        if t.lower().startswith(m.lower()):
            return normalizar_marca(m)
    primera = t.split(" ")[0] if t else ""
    return normalizar_marca(primera) or "Otra"


def serializar(valor):
    """Convierte ObjectId y fechas a texto para poder responder JSON."""
    if isinstance(valor, ObjectId):
        return str(valor)
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, dict):
        return {k: serializar(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [serializar(v) for v in valor]
    return valor


# ------------------------------------------------- validación ($jsonSchema)
NUMERO = ["int", "long", "double", "decimal"]

ESQUEMAS = {
    "vehiculos": {
        "bsonType": "object",
        "required": ["title", "marca", "price", "source", "condition", "estado", "etiquetas"],
        "properties": {
            "title": {"bsonType": "string", "minLength": 2, "maxLength": 200,
                      "description": "Nombre del vehículo (obligatorio)"},
            "marca": {"bsonType": "string", "minLength": 1, "maxLength": 40},
            "price": {"bsonType": NUMERO, "minimum": 0, "maximum": 10_000_000_000,
                      "description": "Precio en pesos colombianos"},
            "source": {"enum": ["autocosmos", "carroya", "manual"],
                       "description": "De dónde viene el anuncio"},
            "condition": {"enum": ["nuevo", "usado"]},
            "estado": {"enum": ["disponible", "vendido"]},
            "url": {"bsonType": "string"},
            "year": {"bsonType": "string"},
            "mileage_km": {"bsonType": NUMERO + ["null"], "minimum": 0},
            "location": {"bsonType": "string"},
            "image": {"bsonType": "string"},
            "etiquetas": {"bsonType": "array", "maxItems": 15, "uniqueItems": True,
                          "items": {"bsonType": "string", "minLength": 1, "maxLength": 30}},
            "orden_id": {"bsonType": ["objectId", "null"],
                         "description": "Referencia a la orden que lo compró"},
        },
    },
    "clientes": {
        "bsonType": "object",
        "required": ["nombre", "documento", "email", "telefono", "direccion", "fecha_registro"],
        "properties": {
            "nombre": {"bsonType": "string", "minLength": 3, "maxLength": 100},
            "documento": {"bsonType": "string", "pattern": "^[0-9]{5,12}$",
                          "description": "Cédula o NIT, solo números"},
            "email": {"bsonType": "string", "pattern": "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"},
            "telefono": {"bsonType": "string", "pattern": "^[0-9]{7,12}$"},
            "direccion": {
                "bsonType": "object",
                "required": ["direccion", "ciudad"],
                "properties": {
                    "direccion": {"bsonType": "string", "minLength": 3},
                    "ciudad": {"bsonType": "string", "minLength": 3},
                    "departamento": {"bsonType": "string"},
                },
            },
            "fecha_registro": {"bsonType": "date"},
        },
    },
    "ordenes": {
        "bsonType": "object",
        "required": ["numero_orden", "fecha", "cliente_id", "direccion_entrega", "items",
                     "cantidad", "total", "metodo_pago", "estado"],
        "properties": {
            "numero_orden": {"bsonType": "string", "pattern": "^AS-[0-9]{8}-[0-9A-F]{6}$"},
            "fecha": {"bsonType": "date"},
            "cliente_id": {"bsonType": "objectId", "description": "Referencia a clientes._id"},
            "cliente": {"bsonType": "object", "description": "Copia del nombre/correo al comprar"},
            "direccion_entrega": {
                "bsonType": "object",
                "required": ["direccion", "ciudad"],
                "properties": {
                    "direccion": {"bsonType": "string", "minLength": 3},
                    "ciudad": {"bsonType": "string", "minLength": 3},
                    "departamento": {"bsonType": "string"},
                },
            },
            "items": {
                "bsonType": "array", "minItems": 1, "maxItems": 10,
                "items": {
                    "bsonType": "object",
                    "required": ["vehiculo_id", "title", "price"],
                    "properties": {
                        "vehiculo_id": {"bsonType": "objectId", "description": "Referencia a vehiculos._id"},
                        "title": {"bsonType": "string"},
                        "price": {"bsonType": NUMERO, "minimum": 0},
                    },
                },
            },
            "cantidad": {"bsonType": NUMERO, "minimum": 1},
            "total": {"bsonType": NUMERO, "minimum": 0},
            "metodo_pago": {"enum": ["tarjeta", "pse", "transferencia"]},
            "estado": {"enum": ["pagada", "cancelada"]},
        },
    },
}

# Índices: (colección, claves, opciones, para qué sirve)
INDICES = [
    ("vehiculos", [("url", ASCENDING)],
     {"name": "url_unica", "unique": True, "partialFilterExpression": {"url": {"$type": "string"}}},
     "El scraper guarda/actualiza por URL (upsert) y evita autos duplicados."),
    ("vehiculos", [("condition", ASCENDING), ("price", ASCENDING)],
     {"name": "condicion_precio"},
     "Filtros del catálogo/admin por nuevo-usado y precio; $match del reporte de precio promedio por marca."),
    ("vehiculos", [("marca", ASCENDING), ("estado", ASCENDING)],
     {"name": "marca_estado"},
     "Filtro por marca en el panel de administración y agrupación del inventario por marca."),
    ("clientes", [("documento", ASCENDING)],
     {"name": "documento_unico", "unique": True},
     "El checkout busca/crea al cliente por su cédula; impide clientes duplicados."),
    ("ordenes", [("numero_orden", ASCENDING)],
     {"name": "numero_orden_unico", "unique": True},
     "Consulta de una orden por su número."),
    ("ordenes", [("cliente_id", ASCENDING), ("fecha", DESCENDING)],
     {"name": "cliente_fecha"},
     "Campo foráneo del $lookup clientes→ordenes (detalle y lista de clientes) y verificación antes de borrar un cliente."),
    ("ordenes", [("items.vehiculo_id", ASCENDING)],
     {"name": "items_vehiculo"},
     "Índice multikey: campo foráneo del $lookup vehiculos→ordenes y verificación antes de borrar un vehículo."),
    ("ordenes", [("estado", ASCENDING), ("fecha", ASCENDING)],
     {"name": "estado_fecha"},
     "$match por estado y rango de fechas de los reportes (ventas mes a mes, por marca)."),
]


def configurar_bd():
    """Crea las colecciones con su $jsonSchema (o lo actualiza con collMod) y crea los índices."""
    base = get_db()
    existentes = set(base.list_collection_names())
    for nombre, esquema in ESQUEMAS.items():
        opciones = {"validator": {"$jsonSchema": esquema},
                    # vehiculos: 'moderate' para no bloquear actualizaciones de anuncios viejos del scraper
                    "validationLevel": "moderate" if nombre == "vehiculos" else "strict",
                    "validationAction": "error"}
        try:
            if nombre in existentes:
                base.command("collMod", nombre, **opciones)
            else:
                base.create_collection(nombre, **opciones)
        except OperationFailure as e:
            print(f"[!] No se pudo aplicar el esquema de {nombre}: {e}")
    for coleccion, claves, opciones, _ in INDICES:
        base[coleccion].create_index(claves, **opciones)


# ------------------------------------------------------------ migración
def migrar_modelo():
    """Adapta los datos guardados por versiones anteriores al modelo actual. Es idempotente."""
    veh, cli, ords = vehiculos(), clientes(), ordenes()
    cambios = 0

    # Vehículos: campos obligatorios nuevos
    cambios += veh.update_many({"estado": {"$exists": False}}, {"$set": {"estado": "disponible"}}).modified_count
    cambios += veh.update_many({"etiquetas": {"$exists": False}}, {"$set": {"etiquetas": []}}).modified_count
    cambios += veh.update_many({"condition": {"$exists": False}}, {"$set": {"condition": "usado"}}).modified_count
    for v in veh.find({"marca": {"$exists": False}}, {"title": 1}):
        veh.update_one({"_id": v["_id"]}, {"$set": {"marca": inferir_marca(v.get("title", ""))}})
        cambios += 1
    for m in veh.distinct("marca"):   # una sola escritura por marca
        if m and normalizar_marca(m) != m:
            cambios += veh.update_many({"marca": m}, {"$set": {"marca": normalizar_marca(m)}}).modified_count
    for v in veh.find({"year": {"$exists": True, "$not": {"$type": "string"}}}, {"year": 1}):
        veh.update_one({"_id": v["_id"]}, {"$set": {"year": str(v["year"])}})
    for v in veh.find({"fecha_extraccion": {"$type": "string"}}, {"fecha_extraccion": 1}):
        try:
            f = datetime.strptime(v["fecha_extraccion"], "%d/%m/%Y %H:%M:%S")
            veh.update_one({"_id": v["_id"]}, {"$set": {"fecha_extraccion": f}})
        except ValueError:
            pass

    # Órdenes de versiones anteriores: cliente embebido → colección clientes + cliente_id
    for o in ords.find({"cliente_id": {"$exists": False}}):
        c = o.get("cliente") or {}
        documento = re.sub(r"\D", "", str(c.get("documento", ""))) or "00000"
        ciudad = c.get("ciudad") or "Sin ciudad"
        direccion = {"direccion": "No registrada", "ciudad": ciudad, "departamento": ""}
        cliente = cli.find_one_and_update(
            {"documento": documento},
            {"$setOnInsert": {"nombre": c.get("nombre", "Cliente"), "documento": documento,
                              "email": c.get("email", "sin-correo@autosmart.co"),
                              "telefono": re.sub(r"\D", "", str(c.get("telefono", ""))) or "0000000",
                              "direccion": direccion, "fecha_registro": o.get("fecha", datetime.now())}},
            upsert=True, return_document=True)
        items = []
        for it in o.get("items", []):
            it = dict(it)
            oid = a_object_id(it.pop("id", None)) or it.get("vehiculo_id")
            if oid:
                it["vehiculo_id"] = oid
                items.append(it)
        ords.update_one({"_id": o["_id"]}, {
            "$set": {"cliente_id": cliente["_id"],
                     "cliente": {"nombre": cliente["nombre"], "email": cliente["email"],
                                 "documento": cliente["documento"]},
                     "direccion_entrega": direccion, "items": items, "cantidad": len(items)}})
        cambios += 1

    # Vehículos vendidos por versiones anteriores: 'orden' (texto) → orden_id (ObjectId)
    for v in veh.find({"orden": {"$type": "string"}}):
        o = ords.find_one({"numero_orden": v["orden"]}, {"_id": 1})
        veh.update_one({"_id": v["_id"]}, {"$set": {"orden_id": o["_id"] if o else None},
                                           "$unset": {"orden": ""}})
        cambios += 1
    return cambios
