import json
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv

# Cargar variables de entorno existentes
load_dotenv()

CREDENTIALS_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def obtener_refresh_token():
    """
    Ejecuta el flujo Oauth, abre el navegador, y guarda el refresh token en un archivo .env.
    """

    #Crear el flujo de autenticación
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)

    # Abre el navegador del usuario para que autorice
    # auth_local_webserver = true abre un servidor local localhost
    # que captura el redirect automaticamente y devuelve el código de autorización
    creds = flow.run_local_server(
    port=8888,
    open_browser=True,
    timeout_seconds=120  # espera 2 minutos
    )
    # creds.refresh_token contiene el refresh token que necesitamos
    refresh_token = creds.refresh_token

    if refresh_token:
        print("\n" + "="*60)
        print("✅ Autorización exitosa!")
        print("="*60)
        print(f"Tu REFRESH_TOKEN es:\n\n{refresh_token}\n")
        print("Cópialo y pégalo en tu .env como:")
        print("GMAIL_REFRESH_TOKEN=" + refresh_token)
        print("="*60 + "\n")
    else:
        print("❌ Error: no se obtuvo refresh_token")

# Ejecutar la función si se ejecuta el script directamente
if __name__ == "__main__":
    obtener_refresh_token()