# Subproyecto `mapa` — clientes potenciales Higiofi

Mapa interactivo (Leaflet + GitHub Pages) con todos los compradores reales
o potenciales del catálogo Higiofi en Andalucía: colegios, ayuntamientos,
guarderías y hoteles con zona infantil. Complementa el dashboard de
licitaciones cubriendo el canal "puerta a puerta / email comercial".

## Alcance V1

- **Provincia única: Cádiz.** Validamos pipeline + UX y luego ampliamos al
  resto de provincias (todas menos Almería).
- Tipos de cliente: colegios (públicos, concertados, privados, todas las
  etapas), ayuntamientos, guarderías privadas, hoteles familiares (estos
  últimos ocultos por defecto en el frontend).

## Fuentes de datos

| Tipo | Fuente | Bloque |
|---|---|---|
| Colegios | Catálogo de Centros de la Junta de Andalucía | 1 |
| Ayuntamientos | INE (municipios) + MPTFP (contactos) | 2 |
| Guarderías privadas | OpenStreetMap vía Overpass API | 3 |
| Hoteles familiares | OpenStreetMap vía Overpass API | 3 |
| Geocoding fallback | Nominatim (1 req/s, cache en `data/mapa/geocache.db`) | 4 |

## Pipeline

```powershell
# 0. Esquema SQLite (ya hecho)
python scripts\mapa\init_db.py

# 1-3. Ingestas por fuente (cada una idempotente con upsert por id)
python scripts\mapa\descargar_centros.py        # colegios (Junta)
python scripts\mapa\descargar_ayuntamientos.py  # municipios (INE+MPTFP)
python scripts\mapa\descargar_osm.py            # guarderías + hoteles

# 4. Geocoding de los que falten coordenadas
python scripts\mapa\geocodificar.py

# 5. Exportar a GeoJSON para el frontend
python scripts\mapa\generar_geojson.py
```

## Estructura

```
data/mapa/
    clientes.db        SQLite con la tabla `clientes` (staging)
    geocache.db        Cache de Nominatim
docs/mapa/
    index.html         Frontend Leaflet
    data/clientes.geojson   Generado por generar_geojson.py
src/mapa/
    config_mapa.py     Rutas, provincias, tipos, fuentes
    modelos.py         Dataclass ClientePotencial
    db.py              Conexión SQLite + DDL
scripts/mapa/
    init_db.py         Bloque 0 (este)
    descargar_*.py     Bloques 1-3 (ingestas)
    geocodificar.py    Bloque 4
    generar_geojson.py Bloque 5
```

## Estado interno (estados comerciales)

Los estados (*Sin contactar · Contactado · Oferta enviada · Cliente ·
Descartado*) y las notas se guardan en `localStorage` del navegador con clave
por `id` de cliente. **No viven en SQLite ni en el GeoJSON** — así regenerar
los datos no destruye el trabajo de la comercial.
