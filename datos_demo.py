"""
Genera clientes y órdenes de demostración usando vehículos reales del catálogo,
para que los reportes de ventas tengan datos. Todo lo que crea queda marcado con demo: true.

    python datos_demo.py                     # 80 clientes y 220 órdenes en los últimos 12 meses
    python datos_demo.py --clientes 50 --ordenes 150
    python datos_demo.py --borrar            # elimina los datos demo y libera los vehículos
"""
import argparse
import random
import secrets
from datetime import datetime, timedelta

from bson import ObjectId

import db
from db import formato_cop

NOMBRES = ["Andrés", "Camila", "Juan", "Valentina", "Carlos", "Daniela", "Luis", "María", "Jorge", "Laura",
           "Santiago", "Natalia", "Diego", "Paola", "Sebastián", "Carolina", "Felipe", "Juliana", "Ricardo",
           "Mariana", "Alejandro", "Sofía", "Óscar", "Diana", "Javier", "Ana", "Mauricio", "Isabel"]
APELLIDOS = ["Gómez", "Rodríguez", "Martínez", "Pérez", "García", "López", "Hernández", "Díaz", "Torres",
             "Ramírez", "Vargas", "Castro", "Rojas", "Moreno", "Ortiz", "Jiménez", "Mendoza", "Herrera",
             "Barrios", "Polo", "De la Hoz", "Pacheco", "Orozco", "Navarro"]
CIUDADES = [("Barranquilla", "Atlántico", 18), ("Cartagena", "Bolívar", 14), ("Bogotá", "Cundinamarca", 20),
            ("Medellín", "Antioquia", 14), ("Cali", "Valle del Cauca", 10), ("Santa Marta", "Magdalena", 7),
            ("Bucaramanga", "Santander", 6), ("Montería", "Córdoba", 5), ("Valledupar", "Cesar", 4),
            ("Sincelejo", "Sucre", 3), ("Pereira", "Risaralda", 3)]
METODOS = ["tarjeta"] * 5 + ["pse"] * 4 + ["transferencia"] * 2
SIN_TILDES = str.maketrans("áéíóúñÁÉÍÓÚÑ ", "aeiounAEIOUN.")


def crear_clientes(n, ahora):
    docs, usados = [], set(db.clientes().distinct("documento"))
    for _ in range(n):
        nombre = f"{random.choice(NOMBRES)} {random.choice(APELLIDOS)} {random.choice(APELLIDOS)}"
        ciudad, depto, _ = random.choices(CIUDADES, weights=[c[2] for c in CIUDADES])[0]
        doc = str(random.randint(10_000_000, 1_199_999_999))
        while doc in usados:
            doc = str(random.randint(10_000_000, 1_199_999_999))
        usados.add(doc)
        partes = nombre.lower().translate(SIN_TILDES).split(".")
        docs.append({
            "nombre": nombre, "documento": doc,
            "email": f"{partes[0]}.{partes[1]}{random.randint(1, 99)}@correo.com",
            "telefono": f"3{random.randint(0, 2)}{random.randint(10_000_000, 99_999_999)}",
            "direccion": {"direccion": f"Calle {random.randint(1, 120)} # {random.randint(1, 90)}-{random.randint(1, 99)}",
                          "ciudad": ciudad, "departamento": depto},
            "fecha_registro": ahora - timedelta(days=random.randint(200, 420)),
            "demo": True,
        })
    db.clientes().insert_many(docs)
    return list(db.clientes().find({"demo": True}))


def crear_ordenes(n, clientes, ahora):
    disponibles = list(db.vehiculos().find({"estado": "disponible", "price": {"$gt": 0}},
                                           {"title": 1, "marca": 1, "price": 1, "image": 1, "source": 1,
                                            "condition": 1, "url": 1, "year": 1, "mileage": 1}))
    random.shuffle(disponibles)
    # Unos clientes compran más que otros (para que el Top N tenga sentido)
    pesos = [random.choice([1, 1, 1, 1, 2, 2, 3, 5]) for _ in clientes]
    creadas = 0
    for _ in range(n):
        if len(disponibles) < 2:
            break
        cliente = random.choices(clientes, weights=pesos)[0]
        items_v = [disponibles.pop() for _ in range(random.choices([1, 2], weights=[85, 15])[0])]
        fecha = ahora - timedelta(days=random.randint(0, 364), hours=random.randint(0, 23),
                                  minutes=random.randint(0, 59))
        estado = "cancelada" if random.random() < 0.08 else "pagada"
        oid = ObjectId()
        items = [{"vehiculo_id": v["_id"], "title": v["title"], "marca": v.get("marca", ""), "price": v["price"],
                  "price_text": formato_cop(v["price"]), "image": v.get("image", ""), "source": v.get("source", ""),
                  "condition": v.get("condition", "usado"), "url": v.get("url", ""),
                  "year": v.get("year", "N/A"), "mileage": v.get("mileage", "N/A")} for v in items_v]
        db.ordenes().insert_one({
            "_id": oid,
            "numero_orden": f"AS-{fecha:%Y%m%d}-{secrets.token_hex(3).upper()}",
            "fecha": fecha,
            "cliente_id": cliente["_id"],
            "cliente": {"nombre": cliente["nombre"], "email": cliente["email"], "documento": cliente["documento"]},
            "direccion_entrega": dict(cliente["direccion"]),
            "items": items,
            "cantidad": len(items),
            "total": sum(i["price"] for i in items),
            "metodo_pago": random.choice(METODOS),
            "referencia_pago": f"SIM-{secrets.token_hex(4).upper()}",
            "estado": estado,
            "demo": True,
        })
        if estado == "pagada":
            db.vehiculos().update_many({"_id": {"$in": [v["_id"] for v in items_v]}},
                                       {"$set": {"estado": "vendido", "orden_id": oid, "fecha_venta": fecha}})
        else:
            disponibles.extend(items_v)
        creadas += 1
    return creadas


def borrar():
    ids = [o["_id"] for o in db.ordenes().find({"demo": True}, {"_id": 1})]
    db.vehiculos().update_many({"orden_id": {"$in": ids}},
                               {"$set": {"estado": "disponible", "orden_id": None}, "$unset": {"fecha_venta": ""}})
    o = db.ordenes().delete_many({"demo": True}).deleted_count
    c = db.clientes().delete_many({"demo": True}).deleted_count
    print(f"Eliminadas {o} órdenes y {c} clientes de demostración. Vehículos liberados.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clientes", type=int, default=80)
    ap.add_argument("--ordenes", type=int, default=220)
    ap.add_argument("--borrar", action="store_true")
    a = ap.parse_args()
    if a.borrar:
        borrar()
    else:
        if db.ordenes().count_documents({"demo": True}):
            print("Ya hay datos demo. Bórralos primero con: python datos_demo.py --borrar")
        else:
            ahora = datetime.now()
            clientes = crear_clientes(a.clientes, ahora)
            n = crear_ordenes(a.ordenes, clientes, ahora)
            print(f"Creados {len(clientes)} clientes y {n} órdenes de demostración.")
            print(f"Totales: {db.vehiculos().count_documents({})} vehículos, {db.clientes().count_documents({})} "
                  f"clientes, {db.ordenes().count_documents({})} órdenes.")
