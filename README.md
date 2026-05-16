# Licitaciones Andalucía — Higiofi

Monitorización diaria de licitaciones públicas en Andalucía a partir de la
Plataforma de Contratación del Sector Público (PLACSP), con filtrado por
provincia, CPV e importe, análisis de pliegos con Claude (modo Max manual por
ahora) y dashboard web para que el equipo decida qué licitaciones perseguir.

## Estado actual (V1)

- [x] Bootstrap (config, esquema SQLite, logging)
- [x] Ingesta PLACSP (descarga ZIP mensual + parser CODICE 2.07)
- [x] Filtros duros (provincia · CPV · importe · fecha)
- [x] Descarga y extracción de pliegos PDF
- [x] Pipeline diario orquestado
- [x] Dashboard web con filtros, estados y notas
- [ ] Análisis IA automatizado vía API Anthropic *(de momento manual con Claude Max)*
- [ ] Publicación del dashboard en GitHub Pages
- [ ] Email diario con resumen (Resend)

## Puesta en marcha

```powershell
# 1. Entorno virtual
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Dependencias
pip install -r requirements.txt

# 3. Configuración
copy .env.example .env
# (editar .env si hace falta — para el flujo sin IA basta con los defaults)

# 4. Inicializar base de datos
python scripts\init_db.py

# 5. Backfill opcional de meses pasados (~3 min por mes)
python scripts\backfill.py --meses 1

# 6. Generar el dashboard por primera vez
python scripts\generar_dashboard.py
```

## Pipeline diario

El entrypoint que ejecuta el Task Scheduler cada mañana es
`scripts/pipeline_diario.py`. Su flujo:

1. Cierra ejecuciones huérfanas (marcaba como `en_curso` algún backfill abortado)
2. Descarga el ZIP del mes en curso desde PLACSP (`force=True`, PLACSP lo actualiza a diario)
3. Parsea todos los `.atom` del ZIP y hace upsert en la tabla `licitaciones`
4. Reaplica los filtros sobre toda la DB (por si cambiaron los CPVs)
5. Descarga pliegos PDF de las candidatas vivas que aún no tienen
6. Extrae texto de cada pliego a `.txt` para que el análisis manual sea rápido
7. Regenera `dashboard/index.html` con los datos reales
8. Registra el resumen en la tabla `log_ejecuciones`

Para lanzarlo manualmente:

```powershell
.venv\Scripts\python.exe scripts\pipeline_diario.py
```

Flags útiles para depurar:

```
--omitir-descarga    No baja el ZIP, reutiliza el local
--omitir-pliegos     Salta la descarga de PDFs
```

### Programar el cron en Windows (Task Scheduler)

Con el script proporcionado:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\programar_tarea_windows.ps1
```

Esto crea la tarea `Higiofi-Licitaciones-Diario` que se ejecuta cada día a las
07:00 incluso si el equipo estaba apagado a esa hora (se lanza al encenderlo).

Comandos útiles:

```powershell
# Ejecutar ahora mismo
Start-ScheduledTask -TaskName "Higiofi-Licitaciones-Diario"

# Ver estado / última ejecución
Get-ScheduledTask -TaskName "Higiofi-Licitaciones-Diario" | Get-ScheduledTaskInfo

# Ver el log del día
Get-Content "logs\pipeline_$((Get-Date).ToString('yyyy-MM-dd')).log" -Tail 30

# Eliminar la tarea
Unregister-ScheduledTask -TaskName "Higiofi-Licitaciones-Diario" -Confirm:$false
```

## Análisis IA manual (modo Max actual)

El pipeline diario deja todas las candidatas vivas con sus pliegos descargados
y texto extraído. Para procesarlas con Claude:

1. Abre Claude Code dentro de la carpeta `licitaciones-andalucia/`
2. Pide *"analiza las candidatas vivas sin análisis IA y guarda el resultado
   en `analisis_ia`"*. Claude leerá los `.txt` de los pliegos, comparará con
   `prompts/catalogo_empresa.txt`, generará el JSON con el prompt de
   `prompts/evaluacion_licitacion.md` y lo persistirá
3. Regenera el dashboard: `python scripts\generar_dashboard.py`

Cuando se quiera pasar a API automatizada, basta con:
1. Crear cuenta en console.anthropic.com y obtener `ANTHROPIC_API_KEY`
2. Ponerla en `.env`
3. Sustituir el paso manual por `python scripts\analizar_candidatas.py` dentro
   del pipeline diario (ya está escrito, solo falta integrarlo)

## Auditoría del estado

```powershell
python scripts\auditoria.py
```

Muestra licitaciones en DB, pliegos descargados, errores conocidos, ejecuciones
registradas y consumo de disco.

## Estructura del proyecto

```
src/
  config.py           Carga .env y constantes (CPVs, provincias)
  logging_setup.py    Configura logs a fichero+consola
  db.py               Esquema SQLite
  db_utils.py         Helpers comunes (cerrar ejecuciones huérfanas, etc.)
  modelos.py          Dataclasses del dominio
  ingesta/            Downloader PLACSP + parser CODICE + persistencia
  filtros/            Reglas duras (provincia · CPV · importe · fecha)
  pliegos/            Descarga PDFs + extracción de texto
  ia/                 Cliente Claude (para API) + analizador (para cuando se active)
  correo/             Envío con Resend (pendiente)
  airtable_sync.py    (vacío — no usamos Airtable por ahora)
prompts/
  evaluacion_licitacion.md   Prompt principal de evaluación
  catalogo_empresa.txt       Catálogo de Higiofi en texto plano (cacheado en IA)
  catalogo_empresa.pdf       PDF original (excluido del repo, 64 MB)
dashboard/
  template.html       Plantilla con placeholders
  index.html          Generado por generar_dashboard.py — abrir en navegador
scripts/
  init_db.py                  Crea esquema SQLite
  backfill.py                 Carga histórica de N meses
  pipeline_diario.py          Entrypoint del cron diario
  generar_dashboard.py        Genera dashboard/index.html desde la DB
  bajar_todas_pliegos.py      Reintenta pliegos de las candidatas
  descargar_y_extraer.py      Pliegos de IDs concretos (para inspección manual)
  auditoria.py                Estado general del sistema
  inspeccionar_db.py          Resumen de candidatas y motivos de descarte
  preparar_catalogo.py        Extrae texto del catálogo PDF de la empresa
  programar_tarea_windows.ps1 Crea la tarea programada
data/
  licitaciones.db     SQLite con todas las tablas
  zips/               ZIPs mensuales de PLACSP cacheados
  pliegos/            PDFs y .txt extraídos de los pliegos
logs/
  pipeline_YYYY-MM-DD.log     Log diario rotado
```
