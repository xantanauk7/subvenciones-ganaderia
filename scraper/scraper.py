"""
scraper.py v2 — Monitor subvenciones ganadería digital
"""

import os
import json
import logging
import datetime
import time
from anthropic import Anthropic
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
claude   = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

KEYWORDS = [
    "digitalización ganadería",
    "tecnología ganadera",
    "monitorización ganado",
    "collar geolocalización ganado",
    "modernización explotación ganadera",
    "ganadería 4.0",
    "sector primario digital",
    "precision livestock",
]

SYSTEM_PROMPT = """Eres un experto en subvenciones para digitalización del sector ganadero español.
Analiza si una convocatoria es relevante para una empresa que vende collares GPS y de monitorización para ganado bovino, ovino y caprino.

Responde ÚNICAMENTE con JSON sin texto adicional:
{
  "relevante": true/false,
  "puntuacion": 1-10,
  "prioridad": "alta"/"media"/"baja",
  "ccaa_normalizada": "nombre oficial CCAA o Nacional",
  "importe_max_estimado": numero_o_null,
  "pct_subvencion_estimado": numero_o_null,
  "fecha_cierre_estimada": "YYYY-MM-DD o null",
  "notas": "resumen 1-2 frases con requisitos clave"
}

Alta (8-10): menciona collares, monitorización animal, IoT ganadero, wearable animal
Media (5-7): digitalización sector primario, modernización explotaciones ganaderas
Baja (1-4): genérica digitalización empresas, no específica de ganadería
No relevante: sin relación con ganadería"""


def fetch_bdns(keyword: str) -> list[dict]:
    import urllib.request
    import urllib.parse

    base = "https://www.infosubvenciones.es/bdnstrans/GE/es/convocatorias.json"
    params = urllib.parse.urlencode({
        "descripcion": keyword,
        "pageNumber": 0,
        "pageSize": 20,
    })
    url = f"{base}?{params}"

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ["convocatorias", "content", "data", "results"]:
                if key in data and isinstance(data[key], list):
                    return data[key]
        return []
    except Exception as e:
        log.warning(f"BDNS error '{keyword}': {e}")
        return []


def parse_item(item: dict) -> dict:
    def get(*keys):
        for k in keys:
            if k in item and item[k]:
                return item[k]
        return None

    return {
        "nombre":          str(get("descripcion", "titulo", "desConvocatoria", "title") or "Sin nombre"),
        "organismo":       str(get("organo", "desOrgano", "organismo", "organ") or ""),
        "ccaa":            str(get("nombreCCAA", "ccaa", "region", "comunidad") or "Nacional"),
        "fuente":          "BDNS",
        "bdns_id":         str(get("id", "idConvocatoria", "codigo", "bdns_id") or ""),
        "url_convocatoria": f"https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/{get('id','idConvocatoria') or ''}",
        "fecha_apertura":  str(get("fechaInicioSolicitud", "fecInicioSolicitud", "inicio") or ""),
        "fecha_cierre":    str(get("fechaFinSolicitud", "fecFinSolicitud", "fin", "deadline") or ""),
        "importe_max":     get("importeTotal", "importe", "budget", "presupuesto"),
        "descripcion":     str(get("descripcion", "titulo", "title") or ""),
    }


def classify(raw: dict):
    texto = f"""Nombre: {raw['nombre']}
Organismo: {raw['organismo']}
CCAA: {raw['ccaa']}
Fecha cierre: {raw['fecha_cierre']}
Importe: {raw['importe_max']}"""
    try:
        msg = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": texto}],
        )
        result = json.loads(msg.content[0].text)
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
        if cierre < hoy:
            return "cerrada"
        if cierre <= hoy + datetime.timedelta(days=60):
            return "abierta"
        return "proxima"
    except:
        return "abierta"


def upsert(raw: dict, ai: dict) -> str:
    record = {
        "nombre":           raw["nombre"],
        "ccaa":             ai.get("ccaa_normalizada") or raw.get("ccaa", "Nacional"),
        "organismo":        raw.get("organismo"),
        "fuente":           "BDNS",
        "bdns_id":          raw.get("bdns_id") or None,
        "url_convocatoria": raw.get("url_convocatoria"),
        "fecha_apertura":   raw.get("fecha_apertura") or None,
        "fecha_cierre":     ai.get("fecha_cierre_estimada") or raw.get("fecha_cierre") or None,
        "importe_max":      ai.get("importe_max_estimado") or raw.get("importe_max"),
        "pct_subvencion":   ai.get("pct_subvencion_estimado"),
        "estado":           calcular_estado(ai.get("fecha_cierre_estimada") or raw.get("fecha_cierre")),
        "prioridad":        ai.get("prioridad", "media"),
        "puntuacion_ia":    ai.get("puntuacion"),
        "notas":            ai.get("notas"),
    }
    record = {k: v for k, v in record.items() if v is not None and v != ""}

    bdns_id = raw.get("bdns_id", "")
    if bdns_id:
        existing = supabase.table("subvenciones").select("id").eq("bdns_id", bdns_id).execute()
        if existing.data:
            supabase.table("subvenciones").update(record).eq("bdns_id", bdns_id).execute()
            return "actualizada"

    supabase.table("subvenciones").insert(record).execute()
    return "nueva"


def run():
    log.info("=== Iniciando scraper v2 ===")
    run_log = supabase.table("scraper_runs").insert({"log": "iniciado v2"}).execute()
    run_id  = run_log.data[0]["id"]

    nuevas = actualizadas = errores = 0
    vistas: set[str] = set()

    for keyword in KEYWORDS:
        log.info(f"Buscando: '{keyword}'")
        items = fetch_bdns(keyword)
        log.info(f"  → {len(items)} resultados")

        for item in items:
            raw = parse_item(item)
            uid = raw.get("bdns_id") or raw["nombre"][:50]
            if uid in vistas:
                continue
            vistas.add(uid)

            ai = classify(raw)
            if ai is None:
                continue

            try:
                resultado = upsert(raw, ai)
                if resultado == "nueva":
                    nuevas += 1
                    log.info(f"  ✓ NUEVA [{ai['puntuacion']}/10] {raw['nombre'][:60]}")
                else:
                    actualizadas += 1
            except Exception as e:
                errores += 1
                log.error(f"  ✗ Error: {e}")

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
