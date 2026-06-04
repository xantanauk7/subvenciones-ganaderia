"""
scraper.py v4 — Usa el buscador oficial del BOE (sin bloqueos)
"""

import os, json, logging, datetime, time
import urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from anthropic import Anthropic
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
claude   = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SEARCHES = [
    "subvenciones ganadería digitalización",
    "ayudas modernización explotaciones ganaderas",
    "subvenciones tecnología ganadera",
    "ayudas sector primario digital",
    "subvenciones collar monitorización ganado",
    "ayudas ganadería precisión IoT",
]

SYSTEM_PROMPT = """Eres experto en subvenciones para digitalización del sector ganadero español.
Analiza si esta convocatoria es relevante para vender collares GPS y sensores para ganado bovino, ovino y caprino.

Responde SOLO con JSON sin markdown:
{
  "relevante": true/false,
  "puntuacion": 1-10,
  "prioridad": "alta"/"media"/"baja",
  "ccaa_normalizada": "nombre CCAA o Nacional",
  "importe_max_estimado": numero_o_null,
  "pct_subvencion_estimado": numero_o_null,
  "fecha_cierre_estimada": "YYYY-MM-DD o null",
  "notas": "resumen breve de requisitos clave"
}"""


def search_boe(query: str) -> list[dict]:
    """Busca en el BOE usando su buscador oficial."""
    # Calcular fechas últimas 2 semanas
    hoy = datetime.date.today()
    hace14 = hoy - datetime.timedelta(days=14)
    
    url = "https://boe.es/buscar/boe.php?" + urllib.parse.urlencode({
        "campo[0]": "TIT",
        "dato[0]": query,
        "operador[0]": "and",
        "campo[1]": "TXT",
        "dato[1]": "subvención OR ayuda OR convocatoria",
        "operador[1]": "and",
        "fechaDesde": hace14.strftime("%d/%m/%Y"),
        "fechaHasta": hoy.strftime("%d/%m/%Y"),
        "sort": "ant",
        "base_datos": "BOE",
    })
    
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SubvencionesBot/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", errors="ignore")
        
        items = []
        # Parsear resultados del HTML del BOE
        import re
        # Buscar bloques de resultado
        bloques = re.findall(
            r'<li class="resultado-busqueda"[^>]*>(.*?)</li>',
            html, re.DOTALL
        )
        for bloque in bloques[:10]:
            titulo_m = re.search(r'<p class="titulo"><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', bloque, re.DOTALL)
            dept_m   = re.search(r'<p class="departamento"[^>]*>(.*?)</p>', bloque, re.DOTALL)
            fecha_m  = re.search(r'<p class="fechas"[^>]*>.*?(\d{2}/\d{2}/\d{4})', bloque, re.DOTALL)
            
            if titulo_m:
                titulo = re.sub(r'<[^>]+>', '', titulo_m.group(2)).strip()
                url_doc = "https://boe.es" + titulo_m.group(1)
                dept    = re.sub(r'<[^>]+>', '', dept_m.group(1)).strip() if dept_m else ""
                fecha   = fecha_m.group(1) if fecha_m else ""
                
                items.append({
                    "nombre":           titulo,
                    "organismo":        dept,
                    "ccaa":             _detectar_ccaa(dept + " " + titulo),
                    "fuente":           "BOE",
                    "bdns_id":          None,
                    "url_convocatoria": url_doc,
                    "url_boletin":      url_doc,
                    "fecha_cierre":     None,
                    "importe_max":      None,
                    "descripcion":      titulo,
                })
        
        log.info(f"  BOE '{query}': {len(items)} resultados")
        return items

    except Exception as e:
        log.warning(f"  BOE error '{query}': {e}")
        return []


CCAA_MAP = {
    "andaluc": "Andalucía", "aragón": "Aragón", "asturias": "Asturias",
    "baleares": "Baleares", "canarias": "Canarias", "cantabria": "Cantabria",
    "castilla-la mancha": "Castilla-La Mancha", "castilla y león": "Castilla y León",
    "cataluña": "Cataluña", "catalunya": "Cataluña",
    "comunitat valenciana": "Comunidad Valenciana", "comunidad valenciana": "Comunidad Valenciana",
    "extremadura": "Extremadura", "galicia": "Galicia", "la rioja": "La Rioja",
    "madrid": "Madrid", "murcia": "Murcia", "navarra": "Navarra",
    "país vasco": "País Vasco", "euskadi": "País Vasco",
}

def _detectar_ccaa(texto: str) -> str:
    t = texto.lower()
    for k, v in CCAA_MAP.items():
        if k in t:
            return v
    return "Nacional"


def classify(raw: dict):
    texto = f"Título: {raw['nombre']}\nOrganismo: {raw['organismo']}\nCCAA: {raw['ccaa']}"
    try:
        msg = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": texto}],
        )
        text = msg.content[0].text.strip().replace("```json","").replace("```","")
        result = json.loads(text)
        return result if result.get("relevante") else None
    except Exception as e:
        log.warning(f"Error clasificando: {e}")
        return None


def calcular_estado(fecha_str):
    if not fecha_str:
        return "abierta"
    try:
        cierre = datetime.date.fromisoformat(str(fecha_str)[:10])
        hoy = datetime.date.today()
        if cierre < hoy: return "cerrada"
        if cierre <= hoy + datetime.timedelta(days=60): return "abierta"
        return "proxima"
    except:
        return "abierta"


def upsert(raw: dict, ai: dict) -> str:
    record = {
        "nombre":           raw["nombre"][:500],
        "ccaa":             ai.get("ccaa_normalizada") or raw.get("ccaa", "Nacional"),
        "organismo":        raw.get("organismo", "")[:200],
        "fuente":           raw.get("fuente", "BOE"),
        "url_convocatoria": raw.get("url_convocatoria"),
        "url_boletin":      raw.get("url_boletin"),
        "fecha_cierre":     ai.get("fecha_cierre_estimada") or None,
        "importe_max":      ai.get("importe_max_estimado"),
        "pct_subvencion":   ai.get("pct_subvencion_estimado"),
        "estado":           calcular_estado(ai.get("fecha_cierre_estimada")),
        "prioridad":        ai.get("prioridad", "media"),
        "puntuacion_ia":    ai.get("puntuacion"),
        "notas":            ai.get("notas"),
    }
    record = {k: v for k, v in record.items() if v is not None and v != ""}

    url = raw.get("url_convocatoria", "")
    if url:
        existing = supabase.table("subvenciones").select("id").eq("url_convocatoria", url).execute()
        if existing.data:
            supabase.table("subvenciones").update(record).eq("url_convocatoria", url).execute()
            return "actualizada"

    supabase.table("subvenciones").insert(record).execute()
    return "nueva"


def run():
    log.info("=== Scraper v4 — Buscador BOE ===")
    run_log = supabase.table("scraper_runs").insert({"log": "iniciado v4"}).execute()
    run_id  = run_log.data[0]["id"]

    nuevas = actualizadas = errores = 0
    vistas: set[str] = set()

    for query in SEARCHES:
        items = search_boe(query)
        for raw in items:
            uid = raw.get("url_convocatoria") or raw["nombre"][:60]
            if uid in vistas:
                continue
            vistas.add(uid)

            ai = classify(raw)
            if ai is None:
                continue

            log.info(f"  ✓ [{ai.get('puntuacion')}/10] {raw['nombre'][:70]}")
            try:
                r = upsert(raw, ai)
                if r == "nueva": nuevas += 1
                elif r == "actualizada": actualizadas += 1
            except Exception as e:
                errores += 1
                log.error(f"  ✗ {e}")
        time.sleep(1)

    msg = f"OK — {nuevas} nuevas, {actualizadas} actualizadas, {errores} errores"
    supabase.table("scraper_runs").update({
        "finalizado_en": datetime.datetime.utcnow().isoformat(),
        "nuevas": nuevas, "actualizadas": actualizadas,
        "errores": errores, "log": msg,
    }).eq("id", run_id).execute()
    log.info(f"=== Fin: {msg} ===")


if __name__ == "__main__":
    run()
