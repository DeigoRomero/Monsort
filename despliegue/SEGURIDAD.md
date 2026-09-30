# Despliegue de las correcciones del 30/09/2026

Seguridad, detector de órdenes de compra, días para pago, notificaciones y
total facturado por importe. **Backend y frontend van juntos**: el login
cambió de forma (el refresh token ahora es cookie) y un frontend viejo con un
backend nuevo no puede iniciar sesión.

Después del despliegue **todos vuelven a iniciar sesión una vez** (la
migración invalida los refresh token guardados en claro).

## Paso 0: subir los cambios (en Windows)

Los archivos nuevos (`nginx-seguridad.conf`, `nginx-proxy.conf`, la
migración, etc.) solo existen en la máquina de desarrollo hasta que se hace
push. Sin esto, en el VPS "No such file or directory".

```powershell
cd D:\ProyectoMonsort
git status                      # revisar que NO aparezca ningún .env
git add -A
git commit -m "Seguridad, detector de OC, dias para pago, notificaciones, total por importe"
git pull --no-rebase origin main
git push origin main
```

## Pasos en el VPS

```bash
ssh monsort@64.177.80.14
cd /srv/monsort

# 1. Respaldo de la base
sudo -u postgres pg_dump -Fc MonsortDB > ~/respaldo_$(date +%Y%m%d_%H%M).dump

# 2. Traer el código y actualizar la copia del script
cd /srv/monsort/ProyectoMonsort && git pull --no-rebase origin main && cd /srv/monsort
ls ProyectoMonsort/despliegue/          # deben aparecer nginx-seguridad.conf y nginx-proxy.conf
cp ProyectoMonsort/despliegue/despliegue.sh /srv/monsort/despliegue.sh && chmod +x /srv/monsort/despliegue.sh

# 3. .env — revisar ANTES de reiniciar
nano ProyectoMonsort/Backend/.env
#   ENTORNO=produccion
#   CORS_ORIGINS=https://facturas.grupomonsort.com
#   TOKEN_ACCESO_MIN_EXPIRACION=15
#   SECRET_KEY de 64+ caracteres. Si es más corta, generar una nueva:
#       python3 -c "import secrets; print(secrets.token_urlsafe(64))"
chmod 600 ProyectoMonsort/Backend/.env

# 4. systemd endurecido
sudo cp ProyectoMonsort/despliegue/monsort-api.service /etc/systemd/system/monsort-api.service
sudo systemctl daemon-reload

# 5. Despliegue (migraciones, build, copia a /var/www/monsort, reinicio)
./despliegue.sh

# 6. nginx. El archivo del sitio NO se llama necesariamente "monsort":
#    buscar cuál es el que está activo.
grep -rl "facturas.grupomonsort.com" /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null
ls -l /etc/nginx/sites-enabled/
SITIO=/etc/nginx/sites-available/NOMBRE_QUE_SALIO    # <- ajustar
sudo cp "$SITIO" ~/nginx-sitio.respaldo
sudo cp ProyectoMonsort/despliegue/nginx-seguridad.conf /etc/nginx/snippets/monsort-seguridad.conf
sudo cp ProyectoMonsort/despliegue/nginx-proxy.conf     /etc/nginx/snippets/monsort-proxy.conf
sudo cp ProyectoMonsort/despliegue/monsort-nginx.conf "$SITIO"
sudo nginx -t && sudo systemctl reload nginx
# Si nginx -t falla:  sudo cp ~/nginx-sitio.respaldo "$SITIO" && sudo nginx -t
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
