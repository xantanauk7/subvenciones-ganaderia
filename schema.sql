-- ============================================================
-- MONITOR DE SUBVENCIONES GANADERÍA DIGITAL
-- Ejecutar en Supabase → SQL Editor
-- ============================================================

create table if not exists subvenciones (
  id              uuid primary key default gen_random_uuid(),
  nombre          text not null,
  ccaa            text not null,
  organismo       text,
  estado          text not null default 'nueva'
                    check (estado in ('nueva','abierta','proxima','cerrada')),
  prioridad       text not null default 'media'
                    check (prioridad in ('alta','media','baja')),
  fecha_cierre    date,
  fecha_apertura  date,
  importe_max     numeric,
  pct_subvencion  integer,
  collares_est    integer generated always as (
                    floor((importe_max * pct_subvencion / 100.0) / 800)
                  ) stored,
  url_convocatoria text,
  url_boletin      text,
  notas           text,
  palabras_clave  text[],
  puntuacion_ia   integer,        -- 1-10, relevancia estimada por el clasificador
  fuente          text,           -- 'BDNS', 'BOE', 'BOJA', etc.
  bdns_id         text unique,    -- ID de la BDNS para evitar duplicados
  creado_en       timestamptz default now(),
  actualizado_en  timestamptz default now()
);

-- Actualizacion automatica de updated_at
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.actualizado_en = now();
  return new;
end;
$$;

create trigger trg_subvenciones_updated
  before update on subvenciones
  for each row execute function set_updated_at();

-- Tabla de ejecuciones del scraper (log de cada pasada)
create table if not exists scraper_runs (
  id          uuid primary key default gen_random_uuid(),
  iniciado_en timestamptz default now(),
  finalizado_en timestamptz,
  nuevas      integer default 0,
  actualizadas integer default 0,
  errores     integer default 0,
  log         text
);

-- Habilitar Row Level Security (lectura pública, escritura solo con service key)
alter table subvenciones enable row level security;
alter table scraper_runs  enable row level security;

create policy "lectura publica" on subvenciones
  for select using (true);

create policy "escritura service role" on subvenciones
  for all using (auth.role() = 'service_role');

create policy "lectura publica runs" on scraper_runs
  for select using (true);

create policy "escritura service role runs" on scraper_runs
  for all using (auth.role() = 'service_role');

-- Índices útiles
create index idx_subvenciones_ccaa     on subvenciones(ccaa);
create index idx_subvenciones_estado   on subvenciones(estado);
create index idx_subvenciones_prioridad on subvenciones(prioridad);
create index idx_subvenciones_cierre   on subvenciones(fecha_cierre);
