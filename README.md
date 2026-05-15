# AutoSmart DB 🚗💻
**Monitor de Mercado y Sistema de Inteligencia Automotriz**

AutoSmart DB es una herramienta automatizada de web scraping y análisis de datos diseñada para extraer, consolidar y comparar información de vehículos usados en Colombia desde múltiples plataformas (TuCarro y Autocosmos). 



## 🚀 Características Principales
* **Motor Stealth:** Utiliza `playwright-stealth` para navegación indetectable y evasión de bloqueos (Cloudflare/DataDome).
* **Extracción Masiva:** Barrido automatizado de paginación con extracción de precios, año, kilometraje y enlaces reales de publicación.
* **Persistencia Inteligente:** Integración con **MongoDB**. El sistema detecta si la base de datos local está vacía antes de iniciar un nuevo barrido, ahorrando recursos y reduciendo el riesgo de baneos.
* **Panel VIP de Comparativa:** Frontend dinámico construido con HTML/JS y Tailwind CSS que, al buscar un vehículo, corona instantáneamente:
  *  La Mejor Oferta (Menor precio)
  *  El Más Moderno (Año más reciente)
  *  Menor Kilometraje

##  Tecnologías Utilizadas
* **Backend:** Python, Flask, Flask-CORS.
* **Scraping:** Playwright (Chromium), Playwright-Stealth v2.0.3.
* **Base de Datos:** MongoDB (PyMongo).
* **Frontend:** HTML5, CSS3, JavaScript Vanilla, Tailwind CSS.

##  Instalación y Uso
1. Clona este repositorio:
   ```bash
   git clone [https://github.com/Ronaldo320/autosmart-scraper.git](https://github.com/Ronaldo320/autosmart-scraper.git)
# Instala las dependencias necesarias:
pip install flask flask-cors playwright playwright-stealth pymongo
playwright install chromium

# Asegúrate de tener el servicio de MongoDB corriendo en tu máquina
(mongodb://localhost:27017/).
#Inicia la aplicación:
python app.py
bre tu navegador en http://127.0.0.1:5000.
