"""
scraper.py v3 — Lee el BOE y boletines autonómicos
Usa la API oficial gratuita del BOE (boe.es/datosabiertos)
"""

import os
import json
import logging
import datetime
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from anthropic import Anthropic
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
claude   = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ── Palabras clave para filtrar en los boletines ─────────────────────────────
KEYWORDS = [
    "ganadería", "ganadero", "ganado", "bovino", "ovino", "caprino",
    "explotación ganadera", "sector primario", "digitalización agraria",
    "modernización agraria", "agricultura de precisión", "IoT ganadería",
    "monitorización animal", "collar", "trazabilidad animal",
    "ganadería 4.0", "precision livestock",
]

# ── Boletines autonómicos con RSS/XML público ────────────────────────────────
BOLETINES = {
    "BOE":              "https://www.boe.es/datosabiertos/api/boe/sumario/{fecha}",
    "BOJA":             "https://www.juntadeandalucia.es/eboja/rss/rss.xml",           # Andalucía
    "BOCYL":            "https://bocyl.jcyl.es/rss/subvenciones.do",                   # CyL
    "DOG":              "https://www.xunta.gal/dog/rss/subvenciones.xml",              # Galicia
    "BOA":              "https://www.boa.aragon.es/rss/rss_subvenciones.xml",          # Aragón
    "BOPV":             "https://www.euskadi.eus/bopv2/datos/rss/subvenciones.xml",    # País Vasco
    "BON":              "https://bon.navarra.es/es/rss/subvenciones.xml",              # Navarra
    "BOPA":             "https://sede.asturias.es/bopa/rss/subvenciones.xml",          # Asturias
    "DOE":              "https://doe.juntaex.es/rss/subvenciones.xml",                 # Extremadura
    "DOCM":             "https://docm.jccm.es/rss/subvenciones.xml",                   # CLM
}

SYSTEM_PROMPT = """Eres un experto en subvenciones para digitalización del sector ganadero español.
Analiza si una convocatoria de subvención es relevante para una empresa que vende
collares GPS y sensores de monitorización para ganado bovino, ovino y caprino.

Responde ÚNICAMENTE con JSON sin texto adicional ni markdown:
{
  "relevante": true,
  "puntuacion": 8,
  "prioridad": "alta",
  "ccaa_normalizada": "Andalucía",
  "importe_max_estimado": 50000,
  "pct_subvencion_estimado": 60,
  "fecha_cierre_estimada": "2026-09-30",
  "notas": "Ayuda para digitalización de explotaciones ganaderas. Cubre tecnología de monitorización animal."
}

Criterios:
- Alta (8-10): menciona collares, monitorización animal, IoT ganadero, wearable animal, trazabilidad ganadera
- Media (5-7): digitalización sector primario, modernización explotaciones, agricultura precisión, ganadería 4.0
- Baja (3-4): digitalización general pymes agrarias, sin mención específica de ganadería
- No relevante (relevante=false): sin relación con ganadería o tecnología agraria

Si no puedes estimar un campo numérico, usa null."""


# ── 1. BOE — API oficial ──────────────────────────────────────────────────────
def fetch_boe_sumario(fecha: datetime.date) -> list[dict]:
    """Descarga el sumario del BOE para una fecha y filtra por keywords ganaderas."""
    url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{fecha.strftime('%Y%m%d')}"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 SubvencionesScraper/3.0",
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())

        items = []
        # Recorrer la estructura del sumario BOE
        diario = data.get("data", {}).get("sumario", {}).get("diario", [])
        if isinstance(diario, dict):
            diario = [diario]

        for seccion in diario:
            secciones = seccion.get("seccion", [])
            if isinstance(secciones, dict):
                secciones = [secciones]
            for s in secciones:
                departamentos = s.get("departamento", [])
                if isinstance(departamentos, dict):
                    departamentos = [departamentos]
                for dept in departamentos:
                    epigrafe_list = dept.get("epigrafe", [])
                    if isinstance(epigrafe_list, dict):
                        epigrafe_list = [epigrafe_list]
                    for epi in epigrafe_list:
                        items_boe = epi.get("item", [])
                        if isinstance(items_boe, dict):
                            items_boe = [items_boe]
                        for item in items_boe:
                            titulo = str(item.get("titulo", "")).lower()
                            if any(kw.lower() in titulo for kw in KEYWORDS):
                                items.append({
                                    "nombre":    item.get("titulo", ""),
                                    "organismo": dept.get("@nombre", ""),
                                    "ccaa":      _detectar_ccaa(dept.get("@nombre", "") + " " + item.get("titulo", "")),
                                    "fuente":    "BOE",
                                    "bdns_id":   item.get("identificador", ""),
                                    "url_convocatoria": f"https://www.boe.es{item.get('urlPdf', {}).get('@valor', '')}",
                                    "url_boletin": f"https://www.boe.es/boe/dias/{fecha.strftime('%Y/%m/%d')}/",
                                    "fecha_cierre": None,
                                    "importe_max": None,
                                    "descripcion": item.get("titulo", ""),
                                })
        log.info(f"  BOE {fecha}: {len(items)} ítems relevantes encontrados")
        return items

    except Exception as e:
        log.warning(f"Error leyendo BOE {fecha}: {e}")
        return []


def fetch_boe_periodo(dias: int = 14) -> list[dict]:
    """Lee el BOE de los últimos N días."""
    items = []
    hoy = datetime.date.today()
    for i in range(dias):
        fecha = hoy - datetime.timedelta(days=i)
        if fecha.weekday() < 5:  # Solo días laborables
            items.extend(fetch_boe_sumario(fecha))
            time.sleep(0.5)
    return items


# ── 2. Boletines autonómicos — RSS ────────────────────────────────────────────
def fetch_rss(nombre: str, url: str) -> list[dict]:
    """Lee un RSS de boletín autonómico y filtra por keywords."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 SubvencionesScraper/3.0",
            "Accept": "application/rss+xml, application/xml, text/xml",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            content = r.read()

        root = ET.fromstring(content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = []

        # Soporta RSS 2.0 y Atom
        entries = root.findall(".//item") or root.findall(".//atom:entry", ns)

        for entry in entries:
            titulo = (
                (entry.findtext("title") or entry.findtext("atom:title", namespaces=ns) or "")
            )
            descripcion = (
                (entry.findtext("description") or entry.findtext("atom:summary", namespaces=ns) or "")
            )
            link = (
                (entry.findtext("link") or entry.findtext("atom:link", namespaces=ns) or "")
            )
            texto = (titulo + " " + descripcion).lower()

            if any(kw.lower() in texto for kw in KEYWORDS):
                items.append({
                    "nombre":         titulo,
                    "organismo":      nombre,
                    "ccaa":           _ccaa_from_boletin(nombre),
                    "fuente":         nombre,
                    "bdns_id":        None,
                    "url_convocatoria": link,
                    "url_boletin":    link,
                    "fecha_cierre":   None,
                    "importe_max":    None,
                    "descripcion":    descripcion[:500],
                })

        log.info(f"  {nombre}: {len(items)} ítems relevantes")
        return items

    except Exception as e:
        log.warning(f"  {nombre}: error ({e})")
        return []


# ── 3. Helpers ────────────────────────────────────────────────────────────────
CCAA_MAP = {
    "andaluc": "Andalucía", "junta de andalucía": "Andalucía",
    "aragón": "Aragón", "aragón": "Aragón",
    "asturias": "Asturias", "principado de asturias": "Asturias",
    "baleares": "Baleares", "illes balears": "Baleares",
    "canarias": "Canarias",
    "cantabria": "Cantabria",
    "castilla-la mancha": "Castilla-La Mancha", "castilla la mancha": "Castilla-La Mancha",
    "castilla y león": "Castilla y León", "castilla y leon": "Castilla y León",
    "cataluña": "Cataluña", "catalunya": "Cataluña",
    "comunitat valenciana": "Comunidad Valenciana", "comunidad valenciana": "Comunidad Valenciana",
    "extremadura": "Extremadura",
    "galicia": "Galicia",
    "la rioja": "La Rioja",
    "madrid": "Madrid",
    "murcia": "Murcia", "región de murcia": "Murcia",
    "navarra": "Navarra", "nafarroa": "Navarra",
    "país vasco": "País Vasco", "euskadi": "País Vasco",
}

def _detectar_ccaa(texto: str) -> str:
    texto_lower = texto.lower()
    for key, ccaa in CCAA_MAP.items():
        if key in texto_lower:
            return ccaa
    return "Nacional"

def _ccaa_from_boletin(nombre: str) -> str:
    mapa = {
        "BOJA": "Andalucía", "BOCYL": "Castilla y León", "DOG": "Galicia",
        "BOA": "Aragón", "BOPV": "País Vasco", "BON": "Navarra",
        "BOPA": "Asturias", "DOE": "Extremadura", "DOCM": "Castilla-La Mancha",
    }
    return mapa.get(nombre, "Nacional")


# ── 4. Clasificador IA ────────────────────────────────────────────────────────
def classify(raw: dict):
    texto = f"""Título: {raw['nombre']}
Organismo: {raw['organismo']}
CCAA: {raw['ccaa']}
Fuente: {raw['fuente']}
Descripción: {raw.get('descripcion', '')[:300]}
URL: {raw.get('url_convocatoria', '')}"""

    try:
        msg = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": texto}],
        )
        text = msg.content[0].text.strip()
        # Limpiar posible markdown
        if "```" in text:
            text = text.split("```")[1].replace("json", "").strip()
        result = json.loads(text)
        return result if result.get("relevante") else None
    except Exception as e:
        log.warning(f"Error clasificando '{raw['nombre'][:40]}': {e}")
        return None


# ── 5. Supabase ───────────────────────────────────────────────────────────────
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

    # Evitar duplicados por URL o bdns_id
    bdns_id = raw.get("bdns_id")
    url = raw.get("url_convocatoria", "")

    if bdns_id:
        record["bdns_id"] = bdns_id
        existing = supabase.table("subvenciones").select("id").eq("bdns_id", bdns_id).execute()
        if existing.data:
            supabase.table("subvenciones").update(record).eq("bdns_id", bdns_id).execute()
            return "actualizada"
    elif url:
        existing = supabase.table("subvenciones").select("id").eq("url_convocatoria", url).execute()
        if existing.data:
            return "duplicada"

    supabase.table("subvenciones").insert(record).execute()
    return "nueva"


# ── 6. Runner principal ───────────────────────────────────────────────────────
def run():
    log.info("=== Iniciando scraper v3 — BOE + boletines autonómicos ===")
    run_log = supabase.table("scraper_runs").insert({"log": "iniciado v3"}).execute()
    run_id  = run_log.data[0]["id"]

    nuevas = actualizadas = errores = 0
    vistas: set[str] = set()

    # --- BOE últimos 14 días ---
    log.info("Leyendo BOE (últimos 14 días)...")
    boe_items = fetch_boe_periodo(dias=14)
    log.info(f"BOE: {len(boe_items)} candidatos encontrados")

    # --- Boletines autonómicos por RSS ---
    rss_items = []
    for nombre, url in BOLETINES.items():
        if nombre == "BOE":
            continue
        items = fetch_rss(nombre, url)
        rss_items.extend(items)
        time.sleep(0.5)

    log.info(f"Boletines CCAA: {len(rss_items)} candidatos encontrados")

    all_items = boe_items + rss_items

    # --- Clasificar con IA y guardar ---
    for raw in all_items:
        uid = raw.get("bdns_id") or raw.get("url_convocatoria") or raw["nombre"][:60]
        if uid in vistas:
            continue
        vistas.add(uid)

        ai = classify(raw)
        if ai is None:
            continue

        log.info(f"  ✓ RELEVANTE [{ai.get('puntuacion')}/10] {raw['nombre'][:70]}")

        try:
            resultado = upsert(raw, ai)
            if resultado == "nueva":
                nuevas += 1
            elif resultado == "actualizada":
                actualizadas += 1
        except Exception as e:
            errores += 1
            log.error(f"  ✗ Error guardando: {e}")

        time.sleep(0.3)  # Pausa entre llamadas a Claude

    msg = f"OK — {nuevas} nuevas, {actualizadas} actualizadas, {errores} errores"
    supabase.table("scraper_runs").update({
        "finalizado_en": datetime.datetime.utcnow().isoformat(),
        "nuevas": nuevas, "actualizadas": actualizadas,
        "errores": errores, "log": msg,
    }).eq("id", run_id).execute()

    log.info(f"=== Fin: {msg} ===")


if __name__ == "__main__":
    run()
