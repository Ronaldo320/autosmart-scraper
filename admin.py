"""
Panel de administración: CRUD de vehículos, clientes y órdenes.

  Crear      POST   /api/admin/<coleccion>
  Leer       GET    /api/admin/<coleccion>?q=...&filtros   (lista paginada)
             GET    /api/admin/<coleccion>/<id>            (detalle, con $lookup)
  Actualizar PUT    /api/admin/<coleccion>/<id>            ($set, incluye campos embebidos)
             POST   /api/admin/vehiculos/<id>/etiquetas    ($push al array)
             DELETE /api/admin/vehiculos/<id>/etiquetas/<e> ($pull del array)
             DELETE /api/admin/ordenes/<id>/items/<vid>     ($pull de un ítem embebido)
  Eliminar   DELETE /api/admin/<coleccion>/<id>            (con integridad referencial)
"""
import math
import re
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request
from pymongo.errors import DuplicateKeyError, WriteError

import db
from db import a_object_id, formato_cop, inferir_marca, normalizar_marca, serializar
from tienda import liberar_vehiculos

bp = Blueprint("admin", __name__)

POR_PAGINA = 20


@bp.route("/admin")
def panel():
    return render_template("admin.html")


# ----------------------------------------------------------------- utilidades
def error(mensaje, codigo=400, campos=None):
    cuerpo = {"ok": False, "error": mensaje}
    if campos:
        cuerpo["campos"] = campos
    return jsonify(cuerpo), codigo


def manejar_errores_mongo(func):
    """Traduce los errores de MongoDB ($jsonSchema, índices únicos) a respuestas claras."""
    def envoltura(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except DuplicateKeyError as e:
            campo = next(iter((e.details or {}).get("keyValue", {}) or {}), "dato")
            return error(f"Ya existe un registro con ese {campo}.", 409, {campo: "Ya está registrado."})
        except WriteError as e:
            if e.code == 121:  # DocumentValidationFailure
                return error("MongoDB rechazó el documento: no cumple el esquema ($jsonSchema) de la colección.",
                             400, {"_esquema": str(e.details.get("errInfo", ""))[:400] if e.details else ""})
            raise
    envoltura.__name__ = func.__name__
    return envoltura


def paginar(coleccion, filtro, orden, proyeccion=None):
    pagina = max(int(request.args.get("pagina", 1) or 1), 1)
    total = coleccion.count_documents(filtro)
    docs = coleccion.find(filtro, proyeccion).sort(orden).skip((pagina - 1) * POR_PAGINA).limit(POR_PAGINA)
    return {"items": [serializar(d) for d in docs], "total": total, "pagina": pagina,
            "paginas": max(math.ceil(total / POR_PAGINA), 1)}


def regex_busqueda(texto):
    return {"$regex": re.escape(texto.strip()), "$options": "i"}


def obtener_o_404(coleccion, id_texto):
    oid = a_object_id(id_texto)
    doc = coleccion.find_one({"_id": oid}) if oid else None
    return oid, doc


def numero(valor):
    """Entero a partir de texto: acepta '245.000.000', '245000000' o '$ 245.000.000'."""
    texto = str(valor if valor is not None else "").strip()
    if not texto or re.search(r"[a-zA-Z]", texto) or texto.lstrip("$ ").startswith("-"):
        return None   # vacío, con letras o negativo
    digitos = re.sub(r"\D", "", texto.split(",")[0])
    return int(digitos) if digitos else None


# ================================================================ VEHÍCULOS
def validar_vehiculo(data, parcial=False):
    """Valida y normaliza los campos editables. parcial=True: solo los que vienen en data."""
    errores, doc = {}, {}

    def hay(k):
        return not parcial or k in data

    if hay("title"):
        t = str(data.get("title", "")).strip()
        if len(t) < 2: errores["title"] = "El nombre es obligatorio."
        doc["title"] = t
    if hay("marca"):
        m = normalizar_marca(data.get("marca", "")) or inferir_marca(doc.get("title", ""))
        if not m: errores["marca"] = "Indica la marca."
        doc["marca"] = m
    if hay("price"):
        p = numero(data.get("price"))
        if p is None or p <= 0: errores["price"] = "El precio debe ser un número mayor que 0."
        doc["price"] = p or 0
        doc["price_text"] = formato_cop(p or 0)
    if hay("condition"):
        c = data.get("condition")
        if c not in ("nuevo", "usado"): errores["condition"] = "Elige nuevo o usado."
        doc["condition"] = c
    if hay("year"):
        y = str(data.get("year", "")).strip()
        if y and not re.fullmatch(r"(19[5-9]\d|20[0-3]\d)", y): errores["year"] = "Año inválido."
        doc["year"] = y or "N/A"
    if hay("mileage_km"):
        raw = str(data.get("mileage_km", "")).strip()
        km = numero(raw) if raw else None
        if raw and km is None: errores["mileage_km"] = "Kilometraje inválido."
        doc["mileage_km"] = km
        doc["mileage"] = f"{km:,} km".replace(",", ".") if km is not None else "Consultar"
    if hay("location"):
        doc["location"] = str(data.get("location", "")).strip() or "Colombia"
    if hay("image"):
        img = str(data.get("image", "")).strip()
        if img and not img.startswith("http"): errores["image"] = "La foto debe ser una URL http(s)."
        doc["image"] = img
    if hay("url"):
        u = str(data.get("url", "")).strip()
        if u and not u.startswith("http"): errores["url"] = "El enlace debe empezar por http."
        if u: doc["url"] = u
    return doc, errores


@bp.route("/api/admin/vehiculos")
def listar_vehiculos():
    f = {}
    if request.args.get("q"):
        f["$or"] = [{"title": regex_busqueda(request.args["q"])}, {"marca": regex_busqueda(request.args["q"])}]
    for campo in ("marca", "condition", "estado", "source"):
        if request.args.get(campo):
            f[campo] = request.args[campo]
    orden = {"precio_asc": [("price", 1)], "precio_desc": [("price", -1)]}.get(
        request.args.get("orden"), [("_id", -1)])
    res = paginar(db.vehiculos(), f, orden,
                  {"title": 1, "marca": 1, "price": 1, "condition": 1, "estado": 1, "source": 1,
                   "year": 1, "mileage": 1, "location": 1, "image": 1, "etiquetas": 1})
    res["marcas"] = sorted(m for m in db.vehiculos().distinct("marca") if m)
    return jsonify(res)


@bp.route("/api/admin/vehiculos/<vid>")
def detalle_vehiculo(vid):
    """Detalle con $lookup a las órdenes que incluyen este vehículo (índice items.vehiculo_id)."""
    oid = a_object_id(vid)
    r = list(db.vehiculos().aggregate([
        {"$match": {"_id": oid}},
        {"$lookup": {"from": "ordenes", "localField": "_id", "foreignField": "items.vehiculo_id",
                     "as": "ordenes",
                     "pipeline": [{"$project": {"numero_orden": 1, "fecha": 1, "estado": 1, "cliente.nombre": 1}}]}},
    ])) if oid else []
    if not r:
        return error("Vehículo no encontrado.", 404)
    return jsonify(serializar(r[0]))


@bp.route("/api/admin/vehiculos", methods=["POST"])
@manejar_errores_mongo
def crear_vehiculo():
    data = request.get_json(silent=True) or {}
    doc, errores = validar_vehiculo(data)
    if errores:
        return error("Revisa los datos del vehículo.", 400, errores)
    etiquetas = [e.strip().lower() for e in data.get("etiquetas", []) if str(e).strip()]
    doc.update({"source": "manual", "estado": "disponible", "etiquetas": sorted(set(etiquetas))[:15],
                "orden_id": None, "fecha_extraccion": datetime.now()})
    r = db.vehiculos().insert_one(doc)
    return jsonify({"ok": True, "id": str(r.inserted_id)}), 201


@bp.route("/api/admin/vehiculos/<vid>", methods=["PUT"])
@manejar_errores_mongo
def editar_vehiculo(vid):
    oid, v = obtener_o_404(db.vehiculos(), vid)
    if not v:
        return error("Vehículo no encontrado.", 404)
    cambios, errores = validar_vehiculo(request.get_json(silent=True) or {}, parcial=True)
    if errores:
        return error("Revisa los datos del vehículo.", 400, errores)
    if not cambios:
        return error("No hay cambios para guardar.")
    db.vehiculos().update_one({"_id": oid}, {"$set": cambios})
    return jsonify({"ok": True})


@bp.route("/api/admin/vehiculos/<vid>/etiquetas", methods=["POST"])
@manejar_errores_mongo
def agregar_etiqueta(vid):
    """$push al array etiquetas (sin repetir y con un máximo de 15)."""
    oid = a_object_id(vid)
    etiqueta = str((request.get_json(silent=True) or {}).get("etiqueta", "")).strip().lower()
    if not 1 <= len(etiqueta) <= 30:
        return error("La etiqueta debe tener entre 1 y 30 caracteres.", 400, {"etiqueta": "Inválida."})
    r = db.vehiculos().update_one(
        {"_id": oid, "etiquetas": {"$ne": etiqueta}, "etiquetas.14": {"$exists": False}},
        {"$push": {"etiquetas": etiqueta}})
    if r.matched_count == 0:
        return error("La etiqueta ya existe o el vehículo ya tiene 15 etiquetas.", 409)
    return jsonify({"ok": True})


@bp.route("/api/admin/vehiculos/<vid>/etiquetas/<etiqueta>", methods=["DELETE"])
def quitar_etiqueta(vid, etiqueta):
    """$pull del array etiquetas."""
    r = db.vehiculos().update_one({"_id": a_object_id(vid)}, {"$pull": {"etiquetas": etiqueta}})
    if r.modified_count == 0:
        return error("Esa etiqueta no estaba en el vehículo.", 404)
    return jsonify({"ok": True})


@bp.route("/api/admin/vehiculos/<vid>", methods=["DELETE"])
def eliminar_vehiculo(vid):
    """Integridad: no se borra un vehículo que aparece en alguna orden."""
    oid, v = obtener_o_404(db.vehiculos(), vid)
    if not v:
        return error("Vehículo no encontrado.", 404)
    usos = db.ordenes().count_documents({"items.vehiculo_id": oid})
    if usos:
        return error(f"No se puede eliminar: el vehículo aparece en {usos} orden(es). "
                     "Primero quítalo de esas órdenes o elimínalas.", 409)
    db.vehiculos().delete_one({"_id": oid})
    return jsonify({"ok": True})


# ================================================================ CLIENTES
def validar_cliente_admin(data, parcial=False):
    errores, doc = {}, {}

    def hay(k):
        return not parcial or k in data

    if hay("nombre"):
        n = str(data.get("nombre", "")).strip()
        if len(n) < 3: errores["nombre"] = "Escribe el nombre completo."
        doc["nombre"] = n
    if hay("documento"):
        d = re.sub(r"\D", "", str(data.get("documento", "")))
        if not 5 <= len(d) <= 12: errores["documento"] = "Documento inválido (5 a 12 números)."
        doc["documento"] = d
    if hay("email"):
        e = str(data.get("email", "")).strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e): errores["email"] = "Correo inválido."
        doc["email"] = e
    if hay("telefono"):
        t = re.sub(r"\D", "", str(data.get("telefono", "")))
        if not 7 <= len(t) <= 12: errores["telefono"] = "Teléfono inválido (7 a 12 números)."
        doc["telefono"] = t
    # Dirección embebida: en una edición se actualiza campo por campo con notación de punto
    for k in ("direccion", "ciudad", "departamento"):
        if hay(k):
            val = str(data.get(k, "")).strip()
            if k in ("direccion", "ciudad") and len(val) < 3:
                errores[k] = "Obligatorio."
            doc[f"direccion.{k}"] = val
    return doc, errores


def embeber_direccion(doc):
    """Convierte {'direccion.ciudad': x, ...} en {'direccion': {'ciudad': x, ...}} para insertar."""
    sub = {k.split(".", 1)[1]: doc.pop(k) for k in list(doc) if k.startswith("direccion.")}
    doc["direccion"] = sub
    return doc


@bp.route("/api/admin/clientes")
def listar_clientes():
    """Lista con $lookup a ordenes para mostrar cuántas compras y cuánto ha gastado cada cliente."""
    f = {}
    if request.args.get("q"):
        rx = regex_busqueda(request.args["q"])
        f["$or"] = [{"nombre": rx}, {"documento": rx}, {"email": rx}, {"direccion.ciudad": rx}]
    pagina = max(int(request.args.get("pagina", 1) or 1), 1)
    total = db.clientes().count_documents(f)
    docs = db.clientes().aggregate([
        {"$match": f},
        {"$sort": {"fecha_registro": -1}},
        {"$skip": (pagina - 1) * POR_PAGINA},
        {"$limit": POR_PAGINA},
        {"$lookup": {"from": "ordenes", "localField": "_id", "foreignField": "cliente_id", "as": "compras",
                     "pipeline": [{"$match": {"estado": "pagada"}}, {"$project": {"total": 1}}]}},
        {"$addFields": {"num_ordenes": {"$size": "$compras"}, "total_comprado": {"$sum": "$compras.total"}}},
        {"$project": {"compras": 0}},
    ])
    return jsonify({"items": [serializar(d) for d in docs], "total": total, "pagina": pagina,
                    "paginas": max(math.ceil(total / POR_PAGINA), 1)})


@bp.route("/api/admin/clientes/<cid>")
def detalle_cliente(cid):
    """Detalle del cliente con sus órdenes ($lookup por cliente_id, campo indexado)."""
    oid = a_object_id(cid)
    r = list(db.clientes().aggregate([
        {"$match": {"_id": oid}},
        {"$lookup": {"from": "ordenes", "localField": "_id", "foreignField": "cliente_id", "as": "ordenes",
                     "pipeline": [{"$sort": {"fecha": -1}},
                                  {"$project": {"numero_orden": 1, "fecha": 1, "estado": 1, "total": 1,
                                                "cantidad": 1, "direccion_entrega": 1}}]}},
    ])) if oid else []
    if not r:
        return error("Cliente no encontrado.", 404)
    return jsonify(serializar(r[0]))


@bp.route("/api/admin/clientes", methods=["POST"])
@manejar_errores_mongo
def crear_cliente():
    doc, errores = validar_cliente_admin(request.get_json(silent=True) or {})
    if errores:
        return error("Revisa los datos del cliente.", 400, errores)
    doc = embeber_direccion(doc)
    doc["fecha_registro"] = datetime.now()
    r = db.clientes().insert_one(doc)
    return jsonify({"ok": True, "id": str(r.inserted_id)}), 201


@bp.route("/api/admin/clientes/<cid>", methods=["PUT"])
@manejar_errores_mongo
def editar_cliente(cid):
    oid, c = obtener_o_404(db.clientes(), cid)
    if not c:
        return error("Cliente no encontrado.", 404)
    cambios, errores = validar_cliente_admin(request.get_json(silent=True) or {}, parcial=True)
    if errores:
        return error("Revisa los datos del cliente.", 400, errores)
    if not cambios:
        return error("No hay cambios para guardar.")
    db.clientes().update_one({"_id": oid}, {"$set": cambios})
    # Mantener al día la copia del nombre/correo dentro de sus órdenes
    copia = {f"cliente.{k}": cambios[k] for k in ("nombre", "email", "documento") if k in cambios}
    if copia:
        db.ordenes().update_many({"cliente_id": oid}, {"$set": copia})
    return jsonify({"ok": True})


@bp.route("/api/admin/clientes/<cid>", methods=["DELETE"])
def eliminar_cliente(cid):
    """Integridad: no se borra un cliente con órdenes registradas."""
    oid, c = obtener_o_404(db.clientes(), cid)
    if not c:
        return error("Cliente no encontrado.", 404)
    n = db.ordenes().count_documents({"cliente_id": oid})
    if n:
        return error(f"No se puede eliminar: el cliente tiene {n} orden(es). Elimina primero sus órdenes.", 409)
    db.clientes().delete_one({"_id": oid})
    return jsonify({"ok": True})


# ================================================================ ÓRDENES
@bp.route("/api/admin/ordenes")
def listar_ordenes():
    f = {}
    if request.args.get("q"):
        rx = regex_busqueda(request.args["q"])
        f["$or"] = [{"numero_orden": rx}, {"cliente.nombre": rx}, {"direccion_entrega.ciudad": rx}]
    if request.args.get("estado"):
        f["estado"] = request.args["estado"]
    return jsonify(paginar(db.ordenes(), f, [("fecha", -1)]))


@bp.route("/api/admin/ordenes/<oid_txt>")
def detalle_orden(oid_txt):
    """Detalle con $lookup al cliente (referencia cliente_id) y a los vehículos de los ítems."""
    oid = a_object_id(oid_txt)
    r = list(db.ordenes().aggregate([
        {"$match": {"_id": oid}},
        {"$lookup": {"from": "clientes", "localField": "cliente_id", "foreignField": "_id", "as": "cliente_actual"}},
        {"$unwind": {"path": "$cliente_actual", "preserveNullAndEmptyArrays": True}},
        {"$lookup": {"from": "vehiculos", "localField": "items.vehiculo_id", "foreignField": "_id",
                     "as": "vehiculos_actuales",
                     "pipeline": [{"$project": {"title": 1, "estado": 1, "price": 1}}]}},
    ])) if oid else []
    if not r:
        return error("Orden no encontrada.", 404)
    return jsonify(serializar(r[0]))


@bp.route("/api/admin/ordenes/<oid_txt>", methods=["PUT"])
@manejar_errores_mongo
def editar_orden(oid_txt):
    """Cambiar el estado (pagada/cancelada) o la dirección de entrega embebida."""
    oid, o = obtener_o_404(db.ordenes(), oid_txt)
    if not o:
        return error("Orden no encontrada.", 404)
    data = request.get_json(silent=True) or {}
    cambios, errores = {}, {}
    for k in ("direccion", "ciudad", "departamento"):
        if k in data:
            val = str(data[k]).strip()
            if k != "departamento" and len(val) < 3:
                errores[k] = "Obligatorio."
            cambios[f"direccion_entrega.{k}"] = val
    nuevo_estado = data.get("estado")
    ids = [it["vehiculo_id"] for it in o.get("items", [])]
    if nuevo_estado and nuevo_estado != o.get("estado"):
        if nuevo_estado == "cancelada":
            liberar_vehiculos(ids)
        elif nuevo_estado == "pagada":
            ocupados = db.vehiculos().count_documents({"_id": {"$in": ids}, "estado": "vendido",
                                                       "orden_id": {"$ne": oid}})
            if ocupados:
                return error("No se puede reactivar: algún vehículo ya fue vendido en otra orden.", 409)
            db.vehiculos().update_many({"_id": {"$in": ids}},
                                       {"$set": {"estado": "vendido", "orden_id": oid}})
        else:
            errores["estado"] = "Estado inválido."
        cambios["estado"] = nuevo_estado
    if errores:
        return error("Revisa los datos de la orden.", 400, errores)
    if not cambios:
        return error("No hay cambios para guardar.")
    db.ordenes().update_one({"_id": oid}, {"$set": cambios})
    return jsonify({"ok": True})


@bp.route("/api/admin/ordenes/<oid_txt>/items/<vid>", methods=["DELETE"])
def quitar_item(oid_txt, vid):
    """$pull de un vehículo del array items; recalcula el total con $inc y libera el vehículo."""
    oid, o = obtener_o_404(db.ordenes(), oid_txt)
    vehiculo_id = a_object_id(vid)
    if not o:
        return error("Orden no encontrada.", 404)
    item = next((it for it in o.get("items", []) if it.get("vehiculo_id") == vehiculo_id), None)
    if not item:
        return error("Ese vehículo no está en la orden.", 404)
    if len(o["items"]) == 1:
        return error("La orden debe tener al menos un vehículo. Cancélala o elimínala.", 409)
    db.ordenes().update_one({"_id": oid}, {"$pull": {"items": {"vehiculo_id": vehiculo_id}},
                                           "$inc": {"total": -item.get("price", 0), "cantidad": -1}})
    if o.get("estado") == "pagada":
        liberar_vehiculos([vehiculo_id])
    return jsonify({"ok": True})


@bp.route("/api/admin/ordenes/<oid_txt>", methods=["DELETE"])
def eliminar_orden(oid_txt):
    """Gestiona los dependientes: los vehículos de la orden vuelven a estar disponibles."""
    oid, o = obtener_o_404(db.ordenes(), oid_txt)
    if not o:
        return error("Orden no encontrada.", 404)
    ids = [it["vehiculo_id"] for it in o.get("items", [])]
    db.vehiculos().update_many({"_id": {"$in": ids}, "orden_id": oid},
                               {"$set": {"estado": "disponible", "orden_id": None},
                                "$unset": {"fecha_venta": ""}})
    db.ordenes().delete_one({"_id": oid})
    return jsonify({"ok": True})
