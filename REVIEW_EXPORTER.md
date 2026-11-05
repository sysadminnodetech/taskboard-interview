# Code review: `review/export_report.py`

**Veredicto: no aprobaría el merge.** Hay una inyección SQL, un secreto en el repositorio y un endpoint sin autenticación que borra todas las tareas. Además, el export no funciona con el esquema actual.

**Orden de revisión:** primero los bloqueantes (1 a 5), después las mejoras (6 a 11).

## Puntos a mencionar

1. **Credenciales:** hay que rotar la clave, porque ya está en el historial de git. Si se agrega un `.env` para gestionarlas, también hay que agregar un `.gitignore` para que no se suban al repositorio.
2. **Export:** consulta la tabla `comments`, que todavía no existe, así que hay que quitar el conteo.
3. **Delete tasks:** responde mal, porque devuelve `deleted: true` aunque el borrado haya fallado.
4. **Base de datos equivocada:** `DB_PATH` ignora `TASKBOARD_DB` y es relativa al directorio de trabajo, así que puede conectarse a otra base.

## Resumen

| # | Severidad | Línea | Problema |
|---|-----------|-------|----------|
| 1 | Bloqueante | 13 | Secreto hardcodeado y ya presente en el historial de git |
| 2 | Bloqueante | 20 | Inyección SQL en la búsqueda |
| 3 | Bloqueante | 40-48 | `DELETE /api/tasks` borra todo y no exige permisos |
| 4 | Bloqueante | 46-48 | Se ocultan los errores y se responde `deleted: true` |
| 5 | Bloqueante | 33-35 | El export consulta una tabla `comments` que no existe |
| 6 | Alta | 12 | La ruta de la base ignora la configuración de la app |
| 7 | Alta | 18, 27, 43 | Las conexiones nunca se cierran |
| 8 | Media | 28-36 | Consultas N+1 en el export |
| 9 | Media | 36 | El export devuelve tuplas en lugar de objetos |
| 10 | Media | 20, 28 | Resultados sin límite ni orden, y `%` y `_` sin escapar |
| 11 | Media | Todo el archivo | Sin tests |

## Bloqueantes

### 1. Secreto en el código (línea 13)

`ADMIN_API_KEY` tiene una clave con formato de producción (`sk-live-...`). Además, nada la usa.

- **Riesgo:** cualquiera con acceso al repositorio puede leerla. Borrar la línea no alcanza, porque ya está en el commit `5d4ecc2`.
- **Cambio, en este orden:**
  1. Revocar y rotar la clave, porque la actual debe considerarse comprometida.
  2. Leer la nueva clave desde una variable de entorno y no escribirla nunca en el código.
  3. Añadir `.env` a un `.gitignore`. El repositorio no tiene ninguno, así que hoy un `.env` se podría commitear por accidente.
  4. Añadir un escáner de secretos al CI.
- **Si falta la variable:** la aplicación debe fallar de forma explícita. No debe usar un valor por defecto ni dejar el endpoint abierto.

```python
ADMIN_API_KEY = os.environ["ADMIN_API_KEY"]
```

### 2. Inyección SQL (línea 20)

`q` llega del usuario y se interpola en la consulta con un f-string.

- **Riesgo:** con `q=' UNION SELECT id, title, description FROM tasks --` el atacante cambia la consulta y puede leer otras columnas o tablas.
- **Cambio:** aplicar dos capas de validación. Cada una cubre algo distinto, y solo la primera previene la inyección.

| Capa | Qué hace | Qué protege |
|------|----------|-------------|
| Consulta parametrizada | La base trata `q` como dato, nunca como SQL | Inyección SQL |
| Validación de entrada | Limita la longitud de `q` y rechaza valores vacíos | Abuso y consultas sobre toda la tabla |

```python
@router.get("/api/tasks/search")
def search_tasks(q: str = Query(min_length=1, max_length=100)):
    ...
    conn.execute(
        "SELECT id, title, status FROM tasks WHERE title LIKE ?",
        (f"%{q}%",),
    )
```

No usaría una lista de caracteres prohibidos: se puede evadir y además rompe búsquedas legítimas, como un título con un apóstrofo (`Don't forget`).

### 3. Borrado masivo sin autenticación (líneas 40-48)

Cualquier cliente que alcance la API puede vaciar la tabla con una sola petición, y el borrado es irreversible.

- **Cambio:** exigir permisos con una dependencia de FastAPI, reutilizable en cualquier endpoint peligroso:

```python
def require_admin(x_api_key: str = Header()) -> None:
    if not secrets.compare_digest(x_api_key.encode(), ADMIN_API_KEY.encode()):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.delete("/api/tasks", dependencies=[Depends(require_admin)])
def delete_all_tasks():
    ...
```

- **Antes de eso:** preguntaría si el endpoint hace falta, porque ningún requisito lo pide y la app ya tiene `DELETE /api/tasks/{task_id}`. Si se mantiene, también añadiría una confirmación explícita y un registro de auditoría.
- **Otros endpoints:** `export_tasks` entrega un volcado completo de los datos, así que también debería exigir permisos. La app actual no tiene autenticación, de modo que esta sería su primera capa de permisos.
- **Límite de esta solución:** una clave compartida no identifica a quién hizo la acción. Si la app llega a tener usuarios, lo correcto es autenticación por usuario con roles.

### 4. Errores ocultos (líneas 46-48)

`except Exception: pass` seguido de `return {"deleted": True}` informa de un borrado exitoso aunque haya fallado.

- **Riesgo:** el cliente cree que los datos se borraron, y no queda ningún rastro del error.
- **Cambio:** quitar el `try/except` y dejar que el error llegue como 500, o capturar solo `sqlite3.Error`, registrarlo y devolver un error real.

### 5. El export consulta una tabla inexistente (líneas 33-35)

`get_conn()` en `app/main.py` solo crea `tasks`, y ningún archivo de `app/`, `tests/` o `static/` menciona `comments`. Con una base creada por la app, el endpoint falla con `sqlite3.OperationalError: no such table: comments`.

- **Cambio:** quitar el conteo, porque la app no tiene ninguna funcionalidad de comentarios. Cuando exista, el conteo se agrega junto con su migración y sus tests.
- **Descartado:** envolver la consulta en un `try/except`. El endpoint dejaría de fallar, pero devolvería `comments: 0` sin saber si es cierto, y podría ocultar otros errores de base de datos.

## Mejoras

### 6. Ruta de la base inconsistente (línea 12)

`DB_PATH = "tasks.db"` es relativa al directorio de trabajo e ignora `TASKBOARD_DB`. Si el proceso arranca desde otra carpeta, `sqlite3.connect` crea una base vacía, y los tests no usarían su base aislada.

**Cambio:** reutilizar `get_conn()` de `app/main.py`, que también aporta `row_factory` y la creación de la tabla.

### 7. Conexiones sin cerrar (líneas 18, 27 y 43)

Ninguna función cierra su conexión, así que dependen del recolector de basura.

**Cambio:** cerrarlas de forma explícita con `contextlib.closing`. Ojo: `with get_conn() as conn`, el patrón de `app/main.py`, gestiona la transacción pero no cierra la conexión, así que ese código tiene el mismo problema.

### 8. Consultas N+1 (líneas 28-36)

El export ejecuta 1 + 2N consultas, y la primera solo obtiene ids que después se vuelven a pedir uno por uno.

**Cambio:** al quitar el conteo (punto 5), alcanza con una sola consulta:

```sql
SELECT * FROM tasks ORDER BY id
```

### 9. Respuesta del export sin nombres de campo (línea 36)

Sin `row_factory`, cada `task` es una tupla y se serializa como un array. El cliente depende del orden de las columnas, que además cambia con `SELECT *` si se agrega un campo como `priority`.

**Cambio:** convertir con `dict(row)` y declarar un `response_model` con Pydantic.

### 10. Resultados sin límite ni orden (líneas 20 y 28)

- La búsqueda y el export devuelven todas las filas, sin orden definido. Una `q` vacía devuelve toda la tabla.
- `%` y `_` en `q` actúan como comodines, incluso con parámetros.

**Cambio:** paginar como `GET /api/tasks`, usar `ORDER BY id`, validar `q` con `Query(min_length=1, max_length=...)` y escapar los comodines con `LIKE ? ESCAPE '\'`. Para un export grande, usar streaming.

### 11. Sin tests (todo el archivo)

Pediría tests para estos casos antes de aprobar:

- [ ] Una `q` con comillas y `UNION` se trata como texto literal.
- [ ] Una búsqueda con `%` solo encuentra títulos que contienen `%`.
- [ ] El export devuelve objetos con nombres de campo.
- [ ] Un fallo de base de datos no devuelve `deleted: true`.
- [ ] Una `q` vacía o demasiado larga devuelve 422.
- [ ] Una petición con una clave incorrecta recibe 403 y no borra tareas.
- [ ] Una petición sin clave es rechazada y no borra tareas.

## Al conectar el módulo

`app/main.py` monta `StaticFiles` en `/` en su última línea. `app.include_router(router)` debe ir antes de ese montaje, porque si no el montaje captura las rutas de este módulo.

## Comentario general para el autor

El archivo indica que se generó con IA, y eso no cambia el estándar de revisión. La mayoría de estos problemas se evita siguiendo los patrones que `app/main.py` ya tiene: `get_conn()` y consultas parametrizadas.
