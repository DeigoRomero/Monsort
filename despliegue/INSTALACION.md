# Instalación inicial del VPS — Monsort

Se corre **una sola vez**, con acceso `root` recién contratado el VPS.
Después, las actualizaciones son solo `despliegue.sh`.

**Dominio:** `facturas.grupomonsort.com`
**Proveedor:** Vultr, región Ciudad de México
**Plan:** 2 vCPU / 4 GB RAM / 80 GB SSD, Ubuntu 24.04 LTS, respaldos activados

---

## 0. Antes de empezar

En el panel de DNS de `grupomonsort.com`, crear el registro:

```
Tipo: A    Nombre: facturas    Valor: <IP del VPS>    TTL: 3600
```

La propagación tarda de minutos a 48 horas. Sin esto, certbot falla en
el paso 8, así que conviene crearlo apenas exista la IP.

---

## 1. Endurecer el servidor

Primero que nada: un VPS recién creado empieza a recibir intentos de
acceso a los pocos minutos.

```bash
apt update && apt upgrade -y
apt install -y ufw fail2ban

# Usuario sin privilegios para la aplicación
adduser --disabled-password --gecos "" monsort
usermod -aG sudo monsort

mkdir -p /home/monsort/.ssh
nano /home/monsort/.ssh/authorized_keys      # pegar tu llave pública
chmod 700 /home/monsort/.ssh
chmod 600 /home/monsort/.ssh/authorized_keys
chown -R monsort:monsort /home/monsort/.ssh
```

> **Comprueba que puedes entrar como `monsort` desde otra terminal antes
> de continuar.** Si cierras el acceso por contraseña sin verificarlo y
> la llave quedó mal, te quedas fuera del servidor y hay que reinstalar.

```bash
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

systemctl enable --now fail2ban
```

Postgres no se abre al exterior: solo escucha en localhost.

---

## 2. Paquetes base

```bash
apt install -y python3 python3-venv python3-dev build-essential \
               postgresql postgresql-contrib \
               nginx certbot python3-certbot-nginx \
               git curl gzip

curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs

python3 --version   # 3.12.x en Ubuntu 24.04
node --version      # v20.x
nginx -v            # 1.24+ (el conf usa la sintaxis nueva de http2)
```

---

## 3. PostgreSQL

```bash
sudo -u postgres psql
```

```sql
-- Contraseña NUEVA, distinta de la de desarrollo.
-- Solo letras y números: acentos y símbolos rompen psycopg2.
CREATE USER monsort WITH PASSWORD 'PonUnaAlfanumericaLargaAqui';
CREATE DATABASE "MonsortDB" OWNER monsort;
GRANT ALL PRIVILEGES ON DATABASE "MonsortDB" TO monsort;
\q
```

El usuario del sistema y el de Postgres se llaman igual a propósito: así
`pg_dump` funciona por autenticación `peer`, sin contraseña en el script.

---

## 4. Código y entorno

```bash
mkdir -p /srv/monsort/respaldos
chown -R monsort:monsort /srv/monsort

sudo -iu monsort
cd /srv/monsort

git clone https://github.com/TU_USUARIO/ProyectoMonsort.git
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r ProyectoMonsort/Backend/requisitos.txt
```

---

## 5. Variables de entorno

```bash
nano /srv/monsort/ProyectoMonsort/Backend/.env
```

```
DATABASE_URL=postgresql://monsort:LaContrasenaDelPaso3@localhost:5432/MonsortDB
SECRET_KEY=
ALGORITMO=HS256
TOKEN_ACCESO_MIN_EXPIRACION=60
CORS_ORIGINS=https://facturas.grupomonsort.com
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=
BANXICO_TOKEN=
SAT_CER_PATH=
SAT_KEY_PATH=
SAT_KEY_PASSWORD=
```

Las tres `SAT_*` van vacías: la e.firma todavía no llega y `Settings`
las declara opcionales.

`CORS_ORIGINS` **sin** `localhost`. Solo el dominio de producción.

```bash
# SECRET_KEY nuevo, no reutilices el de desarrollo
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

chmod 600 /srv/monsort/ProyectoMonsort/Backend/.env
```

---

## 6. Migrar los datos históricos

Desde **tu máquina**:

```powershell
& "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe" -U postgres -d MonsortDB -Fc -f monsort.dump
scp monsort.dump monsort@IP_DEL_VPS:/srv/monsort/
```

En el VPS:

```bash
pg_restore -d MonsortDB --no-owner --role=monsort /srv/monsort/monsort.dump
rm /srv/monsort/monsort.dump          # trae datos fiscales, no lo dejes ahí
```

Verificar:

```bash
psql -d MonsortDB -c 'SELECT origen, COUNT(*) FROM "Facturas" GROUP BY origen;'
psql -d MonsortDB -c "SELECT COUNT(*) FROM \"HistorialVerificacion\" WHERE origen = 'sat';"
psql -d MonsortDB -c 'SELECT COUNT(*) FROM "SolicitudesSAT";'
```

Esperado: 718 de `excel`, 2 de `gmail`, 10 de historial SAT, y
**0 solicitudes** — si aparecen las de prueba del cliente falso,
bórralas antes de arrancar.

---

## 7. Servicio de la API

```bash
exit   # volver a root

cp /srv/monsort/ProyectoMonsort/despliegue/monsort-api.service \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now monsort-api
systemctl status monsort-api
```

Si no arranca:

```bash
journalctl -u monsort-api -n 50 --no-pager
```

---

## 8. nginx y SSL

**Verifica primero que el DNS ya propagó:**

```bash
dig +short facturas.grupomonsort.com    # debe devolver la IP del VPS
```

Si no devuelve nada, espera. Certbot no puede emitir el certificado sin
esto.

```bash
mkdir -p /var/www/certbot

cp /srv/monsort/ProyectoMonsort/despliegue/monsort-nginx.conf \
   /etc/nginx/sites-available/monsort

ln -sf /etc/nginx/sites-available/monsort /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx

certbot --nginx -d facturas.grupomonsort.com --agree-tos -m tu@correo.com
```

El conf ya trae el dominio: no hace falta editarlo.

Renovación automática (certbot la configura solo):

```bash
systemctl list-timers | grep certbot
certbot renew --dry-run
```

---

## 9. Frontend

En el repo, `Frontend/.env.production`:

```
VITE_API_URL=https://facturas.grupomonsort.com/api
```

```bash
sudo -iu monsort
cd /srv/monsort/ProyectoMonsort/Frontend
npm ci && npm run build
ls dist/index.html    # debe existir
```

---

## 10. Primer despliegue

```bash
exit   # root

cp /srv/monsort/ProyectoMonsort/despliegue/despliegue.sh /srv/monsort/
chmod +x /srv/monsort/despliegue.sh
chown monsort:monsort /srv/monsort/despliegue.sh

# Permisos acotados a tres comandos, no sudo general
cat > /etc/sudoers.d/monsort <<'EOF'
monsort ALL=(root) NOPASSWD: /bin/systemctl restart monsort-api
monsort ALL=(root) NOPASSWD: /bin/systemctl reload nginx
monsort ALL=(root) NOPASSWD: /usr/sbin/nginx -t
EOF
chmod 440 /etc/sudoers.d/monsort

sudo -u monsort /srv/monsort/despliegue.sh
```

---

## 11. Verificación final

```bash
curl -fsS https://facturas.grupomonsort.com/api/health
curl -fsS https://facturas.grupomonsort.com/ | head -5

# Los tres jobs deben aparecer al arrancar
journalctl -u monsort-api -n 40 --no-pager | grep -i schedul
```

Desde el navegador: entra al dashboard, revisa que el listado cargue, y
prueba `POST /facturas/{id}/verificar-sat` con una factura conocida.

Ese endpoint es el que ejercita la salida a internet del servidor hacia
el SAT. Si el firewall del proveedor bloquea conexiones salientes, aquí
se nota y en ningún otro lado.

---

## Después del despliegue

**Vigilar los primeros días:**

```bash
journalctl -u monsort-api -f
```

Y sobre todo:

```bash
psql -d MonsortDB -c 'SELECT * FROM "CorreosFallidos" WHERE resuelto = 0;'
```

El job de Gmail manda los correos que fallan a esa tabla **en silencio**.
Si nadie la revisa, una factura que no se procesó no se entera nadie.

**Respaldos:** los de `despliegue.sh` viven en el mismo disco del VPS.
Si el servidor muere, mueren con él. Los snapshots de Vultr ayudan, pero
considera copiar los `.sql.gz` a otro lado una vez por semana.

**Pendientes abiertos:**

- e.firma del cliente → activar `ClienteSATReal` (fase 2)
- Credenciales de Prodigia → timbrado (fase 3)