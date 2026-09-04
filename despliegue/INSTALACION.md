# Instalación inicial del VPS — Monsort

Se corre **una sola vez**, con acceso `root` recién contratado el VPS.
Después de esto, las actualizaciones son solo `despliegue.sh`.

Reemplaza `DOMINIO` por el dominio real en todos los comandos.

---

## 1. Endurecer el servidor

Antes que nada, porque un VPS recién creado con contraseña de root
empieza a recibir intentos de acceso en minutos.

```bash
apt update && apt upgrade -y
apt install -y ufw fail2ban

# Usuario sin privilegios para la aplicación
adduser --disabled-password --gecos "" monsort
usermod -aG sudo monsort

# Tu llave SSH (desde tu máquina, ANTES de cerrar el acceso por contraseña)
# ssh-copy-id monsort@IP_DEL_VPS
mkdir -p /home/monsort/.ssh
# pega tu llave pública aquí:
nano /home/monsort/.ssh/authorized_keys
chmod 700 /home/monsort/.ssh
chmod 600 /home/monsort/.ssh/authorized_keys
chown -R monsort:monsort /home/monsort/.ssh
```

**Verifica que puedes entrar como `monsort` en otra terminal antes de
seguir.** Si cierras el acceso por contraseña sin comprobarlo, te quedas
fuera del servidor.

```bash
# Cerrar acceso por contraseña y por root
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh

# Firewall
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

systemctl enable --now fail2ban
```

Nota que **Postgres no se abre al exterior**. Solo escucha en localhost;
la API se conecta por socket local.

---

## 2. Paquetes base

```bash
apt install -y python3 python3-venv python3-dev build-essential \
               postgresql postgresql-contrib \
               nginx certbot python3-certbot-nginx \
               git curl gzip

# Node 20 para el build de Vite
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs

python3 --version   # esperado: 3.12.x en Ubuntu 24.04
node --version      # esperado: v20.x
```

---

## 3. PostgreSQL

```bash
sudo -u postgres psql
```

```sql
-- Contraseña NUEVA, distinta de la de desarrollo.
-- Alfanumérica: acentos y símbolos rompen psycopg2 (ya nos pasó).
CREATE USER monsort WITH PASSWORD 'PonUnaAlfanumericaLargaAqui';
CREATE DATABASE "MonsortDB" OWNER monsort;
GRANT ALL PRIVILEGES ON DATABASE "MonsortDB" TO monsort;
\q
```

El usuario del sistema y el de Postgres se llaman igual a propósito: así
`pg_dump` en `despliegue.sh` funciona por autenticación `peer` sin
contraseña.

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

Si `requisitos.txt` no incluye `cryptography` (la instalaste a mano en
desarrollo), agrégala antes de continuar o `verificar_certificado.py`
va a fallar.

---

## 5. Variables de entorno

```bash
nano /srv/monsort/ProyectoMonsort/Backend/.env
```

```
DATABASE_URL=postgresql://monsort:LaContrasenaDelPaso3@localhost:5432/MonsortDB
SECRET_KEY=<genera uno nuevo, ver abajo>
ALGORITMO=HS256
TOKEN_ACCESO_MIN_EXPIRACION=60
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=
BANXICO_TOKEN=
SAT_CER_PATH=
SAT_KEY_PATH=
SAT_KEY_PASSWORD=
```

Las tres `SAT_*` se dejan vacías: la e.firma todavía no llega y
`Settings` las declara opcionales.

```bash
# SECRET_KEY nuevo para producción, no reuses el de desarrollo
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

Verifica:

```bash
psql -d MonsortDB -c 'SELECT origen, COUNT(*) FROM "Facturas" GROUP BY origen;'
psql -d MonsortDB -c 'SELECT COUNT(*) FROM "HistorialVerificacion" WHERE origen = '"'"'sat'"'"';'
```

Esperado: 718 de `excel`, 2 de `gmail`, y 10 registros de historial SAT.

---

## 7. Servicios

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

**El dominio ya debe apuntar al VPS antes de este paso.** Verifica:

```bash
dig +short DOMINIO      # debe devolver la IP del VPS
```

```bash
mkdir -p /var/www/certbot

cp /srv/monsort/ProyectoMonsort/despliegue/monsort-nginx.conf \
   /etc/nginx/sites-available/monsort
sed -i 's/DOMINIO/tudominio.mx/g' /etc/nginx/sites-available/monsort

ln -sf /etc/nginx/sites-available/monsort /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx

certbot --nginx -d DOMINIO -d www.DOMINIO --agree-tos -m tu@correo.com
```

Certbot instala la renovación automática por sí solo. Compruébalo:

```bash
systemctl list-timers | grep certbot
certbot renew --dry-run
```

---

## 9. Frontend

Alexandergg necesita apuntar a la API. En `Frontend/.env.production`:

```
VITE_API_URL=https://DOMINIO/api
```

Y en el backend, el CORS: `main.py` tiene `localhost:5173` hardcodeado.
Cámbialo por el dominio real antes del primer despliegue, o el navegador
va a bloquear todas las peticiones.

```bash
sudo -iu monsort
cd /srv/monsort/ProyectoMonsort/Frontend
npm ci && npm run build
```

---

## 10. Primer despliegue

```bash
cp /srv/monsort/ProyectoMonsort/despliegue/despliegue.sh /srv/monsort/
chmod +x /srv/monsort/despliegue.sh
sudo -u monsort /srv/monsort/despliegue.sh
```

Para que `despliegue.sh` pueda reiniciar el servicio sin pedir
contraseña:

```bash
cat > /etc/sudoers.d/monsort <<'EOF'
monsort ALL=(root) NOPASSWD: /bin/systemctl restart monsort-api
monsort ALL=(root) NOPASSWD: /bin/systemctl reload nginx
monsort ALL=(root) NOPASSWD: /usr/sbin/nginx -t
EOF
chmod 440 /etc/sudoers.d/monsort
```

Permisos acotados a tres comandos, no `sudo` general.

---

## 11. Verificación final

```bash
curl -fsS https://DOMINIO/api/health
curl -fsS https://DOMINIO/ | head -5

# Los tres jobs deben aparecer
journalctl -u monsort-api -n 40 --no-pager | grep -i schedul
```

Desde el navegador: entra al dashboard, revisa que el listado de
facturas cargue, y prueba `POST /facturas/{id}/verificar-sat` con una
factura conocida. Ese endpoint es el que ejercita la salida a internet
del servidor hacia el SAT — si el firewall del proveedor bloquea salidas,
aquí se nota.

---

## Después del despliegue

**Vigilar los primeros días:**

```bash
# El job de correos corre cada 2 minutos
journalctl -u monsort-api -f

# Correos que fallaron
psql -d MonsortDB -c 'SELECT * FROM "CorreosFallidos" WHERE resuelto = 0;'
```

Esa última consulta es la importante. El job de Gmail manda los fallos a
esa tabla en silencio; si nadie la mira, un correo mal procesado no se
entera nadie.

**Pendientes que quedan abiertos:**

- e.firma del cliente → activar `ClienteSATReal` en la fase 2
- Credenciales de Prodigia → fase 3 (timbrado)
- Respaldos fuera del VPS: los de `despliegue.sh` viven en el mismo
  disco. Si el VPS muere, mueren con él. Contrata los snapshots del
  proveedor o copia los `.sql.gz` a otro lado.
