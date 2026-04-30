# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit — Estudiar en UK

import os
import re
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    pausar_conversacion, reanudar_conversacion, conversacion_esta_pausada
)
from agent.providers import obtener_proveedor

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))

# Buffer por teléfono: telefono → lista de (texto, mensaje_id, imagen_url)
_buffer_mensajes: dict[str, list[tuple[str, str, str | None]]] = {}
# Timer por teléfono: telefono → asyncio.Task
_buffer_timers: dict[str, asyncio.Task] = {}
BUFFER_DELAY_SEGUNDOS = 7  # espera 7s para acumular mensajes del mismo usuario
OWNER_WHATSAPP = os.getenv("OWNER_WHATSAPP", "+447596099207")
_PATRON_BOOKING = re.compile(r'\[BOOKING:(\{[^}]+\})\]')
_PATRON_PAUSA = re.compile(r'\[PAUSA:(\w+)\]')


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


async def _extraer_y_notificar_booking(respuesta: str, telefono_cliente: str) -> str:
    """
    Busca [BOOKING:{...}] en la respuesta, lo elimina del texto que ve el usuario,
    envía una notificación de WhatsApp al dueño del negocio,
    y actualiza el custom field 'cita_confirmada_sofia' en GHL.
    """
    match = _PATRON_BOOKING.search(respuesta)
    if not match:
        return respuesta

    try:
        datos = json.loads(match.group(1))
        nombre = datos.get("nombre", "N/D")
        fecha = datos.get("fecha", "N/D")
        hora = datos.get("hora", "N/D")

        # Notificar al dueño
        mensaje_notif = (
            f"🔔 Nueva cita confirmada en WhatsApp\n"
            f"• Nombre: {nombre}\n"
            f"• Fecha: {fecha}\n"
            f"• Hora: {hora}\n"
            f"• WhatsApp cliente: {telefono_cliente}"
        )
        await proveedor.enviar_mensaje(OWNER_WHATSAPP, mensaje_notif)
        logger.info(f"Notificación de booking enviada — {nombre} el {fecha} a las {hora}")

        # Actualizar custom fields en GHL (dos campos para workflow)
        exito_fecha = await proveedor.actualizar_custom_field(
            telefono_cliente,
            "cita_fecha_sofia",
            fecha
        )
        exito_hora = await proveedor.actualizar_custom_field(
            telefono_cliente,
            "cita_hora_sofia",
            hora
        )
        if exito_fecha and exito_hora:
            logger.info(f"Custom fields actualizados en GHL para {telefono_cliente} — {fecha} {hora}")
        else:
            logger.warning(f"No se pudieron actualizar custom fields en GHL para {telefono_cliente}")

    except Exception as e:
        logger.error(f"Error procesando booking: {e}")

    return _PATRON_BOOKING.sub("", respuesta).strip()


async def _extraer_y_pausar(respuesta: str, telefono_cliente: str) -> str:
    """
    Busca [PAUSA:razón] en la respuesta, lo elimina del texto que ve el usuario
    y pausa la conversación.
    """
    match = _PATRON_PAUSA.search(respuesta)
    if not match:
        return respuesta

    try:
        razon = match.group(1)
        await pausar_conversacion(telefono_cliente, razon)
        logger.info(f"Conversación pausada para {telefono_cliente} — razón: {razon}")
    except Exception as e:
        logger.error(f"Error pausando conversación: {e}")

    return _PATRON_PAUSA.sub("", respuesta).strip()


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

    # Si la conversación está pausada, reanudarla automáticamente
    if await conversacion_esta_pausada(telefono):
        logger.info(f"Reactivando conversación pausada para {telefono}")
        await reanudar_conversacion(telefono)

    # Combinar todos los mensajes en uno si el usuario envió varios seguidos
    # La primera imagen encontrada se usa para el análisis visual
    imagen_url = next((img for _, _, img in mensajes_pendientes if img), None)
    if len(mensajes_pendientes) == 1:
        texto_combinado = mensajes_pendientes[0][0]
    else:
        texto_combinado = "\n".join(texto for texto, _, _ in mensajes_pendientes if texto)
        logger.info(f"Combinando {len(mensajes_pendientes)} mensajes de {telefono}")

    try:
        logger.info(f"Procesando mensaje de {telefono}: {texto_combinado[:100] or '[imagen]'}...")
        historial = await obtener_historial(telefono)
        respuesta = await generar_respuesta(texto_combinado, historial, imagen_url=imagen_url)
        respuesta = await _extraer_y_notificar_booking(respuesta, telefono)
        respuesta = await _extraer_y_pausar(respuesta, telefono)
        await guardar_mensaje(telefono, "user", texto_combinado or "[imagen]")
        await guardar_mensaje(telefono, "assistant", respuesta)
        await proveedor.enviar_mensaje(telefono, respuesta)
        logger.info(f"Respuesta enviada a {telefono}")
    except Exception as e:
        logger.error(f"Error procesando mensaje de {telefono}: {e}")


async def _encolar_mensaje(telefono: str, texto: str, mensaje_id: str, imagen_url: str | None = None):
    """Agrega mensaje al buffer y reinicia el timer de 7 segundos."""
    if telefono not in _buffer_mensajes:
        _buffer_mensajes[telefono] = []
    _buffer_mensajes[telefono].append((texto, mensaje_id, imagen_url))

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
            if msg.es_propio or (not msg.texto and not msg.imagen_url):
                continue
            await _encolar_mensaje(msg.telefono, msg.texto, msg.mensaje_id, msg.imagen_url)

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error parseando webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
