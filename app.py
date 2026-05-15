from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth 
from pymongo import MongoClient
import multiprocessing
import time
import os

app = Flask(__name__)
CORS(app)

# --- CONEXIÓN A LA BASE DE DATOS ---
def obtener_coleccion():
    client = MongoClient('mongodb://localhost:27017/')
    return client['AutoSmartDB']['vehiculos']

def guardar_bd(vehiculo):
    coleccion = obtener_coleccion()
    if not vehiculo.get("url"): return 
    vehiculo["fecha_extraccion"] = time.strftime("%d/%m/%Y %H:%M:%S")
    coleccion.update_one({"url": vehiculo["url"]}, {"$set": vehiculo}, upsert=True)

# --- SCRAPERS ---
def scrapear_tucarro_masivo(page):
    url_base = "https://carros.mercadolibre.com.co/_ITEM*CONDITION_2230581_NoIndex_True"
    print(f"\n[] TUCARRO: Iniciando barrido en MODO FANTASMA...")
    
    try:
        page.goto(url_base, wait_until="networkidle", timeout=60000)
        time.sleep(4) 

        for pagina in range(1, 11):
            offset = (pagina - 1) * 48 + 1
            if pagina > 1:
                url_pagina = f"https://carros.mercadolibre.com.co/_Desde_{offset}_ITEM*CONDITION_2230581_NoIndex_True"
                page.goto(url_pagina, wait_until="networkidle", timeout=60000)
            
            print(f"\n--- TuCarro: Procesando Página {pagina}/10 ---")
            
            try: 
                page.wait_for_selector(".poly-card, .ui-search-layout__item", timeout=15000)
            except: 
                print(" No se encontraron carros. Puede ser el fin de la lista.")
                break

            for _ in range(8):
                page.mouse.wheel(0, 1000)
                time.sleep(0.8)

            items = page.locator(".poly-card, .ui-search-layout__item").all()
            print(f"[*] Detectados {len(items)} anuncios...")

            guardados = 0
            for i, item in enumerate(items):
                try:
                    titulo_loc = item.locator("h2, h3, .poly-component__title").first
                    titulo = titulo_loc.inner_text(timeout=5000)
                    precio_txt = item.locator(".andes-money-amount__fraction").first.inner_text(timeout=5000)
                    
                    try:
                        enlace_nodo = item.locator("h2").locator("xpath=ancestor::a").first
                        if enlace_nodo.count() == 0:
                            enlace_nodo = item.locator("a").first
                        enlace = enlace_nodo.get_attribute("href", timeout=5000)
                    except: 
                        enlace = ""
                    
                    img_node = item.locator("img").first
                    img_url = img_node.get_attribute("data-src", timeout=5000) or img_node.get_attribute("src", timeout=5000) or ""

                    attrs = item.locator(".poly-attributes-list__item, .ui-search-card-attributes__attribute").all()
                    anio = attrs[0].inner_text(timeout=5000) if len(attrs) > 0 else "N/A"
                    km = attrs[1].inner_text(timeout=5000) if len(attrs) > 1 else "N/A"
                    
                    loc_node = item.locator(".poly-component__location, .ui-search-item__location").first
                    ubicacion = loc_node.inner_text(timeout=5000) if loc_node.is_visible(timeout=5000) else "Colombia"

                    if titulo and enlace: 
                        guardar_bd({
                            "source": "tucarro",
                            "title": titulo,
                            "url": enlace,
                            "price": int(precio_txt.replace(".", "")),
                            "price_text": f"$ {precio_txt}",
                            "image": img_url,
                            "year": anio,
                            "mileage": km,
                            "location": ubicacion,
                            "fuel": "Gasolina/Diésel",
                            "transmission": "Consultar"
                        })
                        guardados += 1
                except Exception as e:
                    print(f"   [!] Anuncio omitido (cargando muy lento o estructura rara).")
                    continue
            
            print(f"✔️ Página {pagina}: {guardados} carros guardados.")
            time.sleep(3) 
                
    except Exception as e: 
        print(f" Error Crítico en TuCarro: {e}")

def scrapear_autocosmos_masivo(page):
    print(f"\n[] AUTOCOSMOS: Iniciando barrido de 10 páginas...")
    try:
        for pagina in range(1, 11):
            url = f"https://www.autocosmos.com.co/auto/usado?pidx={pagina}"
            print(f"\n--- Autocosmos: Procesando Página {pagina}/10 ---")
            
            # 🔥 Volvemos a domcontentloaded para ignorar la publicidad infinita
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Obligamos al bot a esperar hasta que aparezca la primera imagen de un carro (máximo 15 segs)
            try:
                page.wait_for_selector("img[itemprop='image']", timeout=15000)
            except:
                print(" No se encontraron imágenes en Autocosmos. Posible fin de lista o bloqueo.")
                break
            
            for _ in range(8):
                page.mouse.wheel(0, 1200)
                time.sleep(0.6)

            items = page.locator("img[itemprop='image']").all()
            guardados = 0
            for i, img in enumerate(items):
                try:
                    alt = img.get_attribute("alt", timeout=5000)
                    src = img.get_attribute("src", timeout=5000)
                    
                    if alt and "precio $" in alt:
                        partes = alt.split("precio")
                        nombre = partes[0].strip()
                        p_str = partes[1].replace("$", "").replace(".", "").strip()
                        
                        try:
                            enlace_nodo = img.locator("xpath=ancestor::a").first
                            enlace_real = enlace_nodo.get_attribute("href", timeout=5000)
                            if enlace_real and enlace_real.startswith("/"):
                                enlace_real = "https://www.autocosmos.com.co" + enlace_real
                        except:
                            enlace_real = ""
                        
                        if enlace_real:
                            guardar_bd({
                                "source": "autocosmos",
                                "title": nombre,
                                "url": enlace_real, 
                                "price": int(p_str) if p_str.isdigit() else 0,
                                "price_text": f"$ {p_str}",
                                "image": src,
                                "year": "N/A",
                                "mileage": "Consultar",
                                "location": "Colombia"
                            })
                            guardados += 1
                except: 
                    continue
            print(f" Página {pagina}: {guardados} vehículos guardados.")
    except Exception as e: 
        print(f" Error en Autocosmos: {e}")

# --- MOTOR PRINCIPAL ---
def motor_principal():
    print("\n" + "="*50)
    print(" MOTOR DE EXTRACCIÓN AUTO-SMART DB (MODO FANTASMA)")
    print("="*50)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        
        Stealth().apply_stealth_sync(context)
        
        page = context.new_page()
        
        scrapear_tucarro_masivo(page)
        scrapear_autocosmos_masivo(page)
        
        context.close()
        browser.close()
    
    print(f"\n EXTRACCIÓN FINALIZADA.")

# --- RUTAS DE FLASK ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/vehicles', methods=['GET'])
def get_vehicles():
    coleccion = obtener_coleccion()
    todos = list(coleccion.find().sort("_id", -1).limit(1000))
    for v in todos:
        v['_id'] = str(v['_id'])
    
    precios = [c['price'] for c in todos if c.get('price', 0) > 0]
    stats = {
        "total": len(todos),
        "price_min": min(precios) if precios else 0,
        "price_max": max(precios) if precios else 0,
        "price_avg": sum(precios) // len(precios) if precios else 0
    }
    return jsonify({"vehicles": todos, "stats": stats})

if __name__ == '__main__':
    # Lógica de inicio inteligente
    coleccion = obtener_coleccion()
    cantidad_vehiculos = coleccion.count_documents({})
    
    print("\n" + "="*50)
    print(f"ESTADO DE LA BASE DE DATOS: {cantidad_vehiculos} vehículos encontrados.")
    print("="*50)

    if cantidad_vehiculos == 0:
        print(" La base de datos está vacía. Iniciando motor de extracción...")
        proceso_scraper = multiprocessing.Process(target=motor_principal)
        proceso_scraper.start()
    else:
        print(" Datos detectados. El sistema usará la base de datos local.")
        print("(Si quieres forzar un nuevo scrapeo, limpia la colección en MongoDB Compass)")

    app.run(debug=True, port=5000, use_reloader=False)