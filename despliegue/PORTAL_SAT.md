# Sincronización de recibidas con el portal del SAT

Consulta el portal "Consulta y recuperación de comprobantes" con la e.firma
de Monsort varias veces al día y guarda las facturas recibidas (con su XML)
en `FacturasRecibidas`. Reemplaza la espera de la Descarga Masiva para el
día a día; la Descarga Masiva y la verificación por UUID quedan de respaldo.

## Piezas

| Archivo | Qué hace |
|---|---|
| `Backend/sat_portal/consultar_portal.php` | Habla con el portal (librería `phpcfdi/cfdi-sat-scraper`). Devuelve JSON. No toca la base. |
| `Backend/sat_portal/composer.json` | Dependencias de PHP. `vendor/` no va en git. |
| `Backend/app/services/portal_sat_service.py` | Llama al PHP, valida, guarda, registra cada corrida. CLI de prueba. |
| `Backend/app/modelos/sincronizacion_portal.py` | Bitácora de corridas. |
| `Backend/alembic/versions/b8d41f6a2c73_...py` | `xml_factura` y `fecha_vista_portal` en FacturasRecibidas + tabla `SincronizacionesPortal`. |
| `Backend/app/core/scheduler.py` | 9:07, 12:07, 15:07, 18:07 y 22:40 (últimos 5 días) + domingo 3:20 (últimos 90 días). Hora de México. |
| `Backend/app/api/rutas/facturas_recibidas.py` | Endpoints de estado, historial, sincronización manual y descarga de XML. |

## Reglas de escritura

- **Nuevas**: se insertan con `origen='portal'` y su XML.
- **Existentes** (Excel, Descarga Masiva): folio, emisor, fecha y monto **no se tocan**.
  El estatus (`sat_estado`, `fecha_cancelacion`) **sí se actualiza**.
  Tipo, PAC, nombres y XML **solo se rellenan si estaban vacíos**.
- Nunca corren dos a la vez (candado de Postgres).
- Un fallo (SAT caído, login rechazado) queda en `SincronizacionesPortal` y no rompe nada.

## Instalación en el VPS (una vez)

```bash
# 1. PHP y composer
sudo apt update
sudo apt install -y php-cli php-curl php-xml php-mbstring composer
php -v                       # 8.2 o mayor
php -m | grep -E "curl|dom|mbstring|openssl|fileinfo|json"   # deben salir las 6

# 2. Código
cd /srv/monsort/ProyectoMonsort
git pull --no-rebase origin main

# 3. Dependencias de PHP
cd Backend/sat_portal
composer install --no-dev --optimize-autoloader
ls vendor/autoload.php       # debe existir

# 4. Migración
cd /srv/monsort/ProyectoMonsort/Backend
/srv/monsort/.venv/bin/python -c "from alembic.config import main; main()" upgrade head
/srv/monsort/.venv/bin/python -c "from alembic.config import main; main()" current   # b8d41f6a2c73
```

5. En `.env` agregar (el portal queda **apagado** hasta probarlo):

```
SAT_PORTAL_ACTIVO=false
SAT_PORTAL_PHP=/usr/bin/php
```

6. `sudo systemctl restart monsort-api`

## Prueba de concepto (desde `Backend/`)

```bash
PY=/srv/monsort/.venv/bin/python

# a) ¿Entra al portal con la e.firma?
$PY -m app.services.portal_sat_service --verificar-sesion
```

Si falla con `dh key too small` o un error de SSL: agregar `SAT_PORTAL_SECLEVEL1=true`
al `.env` y repetir. Baja el nivel de cifrado **solo** para esta conexión.

```bash
# b) Un día de septiembre, sin escribir nada, comparado contra el Excel
$PY -m app.services.portal_sat_service --probar --desde 2026-09-01 --hasta 2026-09-01

# c) Lo mismo con emitidas (solo para ver que también funciona)
$PY -m app.services.portal_sat_service --probar --desde 2026-09-01 --hasta 2026-09-01 --tipo emitidos
```

En (b), "Solo en el portal" son facturas que el Excel no tenía. Si aparecen
OXXO, gasolineras, etc., el portal cubre lo que Gmail no.

## Carga inicial (abril a hoy), mes por mes

Un mes por corrida para no rebasar el tiempo límite (15 min). Baja el XML de
todo lo que no lo tenga, incluidas las 709 del Excel.

```bash
for MES in "2026-04-01 2026-04-30" "2026-05-01 2026-05-31" "2026-06-01 2026-06-30" \
           "2026-07-01 2026-07-31" "2026-08-01 2026-08-31" "2026-09-01 2026-09-30"; do
  set -- $MES
  $PY -m app.services.portal_sat_service --desde $1 --hasta $2 --apply
done
```

Cada corrida imprime `nuevas`, `actualizadas` y `xml_descargados`.

## Encender

1. En `.env`: `SAT_PORTAL_ACTIVO=true`
2. `sudo systemctl restart monsort-api`
3. Después de la siguiente hora programada:

```bash
$PY -m app.services.portal_sat_service --estado
journalctl -u monsort-api --since "1 hour ago" | grep "Portal SAT"
```

**Apagar** en cualquier momento: `SAT_PORTAL_ACTIVO=false` y reiniciar. Los datos se quedan.

## Endpoints (para el frontend)

| Método | Ruta | Uso |
|---|---|---|
| GET | `/facturas-recibidas/sincronizacion` | Encabezado de la tabla: `ultima_sincronizacion_exitosa`, `nuevas_ultimas_24h`, `en_curso`, y `alerta` (texto para mostrar tal cual si no es null). |
| POST | `/facturas-recibidas/sincronizacion` | Botón "Actualizar desde el SAT". Body opcional `{"dias": 5}`. Responde 202; 409 si ya hay una en curso; 429 si hubo una manual hace menos de 10 min. |
| GET | `/facturas-recibidas/sincronizacion/historial?limite=20` | Bitácora de corridas. |
| GET | `/facturas-recibidas/{id}/xml` | Descarga del XML. El listado y el detalle traen `tiene_xml` para mostrar u ocultar el botón. |

Nota: `origen` ahora puede ser `sat`, `excel` o `portal`.

## Cuando algo falla

```sql
SELECT id, inicio, motivo, estado, tipo_error, left(error, 120)
FROM "SincronizacionesPortal" ORDER BY inicio DESC LIMIT 10;
```

| tipo_error | Qué revisar |
|---|---|
| `configuracion` | `.env`, que exista `vendor/`, ruta de PHP. |
| `credencial` | e.firma vencida (vence 2027-02-20) o contraseña. |
| `login` | El SAT rechazó el acceso. Probar `--verificar-sesion`. |
| `portal` / `timeout` | SAT caído o lento. Se reintenta solo en la siguiente hora. |
| `interno` | PHP tronó (ver `error`): extensión faltante o el SAT cambió el portal → `composer update phpcfdi/cfdi-sat-scraper`. |
