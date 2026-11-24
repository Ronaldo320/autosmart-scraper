"""
Reportes con el framework de agregación de MongoDB.

Cada reporte responde una pregunta de negocio. Entre todos usan:
$match, $group, $sort, $lookup, $unwind, $limit, $project y $dateToString,
con los acumuladores $sum, $avg, $min y $max.
"""
import re
from datetime import datetime, timedelta

from bson import json_util
from flask import Blueprint, jsonify, render_template, request

import db

bp = Blueprint("reportes", __name__)


def _fecha(texto, por_defecto):
    try:
        return datetime.strptime(texto, "%Y-%m-%d")
    except (TypeError, ValueError):
        return por_defecto


def _rango():
    hasta = _fecha(request.args.get("hasta"), datetime.now()).replace(hour=23, minute=59, second=59, microsecond=0)
    desde = _fecha(request.args.get("desde"), hasta - timedelta(days=365)).replace(hour=0, minute=0, second=0, microsecond=0)
    return desde, hasta


# --------------------------------------------------------------- pipelines
def p_ventas_por_marca():
    desde, hasta = _rango()
    return "ordenes", [
        {"$match": {"estado": "pagada", "fecha": {"$gte": desde, "$lte": hasta}}},
        {"$unwind": "$items"},
        {"$lookup": {"from": "vehiculos", "localField": "items.vehiculo_id",
                     "foreignField": "_id", "as": "vehiculo"}},
        {"$unwind": "$vehiculo"},
        {"$group": {"_id": "$vehiculo.marca",
                    "unidades": {"$sum": 1},
                    "ingresos": {"$sum": "$items.price"},
                    "precio_promedio": {"$avg": "$items.price"}}},
        {"$sort": {"ingresos": -1}},
    ]


def p_promedio_por_ciudad():
    desde, hasta = _rango()
    return "ordenes", [
        {"$match": {"estado": "pagada", "fecha": {"$gte": desde, "$lte": hasta}}},
        {"$group": {"_id": "$direccion_entrega.ciudad",
                    "ordenes": {"$sum": 1},
                    "valor_promedio": {"$avg": "$total"},
                    "valor_minimo": {"$min": "$total"},
                    "valor_maximo": {"$max": "$total"}}},
        {"$sort": {"valor_promedio": -1}},
    ]


def p_top_clientes():
    n = min(max(int(request.args.get("top", 5) or 5), 1), 20)
    return "ordenes", [
        {"$match": {"estado": "pagada"}},
        {"$group": {"_id": "$cliente_id",
                    "total_comprado": {"$sum": "$total"},
                    "ordenes": {"$sum": 1},
                    "vehiculos": {"$sum": "$cantidad"}}},
        {"$sort": {"total_comprado": -1}},
        {"$limit": n},
        {"$lookup": {"from": "clientes", "localField": "_id", "foreignField": "_id", "as": "cliente"}},
        {"$unwind": "$cliente"},
        {"$project": {"_id": 0, "cliente": "$cliente.nombre", "documento": "$cliente.documento",
                      "ciudad": "$cliente.direccion.ciudad", "ordenes": 1, "vehiculos": 1,
                      "total_comprado": 1}},
    ]


def p_ventas_por_mes():
    desde, hasta = _rango()
    return "ordenes", [
        {"$match": {"estado": "pagada", "fecha": {"$gte": desde, "$lte": hasta}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m", "date": "$fecha",
                                              "timezone": "America/Bogota"}},
                    "ordenes": {"$sum": 1},
                    "vehiculos": {"$sum": "$cantidad"},
                    "ingresos": {"$sum": "$total"}}},
        {"$sort": {"_id": 1}},
    ]


def p_precio_por_marca():
    condicion = request.args.get("condition") if request.args.get("condition") in ("nuevo", "usado") else None
    filtro = {"price": {"$gt": 0}, "estado": "disponible"}
    filtro = {"condition": condicion, **filtro} if condicion else filtro
    return "vehiculos", [
        {"$match": filtro},
        {"$group": {"_id": "$marca",
                    "vehiculos": {"$sum": 1},
                    "precio_promedio": {"$avg": "$price"},
                    "precio_minimo": {"$min": "$price"},
                    "precio_maximo": {"$max": "$price"}}},
        {"$match": {"vehiculos": {"$gte": 3}}},
        {"$sort": {"precio_promedio": -1}},
        {"$limit": 15},
    ]


REPORTES = {
    "ventas-por-marca": {
        "titulo": "Ventas por marca",
        "tipo": "Totales por categoría",
        "pregunta": "¿Cuánto se ha vendido de cada marca y cuántas unidades?",
        "builder": p_ventas_por_marca, "filtros": ["fechas"],
        "columnas": [["_id", "Marca", "texto"], ["unidades", "Unidades", "numero"],
                     ["ingresos", "Ingresos", "dinero"], ["precio_promedio", "Precio promedio", "dinero"]],
        "grafico": {"etiqueta": "_id", "valor": "ingresos", "tipo": "barra_h", "formato": "dinero"},
        "interpretacion": "Marcas ordenadas por ingresos. Une cada ítem vendido con su vehículo ($lookup) "
                          "para obtener la marca actual del catálogo.",
    },
    "promedio-por-ciudad": {
        "titulo": "Valor promedio de las órdenes por ciudad",
        "tipo": "Promedios",
        "pregunta": "¿Cuál es el valor promedio de las compras según la ciudad de entrega?",
        "builder": p_promedio_por_ciudad, "filtros": ["fechas"],
        "columnas": [["_id", "Ciudad", "texto"], ["ordenes", "Órdenes", "numero"],
                     ["valor_promedio", "Promedio", "dinero"], ["valor_minimo", "Mínimo", "dinero"],
                     ["valor_maximo", "Máximo", "dinero"]],
        "grafico": {"etiqueta": "_id", "valor": "valor_promedio", "tipo": "barra_h", "formato": "dinero"},
        "interpretacion": "Agrupa por un campo del documento embebido direccion_entrega. Sirve para "
                          "decidir en qué ciudades concentrar la publicidad de autos de mayor valor.",
    },
    "top-clientes": {
        "titulo": "Top clientes",
        "tipo": "Ranking (Top N)",
        "pregunta": "¿Quiénes son los clientes que más han comprado?",
        "builder": p_top_clientes, "filtros": ["top"],
        "columnas": [["cliente", "Cliente", "texto"], ["documento", "Documento", "texto"],
                     ["ciudad", "Ciudad", "texto"], ["ordenes", "Órdenes", "numero"],
                     ["vehiculos", "Vehículos", "numero"], ["total_comprado", "Total comprado", "dinero"]],
        "grafico": {"etiqueta": "cliente", "valor": "total_comprado", "tipo": "barra_h", "formato": "dinero"},
        "interpretacion": "Agrupa las órdenes por la referencia cliente_id, se queda con los N mayores "
                          "y solo entonces hace el $lookup a clientes (une pocos documentos).",
    },
    "ventas-por-mes": {
        "titulo": "Ventas mes a mes",
        "tipo": "Análisis por fechas",
        "pregunta": "¿Cómo evolucionan las ventas mes a mes?",
        "builder": p_ventas_por_mes, "filtros": ["fechas"],
        "columnas": [["_id", "Mes", "texto"], ["ordenes", "Órdenes", "numero"],
                     ["vehiculos", "Vehículos", "numero"], ["ingresos", "Ingresos", "dinero"]],
        "grafico": {"etiqueta": "_id", "valor": "ingresos", "tipo": "barra", "formato": "dinero"},
        "interpretacion": "Filtra por rango de fechas (índice estado_fecha) y agrupa por mes con "
                          "$dateToString en hora de Colombia.",
    },
    "precio-por-marca": {
        "titulo": "Precio promedio del inventario por marca",
        "tipo": "Promedios",
        "pregunta": "¿Qué marcas tienen el precio promedio más alto en el inventario disponible?",
        "builder": p_precio_por_marca, "filtros": ["condicion"],
        "columnas": [["_id", "Marca", "texto"], ["vehiculos", "Vehículos", "numero"],
                     ["precio_promedio", "Promedio", "dinero"], ["precio_minimo", "Mínimo", "dinero"],
                     ["precio_maximo", "Máximo", "dinero"]],
        "grafico": {"etiqueta": "_id", "valor": "precio_promedio", "tipo": "barra_h", "formato": "dinero"},
        "interpretacion": "Trabaja sobre los +1000 vehículos del catálogo. Solo marcas con 3 o más "
                          "vehículos, para que el promedio sea representativo.",
    },
}


def indices_usados(coleccion, pipeline):
    """Pregunta a MongoDB (explain) qué índices usó la consulta."""
    try:
        plan = db.get_db().command("explain", {"aggregate": coleccion, "pipeline": pipeline, "cursor": {}},
                                   verbosity="queryPlanner")
        texto = json_util.dumps(plan)
        return sorted(set(re.findall(r'"indexName": "([^"]+)"', texto)))
    except Exception:
        return []


def pipeline_legible(pipeline):
    """Pipeline en formato de mongosh: fechas como ISODate(...) y ids como ObjectId(...)."""
    texto = json_util.dumps(pipeline, indent=2, ensure_ascii=False, json_options=json_util.RELAXED_JSON_OPTIONS)
    texto = re.sub(r'\{\s*"\$date":\s*"([^"]+)"\s*\}', r'ISODate("\1")', texto)
    return re.sub(r'\{\s*"\$oid":\s*"([^"]+)"\s*\}', r'ObjectId("\1")', texto)


@bp.route("/reportes")
def pagina():
    return render_template("reportes.html")


@bp.route("/api/reportes")
def catalogo():
    return jsonify([{"id": k, **{c: v[c] for c in ("titulo", "tipo", "pregunta", "filtros")}}
                    for k, v in REPORTES.items()])


@bp.route("/api/reportes/<rid>")
def ejecutar(rid):
    rep = REPORTES.get(rid)
    if not rep:
        return jsonify({"error": "Reporte no existe"}), 404
    coleccion, pipeline = rep["builder"]()
    inicio = datetime.now()
    resultados = list(db.get_db()[coleccion].aggregate(pipeline))
    ms = int((datetime.now() - inicio).total_seconds() * 1000)
    return jsonify({
        "id": rid, "titulo": rep["titulo"], "tipo": rep["tipo"], "pregunta": rep["pregunta"],
        "coleccion": coleccion, "columnas": rep["columnas"], "grafico": rep["grafico"],
        "interpretacion": rep["interpretacion"],
        # Pipeline exacto enviado a MongoDB, en formato legible (fechas como ISODate)
        "pipeline": pipeline_legible(pipeline),
        "resultados": [{k: (str(v) if k == "_id" and v is not None and not isinstance(v, str) else v)
                        for k, v in r.items()} for r in resultados],
        "indices": indices_usados(coleccion, pipeline),
        "ms": ms,
    })
