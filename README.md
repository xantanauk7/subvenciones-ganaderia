# Monitor de Subvenciones Ganadería Digital

Sistema automatizado para detectar subvenciones de digitalización ganadera
en todas las comunidades autónomas de España cada 14 días.

---

## Arquitectura

```
Fuentes (BDNS, BOE, Boletines CCAA)
        ↓  cada 14 días (GitHub Actions)
  scraper.py  →  Claude IA clasifica  →  Supabase (PostgreSQL)
                                               ↓
                                     dashboard/index.html
                                     (cualquier compañero, sin cuenta)
```

---

## Puesta en marcha en 4 pasos

### Paso 1 — Crear proyecto en Supabase (gratis)

1. Ve a https://supabase.com y crea una cuenta gratuita
2. Crea un nuevo proyecto (elige región "West EU - Frankfurt")
3. Ve a **SQL Editor** y pega el contenido de `schema.sql` → ejecutar
4. Ve a **Project Settings → API** y copia:
   - `Project URL` → tu `SUPABASE_URL`
   - `anon public` key → para el dashboard
   - `service_role` key → para el scraper (¡no compartir!)

### Paso 2 — Subir el código a GitHub

```bash
git init
git add .
git commit -m "Monitor subvenciones ganadería"
git remote add origin https://github.com/TU_ORG/subvenciones-ganaderia
git push -u origin main
```

### Paso 3 — Configurar secretos en GitHub Actions

En tu repo GitHub → **Settings → Secrets → Actions → New repository secret**:

| Nombre                | Valor                              |
|-----------------------|------------------------------------|
| `SUPABASE_URL`        | https://xxxx.supabase.co           |
| `SUPABASE_SERVICE_KEY`| eyJ... (service_role key)          |
| `ANTHROPIC_API_KEY`   | sk-ant-...                         |

A partir de aquí el scraper corre automáticamente los días 1 y 15 de cada mes.
También puedes lanzarlo manualmente en GitHub → Actions → "Run workflow".

### Paso 4 — Publicar el dashboard para el equipo

**Opción A — GitHub Pages (gratis, 2 minutos):**
1. GitHub → Settings → Pages → Source: `main` branch → `/dashboard`
2. URL resultante: `https://TU_ORG.github.io/subvenciones-ganaderia`
3. Cualquier compañero abre esa URL, introduce la URL y anon key de Supabase una vez, y ya ve todos los datos en tiempo real.

**Opción B — Simplemente abrir el archivo:**
Cualquiera puede abrir `dashboard/index.html` directamente en el navegador.
La primera vez pedirá la URL y clave de Supabase (anon/public, no la secreta).

---

## Estructura del proyecto

```
subvenciones-ganaderia/
├── schema.sql                    # Crear tablas en Supabase
├── .env.example                  # Plantilla de variables de entorno
├── .github/
│   └── workflows/
│       └── scraper.yml           # Cron automático cada 14 días
├── scraper/
│   ├── scraper.py                # Scraper + clasificador IA
│   └── requirements.txt
└── dashboard/
    └── index.html                # Dashboard web (sin backend)
```

---

## Ejecutar el scraper manualmente

```bash
cd scraper
cp ../.env.example ../.env       # Rellenar con tus claves reales
pip install -r requirements.txt
python scraper.py
```

---

## Coste estimado

| Servicio      | Coste                                      |
|---------------|--------------------------------------------|
| Supabase      | Gratis (hasta 500 MB y 2 proyectos)        |
| GitHub Actions| Gratis (2.000 min/mes en repos públicos)   |
| Claude API    | ~€0.10–0.30 por ejecución (Haiku model)    |
| **Total/mes** | **< €1**                                   |

---

## Personalización

- **Añadir más fuentes**: edita la función `fetch_bdns()` en `scraper.py`
  o añade funciones nuevas para boletines autonómicos específicos.
- **Ajustar palabras clave**: modifica la lista `KEYWORDS` en `scraper.py`.
- **Cambiar precio collar**: la estimación de collares usa €800/unidad.
  Cámbialo en `schema.sql` (columna `collares_est`) y en `scraper.py`.
- **Alertas por email**: añade un paso en `scraper.yml` que envíe un email
  con SendGrid o Resend cuando `nuevas > 0`.
