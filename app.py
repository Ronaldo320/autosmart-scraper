"""
AutoSmart DB — tienda de vehículos con MongoDB.

Ejecutar:  python app.py              (scrapea si hay menos de la meta de autos)
           python app.py --scrape     (fuerza a buscar más autos)
           python app.py --no-scrape  (solo la aplicación web)
"""
import multiprocessing
import os
import sys

from flask import Flask
from flask_cors import CORS

import admin
import config
import db
import reportes
import scraper
import tienda

VERSION = "3.0 (proyecto final: CRUD + aggregate + $jsonSchema)"


def crear_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    CORS(app, supports_credentials=True)
    app.register_blueprint(tienda.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(reportes.bp)
    return app


app = crear_app()


def verificar_plantillas():
    """Evita correr con plantillas viejas sin darse cuenta."""
    carpeta = os.path.join(config.CARPETA_APP, "templates")
    faltan = [p for p in ("index.html", "dashboard.html", "admin.html", "reportes.html", "vehiculo.html")
              if not os.path.exists(os.path.join(carpeta, p))]
    if faltan:
        print("\n" + "!" * 60 + f"\n [X] Faltan plantillas en {carpeta}: {', '.join(faltan)}\n" + "!" * 60)
    return not faltan


def preparar_base_de_datos():
    """Migra datos de versiones anteriores, aplica $jsonSchema e índices."""
    scraper.limpiar_fuentes_excluidas()
    cambios = db.migrar_modelo()
    if cambios:
        print(f"[🔧] Migrados {cambios} documentos al modelo actual.")
    db.configurar_bd()
    print(f"[✔] Esquemas $jsonSchema e índices listos ({len(db.INDICES)} índices).")


if __name__ == "__main__":
    print("\n" + "=" * 55)
    print(f" AUTOSMART v{VERSION}")
    print(f" Ejecutando: {os.path.abspath(__file__)}")
    print("=" * 55)
    if not verificar_plantillas():
        sys.exit(1)

    preparar_base_de_datos()
    faltan_fotos = scraper.reparar_imagenes()
    cantidad = scraper.total_autos()
    base = db.get_db()
    print(f"ESTADO DE LA BASE DE DATOS: {base.vehiculos.count_documents({})} vehículos ({cantidad} del scraper), "
          f"{base.clientes.count_documents({})} clientes, {base.ordenes.count_documents({})} órdenes "
          f"(meta del scraper: {scraper.META_VEHICULOS}).")

    forzar = "--scrape" in sys.argv
    if "--no-scrape" in sys.argv:
        print(" Scraper desactivado (--no-scrape).")
    elif cantidad < scraper.META_VEHICULOS or forzar:
        print(" Iniciando motor de extracción en segundo plano (la página ya funciona mientras tanto)...")
        meta = cantidad + scraper.META_VEHICULOS if forzar else scraper.META_VEHICULOS
        multiprocessing.Process(target=scraper.motor_principal, args=(meta,)).start()
    else:
        print(" Ya se alcanzó la meta. Usa 'python app.py --scrape' para buscar autos nuevos.")
        if faltan_fotos:
            multiprocessing.Process(target=scraper.trabajador_fotos).start()

    print("\n Catálogo:  http://127.0.0.1:5000")
    print(" Admin:     http://127.0.0.1:5000/admin")
    print(" Reportes:  http://127.0.0.1:5000/reportes")
    print(" Dashboard: http://127.0.0.1:5000/dashboard\n")
    app.run(debug=True, port=5000, use_reloader=False)
