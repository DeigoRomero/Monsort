from app.services.sat_descarga_client_real import construir_cliente_sat

cliente = construir_cliente_sat()
if cliente is None:
    print("No hay e.firma configurada. Revisa SAT_* en el .env")
else:
    print("Token:", cliente.autenticar()[:40], "...")