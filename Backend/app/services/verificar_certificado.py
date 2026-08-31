r"""
Verificacion de la e.firma (FIEL) - Monsort

Paso 1 de la fase 2 (descarga masiva de CFDI).

Comprueba, ANTES de construir nada encima:

  1. Que el .cer cargue (los del SAT vienen en DER, no PEM)
  2. Vigencia y dias restantes
  3. RFC del titular (OID 2.5.4.45), que debe ser el de Monsort
  4. Si el certificado es e.firma o CSD (son distintos y no intercambiables)
  5. Que la contrasena abra la llave privada
  6. Que .cer y .key sean PAREJA

El punto 6 es el que mas se pasa por alto. Es comun recibir el .cer de la
e.firma con el .key de un CSD, o archivos de renovaciones distintas. Si no
corresponden, la firma sale mal formada y el SAT devuelve un error generico
que no dice nada util.

NO hace ninguna llamada de red. NO toca la base de datos.

Ubicacion sugerida: app/services/verificar_certificado.py

Configuracion en Backend/.env:

    SAT_CER_PATH=D:/ruta/fuera/del/repo/monsort.cer
    SAT_KEY_PATH=D:/ruta/fuera/del/repo/monsort.key
    SAT_KEY_PASSWORD=la_contrasena_de_la_llave

Uso (desde Backend/):

    python -m app.services.verificar_certificado

    # o apuntando a archivos concretos, sin pasar por .env
    python -m app.services.verificar_certificado --cer ruta.cer --key ruta.key
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509 import load_der_x509_certificate, load_pem_x509_certificate
from cryptography.x509.oid import NameOID, ObjectIdentifier
from dotenv import load_dotenv

# OID del RFC en los certificados del SAT.
OID_RFC = ObjectIdentifier("2.5.4.45")   # x500UniqueIdentifier

RFC_ESPERADO = "MSF140227BF7"

OK = "  [OK]  "
MAL = "  [MAL] "
AVISO = "  [!]   "


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

def cargar_certificado(ruta: str):
    """Los .cer del SAT vienen en DER. Se intenta PEM solo como respaldo."""
    with open(ruta, "rb") as archivo:
        datos = archivo.read()

    try:
        return load_der_x509_certificate(datos), "DER"
    except Exception:
        pass

    try:
        return load_pem_x509_certificate(datos), "PEM"
    except Exception as error:
        raise SystemExit(
            f"{MAL}No se pudo leer el certificado como DER ni como PEM.\n"
            f"        {ruta}\n        {error}"
        )


def cargar_llave(ruta: str, contrasena: str):
    """
    Los .key del SAT son PKCS#8 en DER, cifrados con la contrasena de la
    llave privada (NO la del portal del SAT, NO la de la CIEC).
    """
    with open(ruta, "rb") as archivo:
        datos = archivo.read()

    clave = contrasena.encode("utf-8") if contrasena else None

    try:
        return serialization.load_der_private_key(datos, password=clave), "DER"
    except Exception as error_der:
        try:
            return serialization.load_pem_private_key(datos, password=clave), "PEM"
        except Exception:
            raise SystemExit(
                f"{MAL}No se pudo abrir la llave privada.\n"
                f"        {ruta}\n"
                f"        Causa probable: contrasena incorrecta.\n"
                f"        Detalle: {error_der}"
            )


# ---------------------------------------------------------------------------
# Lectura del certificado
# ---------------------------------------------------------------------------

def leer_atributo(sujeto, oid) -> str | None:
    try:
        valores = sujeto.get_attributes_for_oid(oid)
        return valores[0].value if valores else None
    except Exception:
        return None


def obtener_vigencia(certificado) -> tuple[datetime, datetime]:
    """
    En cryptography 42+ las propiedades sin sufijo estan deprecadas y las
    nuevas terminan en _utc devolviendo datetime con zona horaria.
    """
    try:
        return certificado.not_valid_before_utc, certificado.not_valid_after_utc
    except AttributeError:
        desde = certificado.not_valid_before.replace(tzinfo=timezone.utc)
        hasta = certificado.not_valid_after.replace(tzinfo=timezone.utc)
        return desde, hasta


def clasificar_certificado(certificado) -> str:
    """
    Distingue e.firma de CSD. No es infalible, pero el numero de serie de
    los CSD suele empezar con '00001000000' y el uso de llave difiere:
    la e.firma trae digital_signature + key_encipherment; el CSD
    normalmente solo digital_signature + non_repudiation.
    """
    try:
        uso = certificado.extensions.get_extension_for_class(
            __import__("cryptography.x509", fromlist=["KeyUsage"]).KeyUsage
        ).value
        if uso.key_encipherment and uso.data_encipherment:
            return "e.firma (FIEL)"
        if uso.content_commitment and not uso.key_encipherment:
            return "CSD (sello digital)"
    except Exception:
        pass
    return "indeterminado"


# ---------------------------------------------------------------------------
# Correspondencia
# ---------------------------------------------------------------------------

def son_pareja(certificado, llave) -> bool:
    """
    Firma un mensaje con la privada y lo verifica con la publica del
    certificado. Si corresponden, la verificacion pasa.
    """
    publica_cert = certificado.public_key()

    if not isinstance(llave, rsa.RSAPrivateKey):
        return False

    numeros_cert = publica_cert.public_numbers()
    numeros_llave = llave.public_key().public_numbers()
    if (numeros_cert.n, numeros_cert.e) != (numeros_llave.n, numeros_llave.e):
        return False

    mensaje = b"monsort-verificacion-de-pareja"
    try:
        firma = llave.sign(mensaje, padding.PKCS1v15(), hashes.SHA256())
        publica_cert.verify(firma, mensaje, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Verifica la e.firma antes de usarla contra el SAT."
    )
    parser.add_argument("--cer", help="Ruta al .cer (default: SAT_CER_PATH del .env)")
    parser.add_argument("--key", help="Ruta al .key (default: SAT_KEY_PATH del .env)")
    parser.add_argument(
        "--password",
        help="Contrasena de la llave. Preferible dejarla en SAT_KEY_PASSWORD.",
    )
    args = parser.parse_args()

    ruta_cer = args.cer or os.getenv("SAT_CER_PATH")
    ruta_key = args.key or os.getenv("SAT_KEY_PATH")
    contrasena = args.password or os.getenv("SAT_KEY_PASSWORD")

    if not ruta_cer:
        raise SystemExit(
            f"{MAL}Falta la ruta del certificado.\n"
            f"        Define SAT_CER_PATH en Backend/.env o usa --cer."
        )

    print()
    print("=" * 72)
    print("VERIFICACION DE LA e.firma")
    print("=" * 72)
    print()

    # ---------------- certificado ----------------
    if not os.path.exists(ruta_cer):
        raise SystemExit(f"{MAL}No existe el archivo: {ruta_cer}")

    certificado, formato = cargar_certificado(ruta_cer)
    print(f"Certificado: {ruta_cer}")
    print(f"{OK}Cargado correctamente (formato {formato})")

    sujeto = certificado.subject
    rfc = leer_atributo(sujeto, OID_RFC)
    nombre = leer_atributo(sujeto, NameOID.COMMON_NAME)

    print(f"        Titular       : {nombre}")
    print(f"        Numero de serie: {certificado.serial_number}")

    tipo = clasificar_certificado(certificado)
    print(f"        Tipo          : {tipo}")
    if tipo == "CSD (sello digital)":
        print(f"{AVISO}Parece un CSD, no la e.firma.")
        print(f"        La descarga masiva requiere e.firma (FIEL).")
        print(f"        El CSD sirve para timbrado (fase 3), no para esto.")

    # ---------------- RFC ----------------
    print()
    if rfc is None:
        print(f"{AVISO}No se encontro el RFC en el certificado (OID 2.5.4.45).")
    elif RFC_ESPERADO in rfc.strip().upper():
        print(f"{OK}RFC del titular: {rfc.strip()}  (coincide con Monsort)")
    else:
        print(f"{MAL}RFC del titular: {rfc.strip()}")
        print(f"        Se esperaba {RFC_ESPERADO}.")
        print(f"        Este certificado NO es de Monsort.")

    # ---------------- vigencia ----------------
    desde, hasta = obtener_vigencia(certificado)
    ahora = datetime.now(timezone.utc)
    dias = (hasta - ahora).days

    print()
    print(f"        Valido desde  : {desde:%Y-%m-%d %H:%M UTC}")
    print(f"        Valido hasta  : {hasta:%Y-%m-%d %H:%M UTC}")

    if ahora < desde:
        print(f"{MAL}El certificado aun no entra en vigor.")
    elif dias < 0:
        print(f"{MAL}VENCIDO hace {abs(dias)} dias. Hay que renovarlo ante el SAT.")
    elif dias < 30:
        print(f"{MAL}Vence en {dias} dias. Renovar YA.")
    elif dias < 90:
        print(f"{AVISO}Vence en {dias} dias. Agendar la renovacion.")
    else:
        print(f"{OK}Vigente. Quedan {dias} dias.")

    # ---------------- llave ----------------
    if not ruta_key:
        print()
        print(f"{AVISO}No se indico .key. Se omiten las pruebas de llave.")
        print(f"        Define SAT_KEY_PATH para validar la pareja.")
        print()
        return

    print()
    print(f"Llave privada: {ruta_key}")

    if not os.path.exists(ruta_key):
        raise SystemExit(f"{MAL}No existe el archivo: {ruta_key}")

    if not contrasena:
        print(f"{AVISO}Sin contrasena (SAT_KEY_PASSWORD vacia).")
        print(f"        Las llaves del SAT siempre estan cifradas.")

    llave, formato_key = cargar_llave(ruta_key, contrasena)
    print(f"{OK}Contrasena correcta, llave abierta (formato {formato_key})")

    if isinstance(llave, rsa.RSAPrivateKey):
        print(f"        Algoritmo     : RSA de {llave.key_size} bits")

    # ---------------- pareja ----------------
    print()
    if son_pareja(certificado, llave):
        print(f"{OK}El .cer y el .key CORRESPONDEN entre si.")
    else:
        print(f"{MAL}El .cer y el .key NO corresponden.")
        print(f"        Son de tramites o certificados distintos.")
        print(f"        Cualquier firma hecha con este par sera rechazada.")

    print()
    print("=" * 72)
    print()


if __name__ == "__main__":
    main()