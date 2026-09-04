#!/usr/bin/env bash
#
# Despliegue de Monsort.
#
#   sudo -u monsort /srv/monsort/despliegue.sh
#
# Idempotente: se puede correr las veces que sea.
# Hace respaldo de la base ANTES de migrar. Si algo falla, aborta sin
# dejar el sistema a medias.

set -euo pipefail

RAIZ="/srv/monsort/ProyectoMonsort"
VENV="/srv/monsort/.venv"
RESPALDOS="/srv/monsort/respaldos"
RAMA="${1:-main}"

BASE_DATOS="MonsortDB"
SERVICIO="monsort-api"

rojo()  { echo -e "\033[0;31m$*\033[0m"; }
verde() { echo -e "\033[0;32m$*\033[0m"; }
azul()  { echo -e "\033[0;34m==> $*\033[0m"; }

fallo() { rojo "FALLO: $*"; exit 1; }

# ---------------------------------------------------------------- checks

[[ -d "$RAIZ" ]] || fallo "No existe $RAIZ"
[[ -d "$VENV" ]] || fallo "No existe el entorno virtual en $VENV"
[[ -f "$RAIZ/Backend/.env" ]] || fallo "Falta Backend/.env"

mkdir -p "$RESPALDOS"

# ---------------------------------------------------------------- respaldo

azul "Respaldo de la base de datos"
SELLO=$(date +%Y%m%d_%H%M%S)
ARCHIVO="$RESPALDOS/monsort_$SELLO.sql.gz"

pg_dump "$BASE_DATOS" | gzip > "$ARCHIVO" \
    || fallo "No se pudo respaldar la base. Se aborta el despliegue."

verde "  $ARCHIVO ($(du -h "$ARCHIVO" | cut -f1))"

# Conserva los ultimos 14 respaldos. Los CFDI ocupan; sin rotacion el
# disco se llena y Postgres deja de aceptar escrituras.
ls -1t "$RESPALDOS"/monsort_*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm --
azul "Respaldos en disco: $(ls -1 "$RESPALDOS"/monsort_*.sql.gz | wc -l)"

# ---------------------------------------------------------------- codigo

azul "Actualizando codigo (rama $RAMA)"
cd "$RAIZ"

# --no-rebase: un rebase con conflictos puede perder archivos.
git fetch origin
git checkout "$RAMA"
git pull --no-rebase origin "$RAMA" || fallo "git pull fallo. Resuelve a mano."

COMMIT=$(git rev-parse --short HEAD)
verde "  HEAD en $COMMIT"

# ---------------------------------------------------------------- backend

azul "Dependencias de Python"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$RAIZ/Backend/requisitos.txt" \
    || fallo "pip install fallo"

azul "Migraciones de Alembic"
cd "$RAIZ/Backend"
ACTUAL=$("$VENV/bin/python" -m alembic current 2>/dev/null | tail -1 || echo "?")
echo "  antes: $ACTUAL"

"$VENV/bin/python" -m alembic upgrade head \
    || fallo "Migracion fallo. La base quedo intacta; restaura con $ARCHIVO si hace falta."

NUEVO=$("$VENV/bin/python" -m alembic current 2>/dev/null | tail -1 || echo "?")
verde "  ahora: $NUEVO"

# ---------------------------------------------------------------- frontend

azul "Build del frontend"
cd "$RAIZ/Frontend"

# npm ci y no npm install: respeta el lockfile exactamente. Un install
# puede resolver versiones distintas y romper el build en produccion.
npm ci --silent || fallo "npm ci fallo"
npm run build || fallo "El build de Vite fallo"

[[ -f "$RAIZ/Frontend/dist/index.html" ]] || fallo "No se genero dist/index.html"
verde "  dist/ generado"

# ---------------------------------------------------------------- servicios

azul "Reiniciando la API"
sudo systemctl restart "$SERVICIO"

sleep 4
systemctl is-active --quiet "$SERVICIO" \
    || fallo "El servicio no arranco. Revisa: journalctl -u $SERVICIO -n 50"

azul "Verificando que responda"
for intento in 1 2 3 4 5; do
    if curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
        verde "  /health responde"
        break
    fi
    [[ $intento -eq 5 ]] && fallo "/health no responde tras 5 intentos"
    sleep 3
done

azul "Recargando nginx"
sudo nginx -t || fallo "La configuracion de nginx tiene errores"
sudo systemctl reload nginx

# ---------------------------------------------------------------- resumen

echo
verde "════════════════════════════════════════════════"
verde " Despliegue completo"
verde "════════════════════════════════════════════════"
echo "  commit    : $COMMIT"
echo "  migracion : $NUEVO"
echo "  respaldo  : $ARCHIVO"
echo
echo "  Jobs activos (revisa que arrancaron):"
echo "    journalctl -u $SERVICIO -n 30 --no-pager | grep -i schedul"
echo
