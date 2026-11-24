"""
Configuración de AutoSmart.

Las credenciales (cadena de conexión de MongoDB y clave secreta de Flask) se leen
de variables de entorno o del archivo `.env`, que NO se sube al repositorio
(está en .gitignore). Usa `.env.example` como plantilla.
"""
import os

from dotenv import load_dotenv

CARPETA_APP = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(CARPETA_APP, ".env"))


def _requerida(nombre):
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise SystemExit(
            f"\n[X] Falta la variable de entorno {nombre}.\n"
            f"    Copia el archivo .env.example como .env y completa sus valores.\n"
        )
    return valor


MONGO_URI = _requerida("MONGO_URI")
MONGO_DB = _requerida("MONGO_DB")
SECRET_KEY = _requerida("SECRET_KEY")
