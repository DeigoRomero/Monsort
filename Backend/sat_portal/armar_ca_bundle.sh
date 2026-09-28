#!/usr/bin/env bash
#
# Arma un bundle de certificados que SI valida los servidores del SAT.
#
# POR QUE: algunos servidores del SAT (portalcfdi.facturaelectronica.sat.gob.mx)
# mandan su certificado SIN el certificado intermedio que lo conecta con una
# autoridad raiz conocida. Los navegadores lo buscan solos (en la extension
# "CA Issuers" del certificado); curl/PHP no, y fallan con:
#     cURL error 60: unable to get local issuer certificate
# Es como una carta firmada por un gerente que no incluye la hoja donde el
# director avala al gerente: el navegador va y la pide, PHP no.
#
# QUE HACE: toma los certificados raiz del sistema, descarga los intermedios
# que le faltan a cada servidor del SAT y guarda todo junto en un archivo.
# NO desactiva la verificacion: la hace posible.
#
# Uso:
#   bash armar_ca_bundle.sh                              # destino por default
#   bash armar_ca_bundle.sh /ruta/sat-ca-bundle.pem
#
# Despues, en .env:  SAT_PORTAL_CA_BUNDLE=<la ruta que imprima al final>
#
# Variables para pruebas: SAT_HOSTS, BUNDLE_BASE.

set -euo pipefail

DESTINO="${1:-/srv/monsort/credenciales/sat-ca-bundle.pem}"
HOSTS="${SAT_HOSTS:-portalcfdi.facturaelectronica.sat.gob.mx cfdiau.sat.gob.mx}"
BUNDLE_BASE="${BUNDLE_BASE:-/etc/ssl/certs/ca-certificates.crt}"
CIFRADO="DEFAULT@SECLEVEL=1"    # el SAT usa llaves DH de 1024 bits
MAX_NIVELES=4

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp "$BUNDLE_BASE" "$TMP/bundle.pem"

# Descarga el certificado de la URL "CA Issuers" de $1 y lo deja en PEM en $2.
bajar_emisor() {
    local cert="$1" salida="$2" url
    url="$(openssl x509 -in "$cert" -noout -ext authorityInfoAccess 2>/dev/null \
           | grep -i "CA Issuers" | head -n1 | sed 's/.*URI://' | tr -d '[:space:]')"
    if [ -z "$url" ]; then
        return 1
    fi
    echo "    descargando intermedio: $url" >&2
    curl -fsS --max-time 30 "$url" -o "$TMP/descarga.crt"
    # Los .crt de AIA suelen venir en DER; a veces en PEM.
    openssl x509 -inform DER -in "$TMP/descarga.crt" -out "$salida" 2>/dev/null \
        || openssl x509 -inform PEM -in "$TMP/descarga.crt" -out "$salida"
}

for HOST in $HOSTS; do
    echo "== $HOST"
    HOST_SIN_PUERTO="${HOST%%:*}"
    PUERTO="${HOST##*:}"; [ "$PUERTO" = "$HOST" ] && PUERTO=443

    openssl s_client -connect "$HOST_SIN_PUERTO:$PUERTO" -servername "$HOST_SIN_PUERTO" \
        -cipher "$CIFRADO" -showcerts </dev/null 2>/dev/null \
        | awk '/BEGIN CERTIFICATE/{n++} n==1{print} /END CERTIFICATE/ && n==1{exit}' \
        > "$TMP/hoja.pem"
    cp "$TMP/hoja.pem" "$TMP/actual.pem"    # "actual" sube por la cadena; "hoja" es la que se valida

    if ! [ -s "$TMP/hoja.pem" ]; then
        echo "   ERROR: no se pudo obtener el certificado de $HOST" >&2
        exit 1
    fi

    for ((nivel = 1; nivel <= MAX_NIVELES; nivel++)); do
        if openssl verify -CAfile "$TMP/bundle.pem" "$TMP/hoja.pem" >/dev/null 2>&1; then
            echo "   OK: la cadena valida"
            break
        fi
        if ! bajar_emisor "$TMP/actual.pem" "$TMP/intermedio.pem"; then
            echo "   ERROR: el certificado no trae URL de 'CA Issuers'; no se puede completar solo" >&2
            exit 1
        fi
        echo "    agregado: $(openssl x509 -in "$TMP/intermedio.pem" -noout -subject)"
        cat "$TMP/intermedio.pem" >> "$TMP/bundle.pem"
        # El intermedio tambien podria necesitar a su propio emisor.
        cp "$TMP/intermedio.pem" "$TMP/actual.pem"
    done

    if ! openssl verify -CAfile "$TMP/bundle.pem" "$TMP/hoja.pem" >/dev/null 2>&1; then
        echo "   ERROR: despues de $MAX_NIVELES niveles la cadena sigue sin validar" >&2
        exit 1
    fi
done

mkdir -p "$(dirname "$DESTINO")"
install -m 0644 "$TMP/bundle.pem" "$DESTINO"

echo
echo "Bundle guardado en: $DESTINO"
echo "Agrega a Backend/.env:"
echo "SAT_PORTAL_CA_BUNDLE=$DESTINO"
