"""
Scraper de AutoSmart: extrae autos nuevos y usados de Autocosmos y CarroYa
y los guarda en la colección `vehiculos` de MongoDB.

Se ejecuta en procesos aparte (varias ventanas de Chrome en paralelo) para no
bloquear la aplicación web.
"""
import json
import multiprocessing
import os
import re
import time
from datetime import datetime

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

import db
from db import formato_cop, inferir_marca

# --- CONFIGURACIÓN DEL SCRAPER ---
META_VEHICULOS = int(os.environ.get("AUTOSMART_META", "1200"))   # objetivo: más de 1000 autos
MAX_PAGINAS = int(os.environ.get("AUTOSMART_MAX_PAGINAS", "150"))
PAGINAS_VACIAS_SEGUIDAS = 3   # fin de lista si varias páginas seguidas no traen autos nuevos
FUENTES_EXCLUIDAS = ["tucarro"]  # Mercado Libre / TuCarro ya no se usa
URL_AUTOCOSMOS = "https://www.autocosmos.com.co"
SECCIONES = ["usado", "nuevo"]
VENTANAS = int(os.environ.get("AUTOSMART_VENTANAS", "3"))  # ventanas de Chrome trabajando a la vez (cada una en un listado distinto)
SCROLLS_POR_PAGINA = 10      # bajar la página poco a poco para que carguen todos los anuncios
PAUSA_SCROLL = 0.6
MARCAS = [
    "chevrolet", "renault", "mazda", "toyota", "kia", "nissan", "volkswagen", "ford",
    "hyundai", "suzuki", "mercedes-benz", "bmw", "audi", "jeep", "mitsubishi", "honda",
    "peugeot", "subaru", "volvo", "citroen", "dodge", "land-rover", "porsche", "ssangyong",
    "byd", "chery", "jac", "mg", "great-wall", "dfsk", "foton", "jmc", "fiat", "seat",
    "skoda", "mini", "ram", "lexus", "jaguar", "chrysler", "isuzu", "hino", "changan",
    "baic", "geely", "zotye", "faw", "haval", "jetour", "cupra", "ds", "opel", "infiniti",
]

SITIOS = ["carroya", "autocosmos"]   # fuentes activas (sin Mercado Libre)

def total_autos():
    return db.vehiculos().count_documents({"source": {"$in": SITIOS}})

total_usados = total_autos  # compatibilidad con la versión anterior

def limpiar_fuentes_excluidas():
    """Quita del catálogo los anuncios de TuCarro/Mercado Libre (las órdenes ya hechas no se tocan)."""
    # Integridad: los que ya están en alguna orden no se borran
    en_ordenes = db.ordenes().distinct("items.vehiculo_id")
    r = db.vehiculos().delete_many({"source": {"$in": FUENTES_EXCLUIDAS}, "_id": {"$nin": en_ordenes}})
    if r.deleted_count:
        print(f"[🧹] Eliminados {r.deleted_count} anuncios de TuCarro/Mercado Libre del catálogo.")

def guardar_bd(vehiculo):
    """Guarda/actualiza por URL. Devuelve True si el auto es nuevo en la base de datos."""
    coleccion = db.vehiculos()
    if not vehiculo.get("url"): return False
    vehiculo["fecha_extraccion"] = datetime.now()
    vehiculo["marca"] = inferir_marca(vehiculo.get("title", ""))
    r = coleccion.update_one(
        {"url": vehiculo["url"]},
        {"$set": vehiculo, "$setOnInsert": {"estado": "disponible", "etiquetas": []}},
        upsert=True,
    )
    return r.upserted_id is not None

# --- SCRAPER AUTOCOSMOS (nuevos y usados) ---
RE_ANIO = re.compile(r"\b(19[6-9]\d|20[0-3]\d)\b")
RE_KM = re.compile(r"(\d{1,3}(?:[.,]\d{3})+|\d+)\s*km\b", re.IGNORECASE)

# Lee TODOS los anuncios de la página en una sola llamada (mucho más rápido que elemento por elemento)
JS_LEER_ANUNCIOS = r"""
() => {
// Elige la mejor URL de foto: ignora las imágenes de relleno de la carga diferida y completa rutas relativas
const mejorImagen = (img) => {
  if (!img) return "";
  const cand = [];
  for (const a of ["data-src", "data-lazy-src", "data-lazy", "data-original", "data-url", "src"]) {
    const v = img.getAttribute(a); if (v) cand.push(v);
  }
  const ultimo = v => v.split(",").pop().trim().split(" ")[0];
  for (const a of ["data-srcset", "srcset"]) { const v = img.getAttribute(a); if (v) cand.push(ultimo(v)); }
  const pic = img.closest("picture");
  if (pic) pic.querySelectorAll("source").forEach(s => {
    const v = s.getAttribute("srcset") || s.getAttribute("data-srcset"); if (v) cand.push(ultimo(v));
  });
  if (img.currentSrc) cand.push(img.currentSrc);
  for (const c of cand) {
    if (!c || c.startsWith("data:") || /blank|placeholder|spinner|loading\.|pixel\.|1x1|transparent/i.test(c)) continue;
    try { return new URL(c, location.href).href; } catch (e) {}
  }
  return "";
};
return Array.from(document.querySelectorAll("img[itemprop='image']")).map(img => {
  const card = img.closest("article") || img.closest("li") || img.closest("a");
  const a = img.closest("a");
  const md = p => {
    const el = card && card.querySelector("[itemprop='" + p + "']");
    return el ? (el.getAttribute("content") || el.textContent || "").trim() : "";
  };
  return {
    alt: img.getAttribute("alt") || "",
    src: mejorImagen(img),
    href: a ? (a.getAttribute("href") || "") : "",
    text: card ? (card.innerText || "") : "",
    year: md("vehicleModelDate"), km: md("mileageFromOdometer"), city: md("addressLocality"),
  };
});
}
"""

def procesar_anuncio(d, seccion):
    """Convierte los datos crudos de un anuncio en un dict de vehículo, o None si no sirve."""
    alt = d.get("alt", "")
    if "precio $" not in alt.lower():
        return None  # sin precio publicado: no se puede vender en la tienda

    partes = re.split(r"precio", alt, maxsplit=1, flags=re.IGNORECASE)
    nombre = re.sub(r"^\s*foto\s+", "", partes[0], flags=re.IGNORECASE)
    nombre = re.split(r"\s+(?:nuevo|usado)\b", nombre, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    p_str = re.sub(r"\D", "", partes[1])
    precio = int(p_str) if p_str else 0
    if not nombre or precio <= 0:
        return None

    enlace = d.get("href", "")
    if enlace.startswith("/"):
        enlace = URL_AUTOCOSMOS + enlace
    if not enlace.startswith("http"):
        return None

    texto = d.get("text", "")
    condicion = seccion
    if re.search(r"\bnuevo\b", alt, re.IGNORECASE): condicion = "nuevo"
    elif re.search(r"\busado\b", alt, re.IGNORECASE): condicion = "usado"

    m = RE_ANIO.search(d.get("year", "")) or RE_ANIO.search(texto) or RE_ANIO.search(nombre)
    anio = m.group(0) if m else ("Último modelo" if condicion == "nuevo" else "N/A")

    m = RE_KM.search(d.get("km", "")) or RE_KM.search(texto)
    km_num = int(re.sub(r"\D", "", m.group(1))) if m else (0 if condicion == "nuevo" else None)
    if km_num is None and d.get("km", "").strip().isdigit():
        km_num = int(d["km"].strip())
    kilometraje = (f"{km_num:,} km".replace(",", ".")) if km_num is not None else "Consultar"

    return {
        "source": "autocosmos",
        "condition": condicion,
        "title": nombre,
        "url": enlace.split("?")[0],
        "price": precio,
        "price_text": formato_cop(precio),
        "image": normalizar_imagen(d.get("src", ""), "autocosmos"),
        "year": anio,
        "mileage": kilometraje,
        "mileage_km": km_num,
        "location": d.get("city") or "Colombia",
    }

# --- SCRAPER CARROYA (segunda fuente) ---
URL_CARROYA = "https://www.carroya.com"
# Formatos de URL posibles del listado ({p} = página). Se usa el primero que traiga anuncios.
PLANTILLAS_CARROYA = {
    "usado": [URL_CARROYA + "/buscar/carros-y-camionetas/usados?page={p}",
              URL_CARROYA + "/automoviles-y-camionetas/usados?page={p}",
              URL_CARROYA + "/automoviles-y-camionetas?page={p}"],
    "nuevo": [URL_CARROYA + "/buscar/carros-y-camionetas/nuevos?page={p}",
              URL_CARROYA + "/automoviles-y-camionetas/nuevos?page={p}"],
}
CARPETA_APP = os.path.dirname(os.path.abspath(__file__))
RE_PRECIO = re.compile(r"\$\s?(\d{1,3}(?:[.,]\d{3}){2,})")

# Busca tarjetas de anuncio a partir de los enlaces a /vehiculo/ (o similares) que tengan un precio cerca
JS_CARROYA_DOM = r"""
() => {
// Elige la mejor URL de foto: ignora las imágenes de relleno de la carga diferida y completa rutas relativas
const mejorImagen = (img) => {
  if (!img) return "";
  const cand = [];
  for (const a of ["data-src", "data-lazy-src", "data-lazy", "data-original", "data-url", "src"]) {
    const v = img.getAttribute(a); if (v) cand.push(v);
  }
  const ultimo = v => v.split(",").pop().trim().split(" ")[0];
  for (const a of ["data-srcset", "srcset"]) { const v = img.getAttribute(a); if (v) cand.push(ultimo(v)); }
  const pic = img.closest("picture");
  if (pic) pic.querySelectorAll("source").forEach(s => {
    const v = s.getAttribute("srcset") || s.getAttribute("data-srcset"); if (v) cand.push(ultimo(v));
  });
  if (img.currentSrc) cand.push(img.currentSrc);
  for (const c of cand) {
    if (!c || c.startsWith("data:") || /blank|placeholder|spinner|loading\.|pixel\.|1x1|transparent/i.test(c)) continue;
    try { return new URL(c, location.href).href; } catch (e) {}
  }
  return "";
};

  const SEL = 'a[href*="/vehiculo"], a[href*="/vehiculos/"], a[href*="/detalle"], a[href*="/carro/"]';
  const precio = /\$\s?\d{1,3}([.,]\d{3}){2,}/;
  const out = [], vistos = new Set();
  document.querySelectorAll(SEL).forEach(a => {
    const href = a.href.split('?')[0];
    if (vistos.has(href)) return;
    let card = a;
    for (let i = 0; i < 7 && card.parentElement; i++) {
      if (precio.test(card.innerText || '')) break;
      const padre = card.parentElement;
      const otros = new Set(Array.from(padre.querySelectorAll(SEL)).map(x => x.href.split('?')[0]));
      if (otros.size > 1) break;           // el contenedor ya incluye otros anuncios: no subir más
      card = padre;
    }
    const texto = (card.innerText || '').trim();
    if (!precio.test(texto)) return;
    vistos.add(href);
    const img = card.querySelector('img');
    const h = card.querySelector('h1,h2,h3,h4,[class*="title" i],[class*="nombre" i]');
    out.push({
      href, text: texto,
      title: (h && h.innerText) || (img && img.alt) || a.title || '',
      img: mejorImagen(img),
    });
  });
  return out;
}
"""
# Datos estructurados incrustados en la página (JSON-LD, __NEXT_DATA__, etc.)
JS_JSON_INCRUSTADO = """
() => Array.from(document.querySelectorAll(
  'script[type="application/ld+json"], script#__NEXT_DATA__, script[type="application/json"]'
)).map(s => s.textContent || '')
"""

def _num(x):
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x)
    if isinstance(x, dict):
        return _num(x.get("value") or x.get("amount") or x.get("price"))
    if isinstance(x, str):
        d = re.sub(r"[^\d]", "", x.split(",")[0] if re.search(r",\d{2}$", x) else x)
        return int(d) if d else None
    return None

def _get(d, *claves):
    """Busca la primera clave presente (sin importar mayúsculas)."""
    low = {str(k).lower(): v for k, v in d.items()}
    for k in claves:
        if k in low and low[k] not in (None, "", [], {}):
            return low[k]
    return None

def _texto(x):
    if isinstance(x, dict):
        return _texto(x.get("name") or x.get("nombre") or x.get("label") or x.get("value"))
    if isinstance(x, list):
        return _texto(x[0]) if x else ""
    return str(x).strip() if x is not None else ""

DOMINIO_FUENTE = {"autocosmos": "https://www.autocosmos.com.co", "carroya": "https://www.carroya.com"}
RE_RELLENO = re.compile(r"blank|placeholder|spinner|loading\.|pixel\.|1x1|transparent", re.I)

def normalizar_imagen(url, fuente):
    """Deja la URL de la foto lista para mostrarse en nuestra página (o vacía si no sirve)."""
    url = (url or "").strip()
    if not url or url.startswith("data:") or RE_RELLENO.search(url):
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return DOMINIO_FUENTE.get(fuente, "") + url
    return url if url.startswith("http") else ""

def _imagen(x):
    if isinstance(x, list):
        return _imagen(x[0]) if x else ""
    if isinstance(x, dict):
        return _imagen(x.get("url") or x.get("src") or x.get("large") or x.get("medium") or x.get("contentUrl"))
    return str(x) if isinstance(x, str) else ""

def _dict_a_anuncio(d):
    """Si un objeto JSON parece un anuncio de carro, lo convierte; si no, devuelve None."""
    precio = _num(_get(d, "price", "precio", "valor", "saleprice", "pricevalue", "amount"))
    if precio is None and isinstance(d.get("offers"), dict):
        precio = _num(d["offers"].get("price"))
    if not precio or not (3_000_000 <= precio <= 5_000_000_000):
        return None
    titulo = _texto(_get(d, "title", "titulo", "name", "nombre"))
    if not titulo:
        partes = [_texto(_get(d, "brand", "marca")), _texto(_get(d, "model", "modelo")), _texto(_get(d, "version", "línea", "linea"))]
        titulo = " ".join(p for p in partes if p)
    if len(titulo) < 3:
        return None
    url = _texto(_get(d, "url", "link", "permalink", "href", "canonical", "detailurl"))
    ident = _texto(_get(d, "id", "_id", "vehicleid", "adid", "idvehiculo", "publicationid"))
    if url.startswith("/"):
        url = URL_CARROYA + url
    if not url.startswith("http"):
        if not ident:
            return None
        slug = re.sub(r"[^a-z0-9]+", "-", titulo.lower()).strip("-")
        url = f"{URL_CARROYA}/vehiculo/{slug}/{ident}"
    return {
        "title": titulo, "price": precio, "url": url,
        "year": _num(_get(d, "year", "anio", "año", "modelyear", "vehiclemodeldate", "yearmodel")),
        "km": _num(_get(d, "mileage", "kilometraje", "km", "mileagefromodometer", "kms")),
        "city": _texto(_get(d, "city", "ciudad", "location", "ubicacion", "address")),
        "image": _imagen(_get(d, "image", "images", "photo", "photos", "foto", "fotos", "thumbnail", "imageurl", "picture")),
        "condition": _texto(_get(d, "condition", "condicion", "estado", "itemcondition")).lower(),
    }

def _buscar_en_json(obj, out, prof=0):
    if prof > 14:
        return
    if isinstance(obj, dict):
        a = _dict_a_anuncio(obj)
        if a:
            out.append(a)
            return
        for v in obj.values():
            _buscar_en_json(v, out, prof + 1)
    elif isinstance(obj, list):
        for v in obj:
            _buscar_en_json(v, out, prof + 1)

def _anuncio_carroya(base, seccion):
    """Normaliza un anuncio (venga del HTML o del JSON) al formato de la base de datos."""
    titulo = re.sub(r"\s+", " ", base["title"]).strip()[:120]
    precio = base["price"]
    anio = base.get("year")
    km = base.get("km")
    texto_cond = (base.get("condition") or "") + " " + base.get("text", "")[:200].lower()
    condicion = "nuevo" if ("nuevo" in texto_cond and "usado" not in texto_cond) else seccion
    if km is None and condicion == "nuevo":
        km = 0
    if anio and not (1960 <= anio <= 2035):
        anio = None
    ciudad = base.get("city") or "Colombia"
    return {
        "source": "carroya",
        "condition": condicion,
        "title": titulo,
        "url": base["url"].split("?")[0],
        "price": precio,
        "price_text": formato_cop(precio),
        "image": normalizar_imagen(base.get("image", ""), "carroya"),
        "year": str(anio) if anio else ("Último modelo" if condicion == "nuevo" else "N/A"),
        "mileage": f"{km:,} km".replace(",", ".") if km is not None else "Consultar",
        "mileage_km": km,
        "location": ciudad[:60],
    }

def _desde_dom(item):
    texto = item.get("text", "")
    m = RE_PRECIO.search(texto)
    if not m:
        return None
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    titulo = (item.get("title") or "").strip() or next((l for l in lineas if not RE_PRECIO.search(l) and len(l) > 4), "")
    ma = RE_ANIO.search(texto)
    mk = RE_KM.search(texto)
    return {
        "title": titulo, "price": int(re.sub(r"\D", "", m.group(1))), "url": item["href"],
        "year": int(ma.group(0)) if ma else None,
        "km": int(re.sub(r"\D", "", mk.group(1))) if mk else None,
        "image": item.get("img", ""), "text": texto, "city": "",
    }

def leer_pagina_carroya(page, url):
    """Abre una página de CarroYa y extrae anuncios del HTML, del JSON incrustado y de las respuestas JSON."""
    respuestas = []
    def capturar(r):
        try:
            if "json" in (r.headers.get("content-type") or ""):
                respuestas.append(r)
        except Exception:
            pass
    page.on("response", capturar)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        # Bajar poco a poco y pulsar "ver más" si existe, para que carguen todos los anuncios
        anterior, estables = -1, 0
        for _ in range(SCROLLS_POR_PAGINA + 6):
            page.mouse.wheel(0, 1200)
            time.sleep(PAUSA_SCROLL)
            try:
                boton = page.get_by_role("button", name=re.compile(r"ver más|cargar más|mostrar más", re.I)).first
                if boton.count() and boton.is_visible():
                    boton.click(timeout=3000)
                    time.sleep(1)
            except Exception:
                pass
            n = page.locator('a[href*="/vehiculo"]').count()
            estables = estables + 1 if n == anterior else 0
            anterior = n
            if estables >= 3:
                break

        anuncios = {}
        for item in page.evaluate(JS_CARROYA_DOM):
            a = _desde_dom(item)
            if a and len(a["title"]) >= 3:
                anuncios[a["url"].split("?")[0]] = a

        encontrados = []
        for txt in page.evaluate(JS_JSON_INCRUSTADO):
            try:
                _buscar_en_json(json.loads(txt), encontrados)
            except Exception:
                pass
        for r in respuestas:
            try:
                _buscar_en_json(r.json(), encontrados)
            except Exception:
                pass
        for a in encontrados:
            clave = a["url"].split("?")[0]
            if clave in anuncios:   # completar lo que el HTML no tenía
                for k in ("year", "km", "city", "image"):
                    if not anuncios[clave].get(k) and a.get(k):
                        anuncios[clave][k] = a[k]
            else:
                anuncios[clave] = a
        return list(anuncios.values())
    except Exception as e:
        print(f"   [carroya] Error abriendo {url}: {e}")
        return []
    finally:
        page.remove_listener("response", capturar)

def guardar_html_debug(page, nombre):
    try:
        ruta = os.path.join(CARPETA_APP, nombre)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"   [carroya] No se encontraron anuncios. Guardé la página en {ruta} — envíala para ajustar el scraper.")
    except Exception:
        pass

def barrer_carroya(page, wid, seccion, marca):
    etiqueta = f"V{wid + 1} carroya {seccion}s" + (f" {marca}" if marca else "")
    plantillas = PLANTILLAS_CARROYA[seccion]
    plantilla = None
    vacias = 0
    for pagina in range(1, MAX_PAGINAS + 1):
        if total_autos() >= META_VEHICULOS:
            return
        candidatas = [plantilla] if plantilla else plantillas
        crudos = []
        for pl in candidatas:
            url = pl.format(p=pagina) + (f"&brand={marca}" if marca else "")
            crudos = leer_pagina_carroya(page, url)
            if crudos:
                plantilla = pl
                break
        if not crudos:
            if pagina == 1:
                print(f"   [{etiqueta}] sin anuncios.")
                if not marca:
                    guardar_html_debug(page, f"debug_carroya_{seccion}.html")
            else:
                print(f"   [{etiqueta}] fin en la página {pagina - 1}.")
            return

        nuevos = vistos = 0
        for base in crudos:
            try:
                v = _anuncio_carroya(base, seccion)
                vistos += 1
                nuevos += guardar_bd(v)
            except Exception:
                continue
        print(f"   [{etiqueta}] Página {pagina}: {vistos} anuncios, {nuevos} nuevos → total en BD: {total_autos()}")
        vacias = vacias + 1 if nuevos == 0 else 0
        if vacias >= PAGINAS_VACIAS_SEGUIDAS:
            print(f"   [{etiqueta}] {PAGINAS_VACIAS_SEGUIDAS} páginas seguidas sin autos nuevos: siguiente listado.")
            return
        time.sleep(1)

def construir_listados():
    """Orden de recorrido: (sitio, sección, marca).
    CarroYa va primero porque trae autos que todavía no están en la base; luego Autocosmos
    (su listado general se corta hacia la página 15, por eso se recorre también por marca)."""
    listados = [("carroya", s, None) for s in SECCIONES]
    listados += [("autocosmos", s, None) for s in SECCIONES]
    for marca in MARCAS:
        for s in SECCIONES:
            listados.append(("carroya", s, marca))
            listados.append(("autocosmos", s, marca))
    return listados

def nuevo_estado_compartido():
    """Contador compartido entre ventanas: cuál es el siguiente listado libre."""
    return {"lock": multiprocessing.Lock(), "siguiente": multiprocessing.Value('i', 0)}

def tomar_listado(estado, listados):
    with estado["lock"]:
        i = estado["siguiente"].value
        if i >= len(listados):
            return None
        estado["siguiente"].value = i + 1
        return listados[i]

def leer_pagina(page, url):
    """Abre una página y la baja poco a poco (como la versión original) para que carguen TODOS los anuncios."""
    for intento in (1, 2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("img[itemprop='image']", timeout=15000)
            anterior, estables = -1, 0
            for _ in range(SCROLLS_POR_PAGINA):
                page.mouse.wheel(0, 1200)
                time.sleep(PAUSA_SCROLL)
                n = page.locator("img[itemprop='image']").count()
                estables = estables + 1 if n == anterior else 0
                anterior = n
                if estables >= 3:      # ya no aparecen más anuncios: no hace falta seguir bajando
                    break
            return page.evaluate(JS_LEER_ANUNCIOS)
        except Exception:
            if intento == 1:
                time.sleep(3)
    return []

def barrer_listado(page, wid, seccion, marca):
    """Recorre un listado página por página hasta que se acabe o se llegue a la meta."""
    url_base = f"{URL_AUTOCOSMOS}/auto/{seccion}" + (f"/{marca}" if marca else "")
    etiqueta = f"V{wid + 1} {seccion}s" + (f" {marca}" if marca else "")
    vacias = 0
    for pagina in range(1, MAX_PAGINAS + 1):
        if total_autos() >= META_VEHICULOS:
            return
        crudos = leer_pagina(page, f"{url_base}?pidx={pagina}")
        if not crudos:
            if pagina == 1:
                print(f"   [{etiqueta}] sin anuncios.")
            else:
                print(f"   [{etiqueta}] fin en la página {pagina - 1}.")
            return

        nuevos = vistos = 0
        for d in crudos:
            try:
                v = procesar_anuncio(d, seccion)
                if v:
                    vistos += 1
                    nuevos += guardar_bd(v)
            except Exception:
                continue
        print(f"   [{etiqueta}] Página {pagina}: {vistos} anuncios, {nuevos} nuevos → total en BD: {total_autos()}")

        vacias = vacias + 1 if nuevos == 0 else 0
        if vacias >= PAGINAS_VACIAS_SEGUIDAS:
            print(f"   [{etiqueta}] {PAGINAS_VACIAS_SEGUIDAS} páginas seguidas sin autos nuevos: siguiente listado.")
            return
        time.sleep(1)  # pausa para no saturar el sitio

def trabajador(wid, meta, estado):
    """Proceso independiente: una ventana de Chrome que va tomando listados libres."""
    global META_VEHICULOS
    db.reiniciar_conexion()
    META_VEHICULOS = meta
    listados = construir_listados()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled",
                      f"--window-position={wid * 420},{wid * 40}", "--window-size=1200,850"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1200, "height": 850}
            )
            Stealth().apply_stealth_sync(context)
            page = context.new_page()
            while total_autos() < META_VEHICULOS:
                listado = tomar_listado(estado, listados)
                if listado is None:
                    break
                sitio, seccion, marca = listado
                if sitio == "carroya":
                    barrer_carroya(page, wid, seccion, marca)
                else:
                    barrer_listado(page, wid, seccion, marca)
            context.close()
            browser.close()
    except Exception as e:
        print(f" [V{wid + 1}] Error: {e}")

# --- FOTOS: reparar las guardadas y completar las que faltan ---
JS_FOTO_DETALLE = r"""
() => {
  const m = document.querySelector('meta[property="og:image"], meta[name="og:image"], meta[name="twitter:image"], link[rel="image_src"]');
  const v = m ? (m.getAttribute("content") || m.getAttribute("href")) : "";
  if (v && !v.startsWith("data:")) { try { return new URL(v, location.href).href; } catch (e) {} }
  const img = document.querySelector('img[itemprop="image"], [class*="gallery" i] img, [class*="carousel" i] img, main img');
  return img ? (img.getAttribute("data-src") || img.currentSrc || img.src || "") : "";
}
"""
MAX_FOTOS_POR_RONDA = int(os.environ.get("AUTOSMART_MAX_FOTOS", "400"))

def filtro_sin_foto():
    return {"source": {"$in": SITIOS}, "estado": {"$ne": "vendido"},
            "$or": [{"image": {"$exists": False}}, {"image": ""}, {"image": None}]}

def reparar_imagenes():
    """Corrige las fotos ya guardadas (rutas relativas, imágenes de relleno). Devuelve cuántos autos siguen sin foto."""
    col = db.vehiculos()
    corregidas = 0
    for v in col.find({"source": {"$in": SITIOS}, "image": {"$nin": ["", None]}}, {"image": 1, "source": 1}):
        nueva = normalizar_imagen(v.get("image"), v.get("source"))
        if nueva != v.get("image"):
            col.update_one({"_id": v["_id"]}, {"$set": {"image": nueva}})
            corregidas += 1
    sin_foto = col.count_documents(filtro_sin_foto())
    if corregidas:
        print(f"[🖼️] Corregidas {corregidas} direcciones de fotos.")
    if sin_foto:
        print(f"[🖼️] {sin_foto} autos sin foto: se buscarán en la página de cada anuncio.")
    return sin_foto

def completar_fotos(page):
    """Abre la página de cada anuncio sin foto y toma su foto principal (og:image)."""
    col = db.vehiculos()
    pendientes = list(col.find(filtro_sin_foto(), {"url": 1, "source": 1}).limit(MAX_FOTOS_POR_RONDA))
    if not pendientes:
        return
    print(f"\n[🖼️] Buscando fotos de {len(pendientes)} autos...")
    ok = 0
    for i, v in enumerate(pendientes, 1):
        try:
            page.goto(v["url"], wait_until="domcontentloaded", timeout=30000)
            foto = normalizar_imagen(page.evaluate(JS_FOTO_DETALLE), v.get("source"))
        except Exception:
            foto = ""
        if foto:
            col.update_one({"_id": v["_id"]}, {"$set": {"image": foto}})
            ok += 1
        if i % 20 == 0 or i == len(pendientes):
            print(f"   [fotos] {i}/{len(pendientes)} revisados, {ok} fotos encontradas")
        time.sleep(0.5)

def trabajador_fotos():
    """Proceso aparte (una ventana) que solo completa fotos."""
    db.reiniciar_conexion()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
            Stealth().apply_stealth_sync(context)
            completar_fotos(context.new_page())
            context.close()
            browser.close()
    except Exception as e:
        print(f" [fotos] Error: {e}")

# --- MOTOR PRINCIPAL ---
def motor_principal(meta=None):
    db.reiniciar_conexion()
    meta = meta or META_VEHICULOS
    print("\n" + "="*50)
    print(f" MOTOR DE EXTRACCIÓN AUTO-SMART DB — NUEVOS Y USADOS")
    print(f" Meta: {meta} autos · {VENTANAS} ventanas en paralelo")
    print("="*50)
    inicio = time.time()

    estado = nuevo_estado_compartido()
    procesos = [multiprocessing.Process(target=trabajador, args=(w, meta, estado)) for w in range(VENTANAS)]
    for pr in procesos: pr.start()
    for pr in procesos: pr.join()

    # Fotos que quedaron vacías durante el barrido
    if reparar_imagenes():
        trabajador_fotos()

    col = db.vehiculos()
    total = total_autos()
    nuevos = col.count_documents({"source": {"$in": SITIOS}, "condition": "nuevo"})
    minutos = (time.time() - inicio) / 60
    print(f"\n EXTRACCIÓN FINALIZADA: {total} autos en MongoDB "
          f"({nuevos} nuevos, {total - nuevos} usados) en {minutos:.1f} min.")
    if total < 1000:
        print(" [!] No se llegó a 1000. Se recorrieron todos los listados y marcas disponibles en Autocosmos y CarroYa.")
