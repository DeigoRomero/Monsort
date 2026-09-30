# Despliegue de las correcciones del 30/09/2026

Seguridad, detector de órdenes de compra, días para pago, notificaciones y
total facturado por importe. **Backend y frontend van juntos**: el login
cambió de forma (el refresh token ahora es cookie) y un frontend viejo con un
backend nuevo no puede iniciar sesión.

Después del despliegue **todos vuelven a iniciar sesión una vez** (la
migración invalida los refresh token guardados en claro).

## Pasos en el VPS

```bash
ssh monsort@64.177.80.14
cd /srv/monsort

# 0. Respaldo
sudo -u postgres pg_dump -Fc MonsortDB > ~/respaldo_$(date +%Y%m%d_%H%M).dump

# 1. .env — revisar ANTES de reiniciar
nano ProyectoMonsort/Backend/.env
#   ENTORNO=produccion
#   CORS_ORIGINS=https://facturas.grupomonsort.com
#   TOKEN_ACCESO_MIN_EXPIRACION=15
#   SECRET_KEY de 64+ caracteres. Si es más corta, generar una nueva:
#       python3 -c "import secrets; print(secrets.token_urlsafe(64))"
chmod 600 ProyectoMonsort/Backend/.env

# 2. nginx: snippets + sitio
sudo cp ProyectoMonsort/despliegue/nginx-seguridad.conf /etc/nginx/snippets/monsort-seguridad.conf
sudo cp ProyectoMonsort/despliegue/nginx-proxy.conf     /etc/nginx/snippets/monsort-proxy.conf
sudo cp /etc/nginx/sites-available/monsort ~/monsort-nginx.respaldo
sudo cp ProyectoMonsort/despliegue/monsort-nginx.conf /etc/nginx/sites-available/monsort
sudo nginx -t          # si falla: sudo cp ~/monsort-nginx.respaldo /etc/nginx/sites-available/monsort

# 3. systemd endurecido
sudo cp ProyectoMonsort/despliegue/monsort-api.service /etc/systemd/system/monsort-api.service
sudo systemctl daemon-reload

# 4. Despliegue normal (pull, pip, alembic, build, rsync, restart, reload nginx)
diff /srv/monsort/despliegue.sh ProyectoMonsort/despliegue/despliegue.sh   # después del pull
./despliegue.sh
```

La migración `a7c3e9d1f250` imprime las cuentas que desactiva. Deben ser dos:
`string` (cuenta de prueba creada desde /docs, contraseña "string") y
`Sistema Automatico` (usuario interno del scheduler, no inicia sesión).

## Verificación (todo desde fuera del servidor)

```bash
S=https://facturas.grupomonsort.com
curl -s -o /dev/null -w "%{http_code}\n" $S/api/facturas/          # 401
curl -s -o /dev/null -w "%{http_code}\n" $S/api/openapi.json       # 404
curl -s -o /dev/null -w "%{http_code}\n" -X POST $S/api/auth/registro -H 'Content-Type: application/json' -d '{}'   # 401
curl -sI $S/ | grep -iE "content-security|strict-transport|x-frame"  # los tres
curl -s -o /dev/null -w "%{http_code}\n" $S/.env                    # 404
systemd-analyze security monsort-api | tail -1                      # debe bajar de ~9 a ~2-3
```

En el navegador: iniciar sesión, recargar la página (debe seguir dentro),
abrir el PDF de una OC y cerrar sesión.

## Recalcular OCs existentes

```bash
cd /srv/monsort/ProyectoMonsort/Backend
../../.venv/bin/python -m app.services.redetectar_oc                 # vista previa
../../.venv/bin/python -m app.services.redetectar_oc --aplicar
# OCs que llegaron solo en el cuerpo del correo (Regal Rexnord, D1009036):
../../.venv/bin/python -m app.services.redetectar_oc --buscar-cuerpo --desde 2026-09-01 --hasta 2026-09-30
../../.venv/bin/python -m app.services.redetectar_oc --buscar-cuerpo --desde 2026-09-01 --hasta 2026-09-30 --aplicar
```

Respeta los números que alguien corrigió a mano. Cotizaciones y recibos que
estaban guardados como OC solo se listan; no se borran.

## Reconectar Gmail (si algún día hace falta)

`GET /auth/gmail/iniciar` ya no existe (era público y permitía conectar
OTRA cuenta de Gmail). Ahora un administrador pide el enlace:

```bash
TOKEN=$(curl -s -X POST $S/api/auth/login -H 'Content-Type: application/json' \
  -d '{"correo":"TU_CORREO","password":"TU_CONTRASEÑA"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST $S/api/auth/gmail/enlace -H "Authorization: Bearer $TOKEN"
```

El `url` que regresa vale 24 horas y se le puede mandar al dueño del buzón.

## Qué quedó protegido

| Riesgo (OWASP) | Antes | Ahora |
|---|---|---|
| Control de acceso (A01) | Solo 2 rutas pedían sesión | Todas; roles leídos de la base |
| Alta de usuarios | `/auth/registro` público, cualquier rol | Solo administrador; desarrollador solo lo crea otro desarrollador |
| OAuth de Gmail | Público, sin `state` | Enlace firmado de un administrador, 24 h |
| Fuerza bruta (A07) | Sin límite | 5 fallos bloquean la cuenta 15 min; 20 por IP; nginx 10/min |
| Tokens | Access y refresh en localStorage; refresh en claro en la base | Access en memoria (15 min), refresh en cookie HttpOnly+Secure+SameSite=Strict, SHA-256 en la base, rotación, logout que invalida todo |
| Contraseñas | Sin reglas | 12+ caracteres, 3 tipos, sin el correo |
| XSS / clickjacking (A03/A05) | Sin CSP | CSP estricta, X-Frame-Options DENY, HSTS 2 años |
| Exposición (A05) | Swagger público, trazas en errores 500 | Swagger apagado, errores genéricos |
| Descargas | Nombre de archivo del correo directo al encabezado | Saneado (sin inyección de encabezados), sin caché |
| Dependencias (A06) | anyio, cryptography con CVE | Actualizadas; `npm audit` en 0 |
| Servidor | systemd básico | Sin capacidades, sistema de archivos de solo lectura salvo /srv/monsort |

Pendientes conocidos, sin riesgo práctico hoy:
- `ecdsa` (dependencia de python-jose) tiene un CVE sin arreglo para firmas
  EC; el proyecto firma con HS256, así que no aplica.
- `oauthlib` 4.0 corrige un problema del lado SERVIDOR de PKCE; aquí se usa
  como cliente. Subir de versión mayor queda para cuando google-auth-oauthlib
  lo soporte.
