from apscheduler.schedulers.background import  BackgroundScheduler
from app.BaseDeDatos import SessionLocal
from app.services.factura_service import procesar_correos_nuevos

def job_procesar_correos():
    db = SessionLocal()
    try:
        procesar_correos_nuevos(db)
    finally:
        db.close()

scheduler = BackgroundScheduler()
scheduler.add_job(job_procesar_correos, 'interval', minutes=2) # Ejecutar cada 2 minutos
