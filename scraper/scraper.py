"""
scraper.py — Monitor de subvenciones ganadería digital
Ejecutar manualmente o con cron/GitHub Actions cada 14 días.
 
Requisitos:
    pip install requests beautifulsoup4 anthropic supabase python-dotenv
 
Variables de entorno (.env):
    SUPABASE_URL=https://xxxx.supabase.co
    SUPABASE_SERVICE_KEY=eyJ...
    ANTHROPIC_API_KEY=sk-ant-...
"""
 
import os
import json
import logging
import datetime
import time
import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic
from supabase import create_client
from dotenv import load_dotenv
 
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
 
# ── Clientes ────────────────────────────────────────────────────────────────
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
claude   = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
 
# ── Configuración de búsqueda ────────────────────────────────────────────────
KEYWORDS = [
    "digitalización ganadería",
    "tecnología ganadera",
    "precision livestock",
    "monitorización ganado",
    "collar geolocalización ganado",
    "modernización explotación ganadera",
    "IoT ganadería",
    "trazabilidad animal",
    "ganadería 4.0",
    "sector primario digital",
]
 
CCAA_LIST = [
    "Andalucía", "Aragón", "Asturias", "Baleares", "Canarias",
    "Cantabria", "Castilla-La Mancha", "Castilla y León", "Cataluña",
    "Comunidad Valenciana", "Extremadura", "Galicia", "La Rioja",
    "Madrid", "Murcia", "Navarra", "País Vasco",
]
 
# ── 1. BDNS (Base de Datos Nacional de Subvenciones) ─────────────────────────
def fetch_bdns(keyword: str) -> list[dict]:
    """Consulta la API REST de la BDNS."""
    url = "https://www.infosubvenciones.es/bdnstrans/GE/es/convocatorias"
    params = {
        "texto": keyword,
        "tipoConvocatoria": "1",    # 1 = subvenciones
        "inicio": 0,
        "fin": 20,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("convocatorias", [])
    except Exception as e:
        log.warning(f"BDNS error con '{keyword}': {e}")
        return []
 
 
def parse_bdns_item(item: dict) -> dict:
    """Normaliza un ítem de la BDNS al esquema de la BD."""
    return {
        "nombre":           item.get("desConvocatoria", "").strip(),
        "ccaa":             item.get("desOrgano", "Nacional"),
        "organismo":        item.get("desOrgano", ""),
        "fuente":           "BDNS",
        "bdns_id":          str(item.get("idConvocatoria", "")),
        "url_convocatoria": f"https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/{item.get('idConvocatoria','')}",
        "fecha_apertura":   item.get("fecInicioSolicitud"),
        "fecha_cierre":     item.get("fecFinSolicitud"),
        "importe_max":      item.get("importeTotal"),
        "palabras_clave":   [],
        "estado":           "abierta",
        "prioridad":        "media",
        "puntuacion_ia":    None,
    }
 
 
# ── 2. Clasificador IA ───────────────────────────────────────────────────────
SYSTEM_PROMPT = """Eres un experto en subvenciones para digitalización del sector ganadero español.
Tu tarea es analizar si una convocatoria de subvención es relevante para una empresa que vende
collares GPS y de monitorización para ganado bovino, ovino y caprino.
 
Responde ÚNICAMENTE con un objeto JSON con este formato exacto, sin texto adicional:
{
  "relevante": true/false,
  "puntuacion": 1-10,
  "prioridad": "alta"/"media"/"baja",
  "ccaa_normalizada": "nombre oficial de la CCAA o 'Nacional'",
  "importe_max_estimado": numero_o_null,
  "pct_subvencion_estimado": numero_o_null,
  "fecha_cierre_estimada": "YYYY-MM-DD o null",
  "notas": "resumen de 1-2 frases con los requisitos clave"
}
 
Criterios de relevancia:
- Alta (8-10): menciona explícitamente collares, monitorización animal, IoT ganadero, tecnología wearable animal
- Media (5-7): digitalización sector primario, modernización explotaciones ganaderas, agricultura de precisión
- Baja (1-4): genérica de digitalización empresas, no específica de ganadería
- No relevante: no tiene ninguna relación con ganadería o sector primario"""
 
 
def classify_with_ai(raw: dict) -> dict | None:
    """Clasifica una subvención con Claude. Devuelve None si no es relevante."""
    texto = f"""
Nombre: {raw.get('nombre','')}
Organismo: {raw.get('organismo','')}
CCAA: {raw.get('ccaa','')}
Descripción adicional: {raw.get('descripcion','')}
Fecha cierre: {raw.get('fecha_cierre','')}
Importe total: {raw.get('importe_max','')}
"""
    try:
        msg = claude.messages.create(
            model="claude-haiku-4-5-20251001",   # Haiku es suficiente y más barato
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": texto}],
        )
        result = json.loads(msg.content[0].text)
        if not result.get("relevante"):
            return None
        return result
    except Exception as e:
        log.warning(f"Error clasificando '{raw.get('nombre','')}': {e}")
        return None
 
 
# ── 3. Upsert en Supabase ────────────────────────────────────────────────────
def upsert_subvencion(raw: dict, ai: dict) -> str:
    """Inserta o actualiza la subvención. Devuelve 'nueva' o 'actualizada'."""
    record = {
        "nombre":           raw["nombre"],
        "ccaa":             ai.get("ccaa_normalizada") or raw.get("ccaa", "Nacional"),
        "organismo":        raw.get("organismo"),
        "fuente":           raw.get("fuente", "BDNS"),
        "bdns_id":          raw.get("bdns_id"),
        "url_convocatoria": raw.get("url_convocatoria"),
        "url_boletin":      raw.get("url_boletin"),
        "fecha_apertura":   raw.get("fecha_apertura"),
        "fecha_cierre":     ai.get("fecha_cierre_estimada") or raw.get("fecha_cierre"),
        "importe_max":      ai.get("importe_max_estimado") or raw.get("importe_max"),
        "pct_subvencion":   ai.get("pct_subvencion_estimado"),
        "estado":           calcular_estado(ai.get("fecha_cierre_estimada") or raw.get("fecha_cierre")),
        "prioridad":        ai.get("prioridad", "media"),
        "puntuacion_ia":    ai.get("puntuacion"),
        "notas":            ai.get("notas"),
        "palabras_clave":   raw.get("palabras_clave", []),
    }
    record = {k: v for k, v in record.items() if v is not None}
 
    existing = supabase.table("subvenciones").select("id").eq("bdns_id", raw.get("bdns_id", "")).execute()
    if existing.data:
        supabase.table("subvenciones").update(record).eq("bdns_id", raw["bdns_id"]).execute()
        return "actualizada"
    else:
        supabase.table("subvenciones").insert(record).execute()
        return "nueva"
 
 
def calcular_estado(fecha_cierre_str: str | None) -> str:
    if not fecha_cierre_str:
        return "abierta"
    try:
        cierre = datetime.date.fromisoformat(str(fecha_cierre_str)[:10])
        hoy    = datetime.date.today()
        if cierre < hoy:
            return "cerrada"
        if cierre <= hoy + datetime.timedelta(days=60):
            return "abierta"
        return "proxima"
    except Exception:
        return "abierta"
 
 
# ── 4. Runner principal ──────────────────────────────────────────────────────
def run():
    log.info("=== Iniciando scraper de subvenciones ===")
    run_log = supabase.table("scraper_runs").insert({"log": "iniciado"}).execute()
    run_id  = run_log.data[0]["id"]
 
    nuevas = actualizadas = errores = 0
    vistas: set[str] = set()
 
    for keyword in KEYWORDS:
        log.info(f"Buscando: '{keyword}'")
        items = fetch_bdns(keyword)
        log.info(f"  → {len(items)} resultados en BDNS")
 
        for item in items:
            raw = parse_bdns_item(item)
            bdns_id = raw.get("bdns_id", "")
 
            if bdns_id in vistas:
                continue
            vistas.add(bdns_id)
 
            ai = classify_with_ai(raw)
            if ai is None:
                continue
 
            try:
                resultado = upsert_subvencion(raw, ai)
                if resultado == "nueva":
                    nuevas += 1
                    log.info(f"  ✓ NUEVA [{ai['puntuacion']}/10] {raw['nombre'][:60]}")
                else:
                    actualizadas += 1
            except Exception as e:
                errores += 1
                log.error(f"  ✗ Error guardando '{raw['nombre'][:40]}': {e}")
 
        time.sleep(1)   # Pausa cortés entre keywords
 
    supabase.table("scraper_runs").update({
        "finalizado_en": datetime.datetime.utcnow().isoformat(),
        "nuevas":        nuevas,
        "actualizadas":  actualizadas,
        "errores":       errores,
        "log":           f"OK — {nuevas} nuevas, {actualizadas} actualizadas, {errores} errores",
    }).eq("id", run_id).execute()
 
    log.info(f"=== Fin: {nuevas} nuevas, {actualizadas} actualizadas, {errores} errores ===")
 
 
if __name__ == "__main__":
    run()
