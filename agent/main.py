# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit — Estudiar en UK

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
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

# Buffer por teléfono: telefono → lista de (texto, mensaje_id)
_buffer_mensajes: dict[str, list[tuple[str, str]]] = {}
# Timer por teléfono: telefono → asyncio.Task
_buffer_timers: dict[str, asyncio.Task] = {}
BUFFER_DELAY_SEGUNDOS = 7  # espera 7s para acumular mensajes del mismo usuario


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
    return {"status": "ok", "agente": "Sofía", "negocio": "Estudiar en UK"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


async def _procesar_buffer(telefono: str):
    """Espera BUFFER_DELAY_SEGUNDOS y procesa todos los mensajes acumulados."""
    try:
        await asyncio.sleep(BUFFER_DELAY_SEGUNDOS)
    except asyncio.CancelledError:
        return  # Llegó otro mensaje — el nuevo timer lo maneja

    mensajes_pendientes = _buffer_mensajes.pop(telefono, [])
    _buffer_timers.pop(telefono, None)

    if not mensajes_pendientes:
        return

    # Combinar todos los mensajes en uno si el usuario envió varios seguidos
    if len(mensajes_pendientes) == 1:
        texto_combinado = mensajes_pendientes[0][0]
    else:
        texto_combinado = "\n".join(texto for texto, _ in mensajes_pendientes)
        logger.info(f"Combinando {len(mensajes_pendientes)} mensajes de {telefono}")

    try:
        logger.info(f"Procesando mensaje de {telefono}: {texto_combinado[:100]}...")
        historial = await obtener_historial(telefono)
        respuesta = await generar_respuesta(texto_combinado, historial)
        await guardar_mensaje(telefono, "user", texto_combinado)
        await guardar_mensaje(telefono, "assistant", respuesta)
        await proveedor.enviar_mensaje(telefono, respuesta)
        logger.info(f"Respuesta enviada a {telefono}")
    except Exception as e:
        logger.error(f"Error procesando mensaje de {telefono}: {e}")


async def _encolar_mensaje(telefono: str, texto: str, mensaje_id: str):
    """Agrega mensaje al buffer y reinicia el timer de 7 segundos."""
    if telefono not in _buffer_mensajes:
        _buffer_mensajes[telefono] = []
    _buffer_mensajes[telefono].append((texto, mensaje_id))

    # Cancelar timer existente y crear uno nuevo
    timer_actual = _buffer_timers.get(telefono)
    if timer_actual and not timer_actual.done():
        timer_actual.cancel()

    _buffer_timers[telefono] = asyncio.create_task(_procesar_buffer(telefono))
    logger.debug(f"Encolado para {telefono} — {len(_buffer_mensajes[telefono])} en buffer")


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp via GHL.
    Responde 200 inmediatamente y acumula mensajes 7s antes de procesar,
    permitiendo que el usuario envíe varios mensajes seguidos como uno solo.
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue
            await _encolar_mensaje(msg.telefono, msg.texto, msg.mensaje_id)

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error parseando webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
