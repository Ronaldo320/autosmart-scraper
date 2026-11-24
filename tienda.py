"""
Tienda: catálogo, carrito (en la sesión), checkout con pago simulado y dashboard de ventas.
"""
import re
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request, session
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

import db
from db import a_object_id, formato_cop, serializar

bp = Blueprint("tienda", __name__)

MAX_ITEMS_CARRITO = 10
METODOS_PAGO = {
    "tarjeta": "Tarjeta crédito/débito (simulado)",
    "pse": "PSE (simulado)",
    "transferencia": "Transferencia bancaria (simulado)",
}
NOMBRES_CONDICION = {"nuevo": "Nuevos", "usado": "Usados"}


# ----------------------------------------------------------------- páginas
@bp.route("/")
def home():
    return render_template("index.html")


@bp.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@bp.route("/vehiculo/<vehiculo_id>")
def pagina_vehiculo(vehiculo_id):
    return render_template("vehiculo.html", vehiculo_id=vehiculo_id)


# ----------------------------------------------------------------- catálogo
@bp.route("/api/vehicles")
def get_vehicles():
    todos = list(db.vehiculos().find({}, {"especificaciones": 0}).sort("_id", -1).limit(5000))
    todos = [serializar(v) for v in todos]
    precios = [c["price"] for c in todos if (c.get("price") or 0) > 0]
    stats = {
        "total": len(todos),
        "disponibles": sum(1 for c in todos if c.get("estado") != "vendido"),
        "price_min": min(precios) if precios else 0,
        "price_max": max(precios) if precios else 0,
        "price_avg": sum(precios) // len(precios) if precios else 0,
    }
    return jsonify({"vehicles": todos, "stats": stats})


@bp.route("/api/vehiculos/<vehiculo_id>")
def detalle_publico(vehiculo_id):
    oid = a_object_id(vehiculo_id)
    v = db.vehiculos().find_one({"_id": oid}) if oid else None
    if not v:
        return jsonify({"error": "Vehículo no encontrado"}), 404
    return jsonify(serializar(v))


def es_comprable(v):
    return v.get("estado") != "vendido" and (v.get("price") or 0) > 0


# ----------------------------------------------------------------- carrito
def ids_carrito():
    return list(session.get("carrito", []))


def construir_carrito():
    ids = ids_carrito()
    oids = [o for o in (a_object_id(i) for i in ids) if o]
    docs = {str(d["_id"]): d for d in db.vehiculos().find({"_id": {"$in": oids}})}
    items, validos = [], []
    for i in ids:
        v = docs.get(i)
        if not v:
            continue
        validos.append(i)
        items.append({
            "id": i, "title": v.get("title", ""), "marca": v.get("marca", ""),
            "price": v.get("price", 0), "price_text": formato_cop(v.get("price", 0)),
            "image": v.get("image", ""), "source": v.get("source", ""), "url": v.get("url", ""),
            "year": v.get("year", "N/A"), "mileage": v.get("mileage", "N/A"),
            "condition": v.get("condition", "usado"), "disponible": es_comprable(v),
        })
    if len(validos) != len(ids):
        session["carrito"] = validos
    total = sum(it["price"] for it in items if it["disponible"])
    return {"items": items, "count": len(items), "total": total, "total_text": formato_cop(total)}


@bp.route("/api/cart", methods=["GET"])
def ver_carrito():
    return jsonify(construir_carrito())


@bp.route("/api/cart", methods=["POST"])
def agregar_carrito():
    data = request.get_json(silent=True) or {}
    oid = a_object_id(data.get("vehicle_id"))
    vehiculo = db.vehiculos().find_one({"_id": oid}) if oid else None
    if not vehiculo:
        return jsonify({"ok": False, "error": "Vehículo no encontrado."}), 404
    if vehiculo.get("estado") == "vendido":
        return jsonify({"ok": False, "error": "Este vehículo ya fue vendido."}), 409
    if (vehiculo.get("price") or 0) <= 0:
        return jsonify({"ok": False, "error": "Este vehículo no tiene precio publicado."}), 400
    ids = ids_carrito()
    vid = str(oid)
    if vid in ids:
        return jsonify({"ok": True, "mensaje": "Ya estaba en tu carrito.", "carrito": construir_carrito()})
    if len(ids) >= MAX_ITEMS_CARRITO:
        return jsonify({"ok": False, "error": f"Máximo {MAX_ITEMS_CARRITO} vehículos por compra."}), 400
    ids.append(vid)
    session["carrito"] = ids
    return jsonify({"ok": True, "mensaje": "Agregado al carrito.", "carrito": construir_carrito()})


@bp.route("/api/cart/<vehicle_id>", methods=["DELETE"])
def quitar_carrito(vehicle_id):
    session["carrito"] = [i for i in ids_carrito() if i != vehicle_id]
    return jsonify({"ok": True, "carrito": construir_carrito()})


@bp.route("/api/cart", methods=["DELETE"])
def vaciar_carrito():
    session["carrito"] = []
    return jsonify({"ok": True, "carrito": construir_carrito()})


# ----------------------------------------------------------------- checkout
def validar_cliente(data):
    """Valida los datos del comprador. Devuelve (cliente, dirección, método, errores)."""
    limpio = lambda k: str(data.get(k, "")).strip()
    nombre, email = limpio("nombre"), limpio("email").lower()
    documento = re.sub(r"\D", "", limpio("documento"))
    telefono = re.sub(r"\D", "", limpio("telefono"))
    direccion, ciudad, departamento = limpio("direccion"), limpio("ciudad"), limpio("departamento")
    metodo = limpio("metodo_pago")

    errores = {}
    if len(nombre) < 3: errores["nombre"] = "Escribe tu nombre completo."
    if not 5 <= len(documento) <= 12: errores["documento"] = "Documento inválido (5 a 12 números)."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): errores["email"] = "Correo inválido."
    if not 7 <= len(telefono) <= 12: errores["telefono"] = "Teléfono inválido (7 a 12 números)."
    if len(direccion) < 5: errores["direccion"] = "Escribe la dirección de entrega."
    if len(ciudad) < 3: errores["ciudad"] = "Indica la ciudad."
    if metodo not in METODOS_PAGO: errores["metodo_pago"] = "Elige un método de pago."

    cliente = {"nombre": nombre, "documento": documento, "email": email, "telefono": telefono}
    dir_entrega = {"direccion": direccion, "ciudad": ciudad, "departamento": departamento}
    return cliente, dir_entrega, metodo, errores


def serializar_orden(o):
    s = serializar(o)
    if isinstance(o.get("fecha"), datetime):
        s["fecha_iso"] = o["fecha"].isoformat()
    s["metodo_pago_texto"] = METODOS_PAGO.get(o.get("metodo_pago"), o.get("metodo_pago"))
    s["total_text"] = formato_cop(o.get("total", 0))
    return s


def registrar_cliente(cliente, direccion, ahora):
    """Busca al cliente por documento (índice único); si no existe lo crea. Devuelve el documento."""
    return db.clientes().find_one_and_update(
        {"documento": cliente["documento"]},
        {"$set": {**cliente, "direccion": direccion},
         "$setOnInsert": {"fecha_registro": ahora}},
        upsert=True, return_document=ReturnDocument.AFTER)


@bp.route("/api/checkout", methods=["POST"])
def checkout():
    data = request.get_json(silent=True) or {}
    cliente, direccion, metodo, errores = validar_cliente(data)
    if errores:
        return jsonify({"ok": False, "error": "Revisa los datos del formulario.", "campos": errores}), 400

    carrito = construir_carrito()
    if not carrito["items"]:
        return jsonify({"ok": False, "error": "Tu carrito está vacío."}), 400
    no_disponibles = [it["title"] for it in carrito["items"] if not it["disponible"]]
    if no_disponibles:
        return jsonify({"ok": False, "error": "Algunos vehículos ya no están disponibles.",
                        "no_disponibles": no_disponibles}), 409

    ahora = datetime.now()
    orden_id = ObjectId()
    numero = f"AS-{ahora:%Y%m%d}-{secrets.token_hex(3).upper()}"
    veh = db.vehiculos()

    # Reserva atómica: solo se marca si nadie lo compró antes
    reservados = []
    for it in carrito["items"]:
        r = veh.update_one({"_id": a_object_id(it["id"]), "estado": {"$ne": "vendido"}},
                           {"$set": {"estado": "vendido", "orden_id": orden_id, "fecha_venta": ahora}})
        if r.matched_count == 0:
            liberar_vehiculos(reservados)
            return jsonify({"ok": False, "error": f"'{it['title']}' acaba de ser vendido."}), 409
        reservados.append(a_object_id(it["id"]))

    try:
        doc_cliente = registrar_cliente(cliente, direccion, ahora)
        orden = {
            "_id": orden_id,
            "numero_orden": numero,
            "fecha": ahora,
            "cliente_id": doc_cliente["_id"],
            "cliente": {"nombre": cliente["nombre"], "email": cliente["email"], "documento": cliente["documento"]},
            "direccion_entrega": direccion,
            "items": [{"vehiculo_id": a_object_id(it["id"]), "title": it["title"], "marca": it["marca"],
                       "price": it["price"], "price_text": it["price_text"], "image": it["image"],
                       "source": it["source"], "condition": it["condition"], "url": it["url"],
                       "year": it["year"], "mileage": it["mileage"]} for it in carrito["items"]],
            "cantidad": len(carrito["items"]),
            "total": carrito["total"],
            "metodo_pago": metodo,
            "referencia_pago": f"SIM-{secrets.token_hex(4).upper()}",
            "estado": "pagada",
        }
        db.ordenes().insert_one(orden)
    except PyMongoError as e:
        liberar_vehiculos(reservados)
        return jsonify({"ok": False, "error": f"No se pudo registrar la compra: {e}"}), 500

    session["carrito"] = []
    print(f"[🛒] Compra exitosa {numero} — {orden['cantidad']} vehículo(s) — {formato_cop(orden['total'])}")
    return jsonify({"ok": True, "orden": serializar_orden(orden)}), 201


def liberar_vehiculos(ids):
    """Devuelve vehículos al estado 'disponible' (compra revertida, cancelada o eliminada)."""
    if ids:
        db.vehiculos().update_many({"_id": {"$in": ids}},
                                   {"$set": {"estado": "disponible", "orden_id": None},
                                    "$unset": {"fecha_venta": ""}})


@bp.route("/api/orders")
def listar_ordenes():
    return jsonify([serializar_orden(o) for o in db.ordenes().find().sort("fecha", -1).limit(100)])


@bp.route("/api/orders/<numero>")
def ver_orden(numero):
    o = db.ordenes().find_one({"numero_orden": numero})
    if not o:
        return jsonify({"error": "Orden no encontrada"}), 404
    return jsonify(serializar_orden(o))


# ----------------------------------------------------------------- dashboard
@bp.route("/api/dashboard")
def datos_dashboard():
    """Todo el dashboard se calcula en MongoDB con aggregate + $facet."""
    hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    desde = hoy - timedelta(days=13)

    r = next(db.ordenes().aggregate([
        {"$match": {"estado": "pagada"}},
        {"$facet": {
            "kpis": [{"$group": {"_id": None, "ingresos": {"$sum": "$total"}, "ordenes": {"$sum": 1},
                                 "vehiculos_vendidos": {"$sum": "$cantidad"}, "ticket": {"$avg": "$total"}}}],
            "por_dia": [
                {"$match": {"fecha": {"$gte": desde}}},
                {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$fecha"}},
                            "ordenes": {"$sum": 1}, "ingresos": {"$sum": "$total"}}}],
            "por_condicion": [
                {"$unwind": "$items"},
                {"$group": {"_id": {"$ifNull": ["$items.condition", "usado"]},
                            "vehiculos": {"$sum": 1}, "ingresos": {"$sum": "$items.price"}}},
                {"$sort": {"vehiculos": -1}}],
            "por_metodo": [{"$group": {"_id": "$metodo_pago", "ordenes": {"$sum": 1}}},
                           {"$sort": {"ordenes": -1}}],
            "ultimas": [{"$sort": {"fecha": -1}}, {"$limit": 10}],
        }},
    ]))

    inventario = list(db.vehiculos().aggregate([
        {"$group": {"_id": "$condition", "total": {"$sum": 1},
                    "vendidos": {"$sum": {"$cond": [{"$eq": ["$estado", "vendido"]}, 1, 0]}},
                    "precio_promedio": {"$avg": {"$cond": [{"$gt": ["$price", 0]}, "$price", None]}}}},
        {"$sort": {"total": -1}},
    ]))

    k = (r["kpis"] or [{}])[0]
    dias_map = {x["_id"]: x for x in r["por_dia"]}
    dias = [desde + timedelta(days=i) for i in range(14)]
    inv = [{"fuente": NOMBRES_CONDICION.get(f["_id"], "Usados"), "total": f["total"],
            "vendidos": f["vendidos"], "disponibles": f["total"] - f["vendidos"],
            "precio_promedio": int(f["precio_promedio"] or 0)} for f in inventario]
    return jsonify({
        "kpis": {
            "ingresos": k.get("ingresos", 0), "ordenes": k.get("ordenes", 0),
            "vehiculos_vendidos": k.get("vehiculos_vendidos", 0),
            "ticket_promedio": int(k.get("ticket") or 0),
            "inventario_total": sum(f["total"] for f in inv),
            "inventario_disponible": sum(f["disponibles"] for f in inv),
        },
        "ventas_por_dia": [{"fecha": d.strftime("%d/%m"),
                            "ordenes": dias_map.get(d.strftime("%Y-%m-%d"), {}).get("ordenes", 0),
                            "ingresos": dias_map.get(d.strftime("%Y-%m-%d"), {}).get("ingresos", 0)} for d in dias],
        "ventas_por_fuente": [{"fuente": NOMBRES_CONDICION.get(x["_id"], "Usados"),
                               "vehiculos": x["vehiculos"], "ingresos": x["ingresos"]} for x in r["por_condicion"]],
        "ventas_por_metodo": [{"metodo": METODOS_PAGO.get(x["_id"], x["_id"]), "ordenes": x["ordenes"]}
                              for x in r["por_metodo"]],
        "inventario": inv,
        "ultimas_ordenes": [serializar_orden(o) for o in r["ultimas"]],
    })
