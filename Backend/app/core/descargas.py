"""
Respuestas de archivo seguras.

Los nombres de archivo vienen de correos de terceros ("Pedido 934541 /
MONSORT SIN FRONTERAS SA DE CV.PDF"). Meterlos crudos en Content-Disposition
permite romper el encabezado con comillas o saltos de línea (inyección de
encabezados) y rompe la descarga con caracteres como '/'. Aquí se arma un
nombre ASCII seguro más la versión UTF-8 codificada (RFC 6266 / RFC 5987).
"""
import re
from urllib.parse import quote

from fastapi import Response


def _nombre_seguro(nombre: str | None, respaldo: str, extension: str) -> str:
    base = (nombre or "").strip() or respaldo
    base = base.replace("\\", "/").rsplit("/", 1)[-1]          # sin rutas
    base = re.sub(r"[\x00-\x1f\x7f\"';]", "", base)            # control y comillas
    base = re.sub(r"\s+", " ", base).strip(" .") or respaldo
    if extension and not base.lower().endswith(extension):
        base += extension
    return base[:150]


def respuesta_archivo(contenido: bytes, nombre: str | None, *, respaldo: str,
                      media_type: str = "application/pdf", extension: str = ".pdf",
                      inline: bool = True) -> Response:
    nombre_ok = _nombre_seguro(nombre, respaldo, extension)
    ascii_ok = nombre_ok.encode("ascii", "ignore").decode() or f"{respaldo}{extension}"
    modo = "inline" if inline else "attachment"
    return Response(
        content=contenido,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'{modo}; filename="{ascii_ok}"; '
                f"filename*=UTF-8''{quote(nombre_ok)}"
            ),
            # El navegador no debe "adivinar" que un PDF es HTML
            "X-Content-Type-Options": "nosniff",
            # Documentos fiscales: que no queden en cachés intermedios
            "Cache-Control": "private, no-store",
        },
    )
