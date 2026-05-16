# Licitaciones Andalucía

Monitorización diaria de licitaciones públicas en Andalucía a partir de la
Plataforma de Contratación del Sector Público (PLACSP), con filtrado por
provincia, CPV e importe, análisis de pliegos con Claude (Anthropic) y resumen
diario por correo.

## Estado del proyecto

V1 en construcción. Pasos completados / pendientes:

- [x] Bootstrap (config, esquema SQLite, logging)
- [ ] Ingesta PLACSP (descarga ZIP + parser CODICE 2.07)
- [ ] Filtros duros (provincia, CPV, importe, fecha)
- [ ] Descarga y extracción de pliegos
- [ ] Análisis IA con Claude Sonnet 4.6
- [ ] Sincronización con Airtable
- [ ] Envío de resumen diario con Resend
- [ ] Orquestación + Task Scheduler

## Puesta en marcha

```powershell
# 1. Entorno virtual
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Dependencias
pip install -r requirements.txt

# 3. Configuración
copy .env.example .env
# editar .env con las claves reales

# 4. Inicializar base de datos
python scripts/init_db.py
```

## Estructura

```
src/
  config.py          Carga .env y constantes
  logging_setup.py   Configuración de logs
  db.py              Conexión SQLite y helpers
  modelos.py         Dataclasses / Pydantic
  ingesta/           Descarga PLACSP + parser CODICE
  filtros/           Provincia / CPV / importe / fecha
  pliegos/           Descarga PDFs + extracción de texto
  ia/                Cliente Claude + analizador
  airtable_sync.py   Sincronización con Airtable
  correo/            Envío con Resend
  pipeline.py        Orquestación diaria
scripts/             Entrypoints (init_db, run_daily, backfill)
tests/               Pruebas
```
