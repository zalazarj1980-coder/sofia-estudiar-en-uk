# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit — Estudiar en UK

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    logger.info("Base de datos inicializada")
    logger.info(f"Servidor AgentKit corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")
    yield


app = FastAPI(
    title="Sofía — Agente WhatsApp de Estudiar en UK",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def health_check():
    """Endpoint de salud para Railway/monitoreo."""
    return {"status": "ok", "agente": "Sofía", "negocio": "Estudiar en UK"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta, no-op para GHL)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


async def procesar_mensaje(telefono: str, texto: str, mensaje_id: str):
    """Procesa el mensaje en segundo plano: genera respuesta y la envía."""
    try:
        logger.info(f"Procesando mensaje de {telefono}: {texto}")
        historial = await obtener_historial(telefono)
        respuesta = await generar_respuesta(texto, historial)
        await guardar_mensaje(telefono, "user", texto)
        await guardar_mensaje(telefono, "assistant", respuesta)
        await proveedor.enviar_mensaje(telefono, respuesta)
        logger.info(f"Respuesta enviada a {telefono}: {respuesta}")
    except Exception as e:
        logger.error(f"Error procesando mensaje de {telefono}: {e}")


@app.post("/webhook")
async def webhook_handler(request: Request, background_tasks: BackgroundTasks):
    """
    Recibe mensajes de WhatsApp via GHL.
    Responde 200 inmediatamente y procesa el mensaje en segundo plano
    para no exceder el timeout del webhook de GHL.
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue
            # Encolar en background — GHL recibe 200 de inmediato
            background_tasks.add_task(procesar_mensaje, msg.telefono, msg.texto, msg.mensaje_id)

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error parseando webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
