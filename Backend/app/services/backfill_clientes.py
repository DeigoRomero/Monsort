import xml.etree.ElementTree as ET
from app.BaseDeDatos import SessionLocal
from app.modelos.factura import Facturas
from app.services.factura_service import resolver_cliente


def main(dry_run: bool = True):
    db = SessionLocal()
    try:
        facturas = (
            db.query(Facturas)
            .filter(
                Facturas.xml_factura.isnot(None),
                Facturas.id_cliente.is_(None)
            )
            .all()
        )

        print(f"Facturas sin id_cliente: {len(facturas)}")

        for factura in facturas:
            try:
                root = ET.fromstring(factura.xml_factura)
                receptor = root.find("{*}Receptor")
                rfc = receptor.get("Rfc", "").upper().strip()
                nombre = receptor.get("Nombre", "").strip()

                if dry_run:
                    print(f"  id={factura.id_factura} → rfc={rfc}, nombre={nombre}")
                else:
                    cliente_obj = resolver_cliente(db, rfc, nombre)
                    factura.id_cliente = cliente_obj.id
                    print(f"  id={factura.id_factura} → cliente id={cliente_obj.id} ({rfc})")

            except Exception as e:
                print(f"  id={factura.id_factura} → ERROR: {e}")

        if not dry_run:
            db.commit()
            print("Commit realizado.")
        else:
            print("\nDry run — no se guardó nada. Corre con --apply para aplicar.")

    finally:
        db.close()


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    main(dry_run)