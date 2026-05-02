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
from agent.jose.brain import generar_respuesta_jose, mensaje_primer_contacto, mensaje_recordatorio_1h, mensaje_recordatorio_22h
from agent.jose.tools import parsear_email_para_jose
from agent.utils.scheduler import programar_recordatorios, cancelar_recordatorios, tiene_recordatorios_activos
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    pausar_conversacion, conversacion_esta_pausada
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
_PATRON_BOOKING_JOSE = re.compile(r'\[BOOKING_JOSE:(\{[^}]+\})\]')
_PATRON_PAUSA_JOSE = re.compile(r'\[PAUSA_JOSE:(\w+)\]')
_PATRON_OBJECION_JOSE = re.compile(r'\[OBJECION_JOSE:(\{[^}]+\})\]')

# Cache: contact_id → fase de José ("oferta_enviada", "interesado", etc.)
_cache_fase_jose: dict[str, str] = {}
# Cache: contact_id → datos de oferta parseados
_cache_oferta: dict[str, dict] = {}


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


async def _detectar_agente(contact_id: str) -> tuple[str, dict | None]:
    """
    Determina qué agente atiende basado en el custom field 'jose_fase' de GHL.
    Retorna: ("jose", oferta_dict) o ("sofia", None)
    """
    if not contact_id:
        logger.info("DEBUG _detectar_agente: contact_id vacío → Sofia")
        return "sofia", None

    # Usar cache para evitar llamadas repetidas a GHL API
    fase = _cache_fase_jose.get(contact_id)
    logger.info(f"DEBUG _detectar_agente: contact_id={contact_id}, fase_cache='{fase}'")

    if not fase:
        fase = await proveedor.obtener_custom_field(contact_id, "jose_fase")
        logger.info(f"DEBUG _detectar_agente: fase_ghl='{fase}'")
        if fase:
            _cache_fase_jose[contact_id] = fase

    if fase in ("oferta_enviada", "interesado", "objecion"):
        # Obtener oferta desde cache o GHL API
        oferta = _cache_oferta.get(contact_id)
        if not oferta:
            email = await proveedor.obtener_ultimo_email(contact_id)
            oferta = parsear_email_para_jose(email) if email else {}
            _cache_oferta[contact_id] = oferta
        logger.info(f"DEBUG _detectar_agente: → JOSÉ (fase={fase})")
        return "jose", oferta

    logger.info(f"DEBUG _detectar_agente: → SOFÍA (fase='{fase}')")
    return "sofia", None


async def _enviar_recordatorio_jose(telefono: str, tipo: str):
    """Callback del scheduler — envía recordatorio de José por WhatsApp."""
    contact_id = proveedor._contact_cache.get(telefono, "")
    oferta = _cache_oferta.get(contact_id)

    if tipo == "1h":
        msg = mensaje_recordatorio_1h()
        logger.info(f"Enviando recordatorio 1h a {telefono}")
        # Actualizar GHL: recordatorio enviado
        if contact_id:
            await proveedor.actualizar_custom_field(telefono, "contact.jose_mensaje_1h_enviado", "true")
    else:
        msg = mensaje_recordatorio_22h()
        logger.info(f"Enviando recordatorio 22h a {telefono}")
        if contact_id:
            await proveedor.actualizar_custom_field(telefono, "contact.jose_mensaje_22h_enviado", "true")

    await guardar_mensaje(telefono, "assistant", msg)
    await proveedor.enviar_mensaje(telefono, msg)


async def _extraer_y_procesar_jose(respuesta: str, telefono: str) -> str:
    """
    Detecta tokens especiales de José y ejecuta acciones:
    - [BOOKING_JOSE:{...}] → notifica cita al asesor + pausa conversación
    - [PAUSA_JOSE:razón] → pausa conversación
    - [OBJECION_JOSE:{...}] → registra objeción en GHL
    """
    # Booking de cita con José
    match_booking = _PATRON_BOOKING_JOSE.search(respuesta)
    if match_booking:
        try:
            datos = json.loads(match_booking.group(1))
            nombre = datos.get("nombre", "N/D")
            fecha = datos.get("fecha", "N/D")
            hora = datos.get("hora", "N/D")

            notif = (
                f"📅 Cita agendada por JOSÉ\n"
                f"• Nombre: {nombre}\n"
                f"• Fecha: {fecha}\n"
                f"• Hora: {hora}\n"
                f"• WhatsApp cliente: {telefono}"
            )
            await proveedor.enviar_mensaje(OWNER_WHATSAPP, notif)

            await proveedor.actualizar_custom_field(telefono, "contact.jose_cita_fecha", fecha)
            await proveedor.actualizar_custom_field(telefono, "contact.jose_cita_hora", hora)
            await proveedor.actualizar_custom_field(telefono, "contact.jose_fase", "cita_agendada")

            # Limpiar cache para que el router refleje la nueva fase
            contact_id = proveedor._contact_cache.get(telefono, "")
            if contact_id in _cache_fase_jose:
                _cache_fase_jose[contact_id] = "cita_agendada"

            cancelar_recordatorios(telefono)
            logger.info(f"Cita José confirmada — {nombre} el {fecha} a las {hora}")
        except Exception as e:
            logger.error(f"Error procesando BOOKING_JOSE: {e}")
        respuesta = _PATRON_BOOKING_JOSE.sub("", respuesta).strip()

    # Pausa post-José
    match_pausa = _PATRON_PAUSA_JOSE.search(respuesta)
    if match_pausa:
        try:
            razon = match_pausa.group(1)
            await pausar_conversacion(telefono, razon)
            cancelar_recordatorios(telefono)
            logger.info(f"Conversación pausada por José — {telefono}: {razon}")
        except Exception as e:
            logger.error(f"Error pausando conversación (José): {e}")
        respuesta = _PATRON_PAUSA_JOSE.sub("", respuesta).strip()

    # Objeción detectada
    match_objecion = _PATRON_OBJECION_JOSE.search(respuesta)
    if match_objecion:
        try:
            datos = json.loads(match_objecion.group(1))
            razon = datos.get("razon", "no_especificada")
            await proveedor.actualizar_custom_field(telefono, "contact.jose_objecion", razon)
            await proveedor.actualizar_custom_field(telefono, "contact.jose_fase", "objecion")
            logger.info(f"Objeción registrada para {telefono}: {razon}")
        except Exception as e:
            logger.error(f"Error procesando OBJECION_JOSE: {e}")
        respuesta = _PATRON_OBJECION_JOSE.sub("", respuesta).strip()

    return respuesta


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
            "contact.cita_fecha_sofia",
            fecha
        )
        exito_hora = await proveedor.actualizar_custom_field(
            telefono_cliente,
            "contact.cita_hora_sofia",
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

    # Si la conversación está pausada, ignorar mensajes — solo se reactiva manualmente
    if await conversacion_esta_pausada(telefono):
        logger.info(f"Conversación pausada para {telefono} — mensaje ignorado")
        return

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

        # Obtener contact_id del cache del proveedor
        contact_id = proveedor._contact_cache.get(telefono, "")

        # Router: ¿José o Sofía?
        agente, oferta = await _detectar_agente(contact_id)

        if agente == "jose":
            logger.info(f"Router → JOSÉ para {telefono}")
            respuesta = await generar_respuesta_jose(texto_combinado, historial, oferta=oferta)
            respuesta = await _extraer_y_procesar_jose(respuesta, telefono)
            # Si el estudiante respondió, cancelar recordatorios pendientes
            cancelar_recordatorios(telefono)
        else:
            logger.info(f"Router → SOFÍA para {telefono}")
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


@app.post("/webhook/jose-activar")
async def jose_activar(request: Request):
    """
    Endpoint exclusivo para GHL Workflow.
    Se llama cuando el asesor mueve un contact a 'jose_fase = oferta_enviada'.
    José envía el primer mensaje automáticamente y programa los recordatorios.

    GHL Workflow → Webhook Action → POST /webhook/jose-activar
    Body esperado: { "contact_id": "...", "phone": "...", "name": "..." }
    """
    try:
        body = await request.json()
        telefono_raw = body.get("phone") or body.get("contactPhone") or ""
        contact_id = str(body.get("contact_id") or body.get("contactId") or "")
        nombre = body.get("name") or body.get("firstName") or ""
        telefono = proveedor._normalizar_telefono(telefono_raw) if telefono_raw else ""

        if not telefono:
            logger.warning("jose-activar: teléfono no encontrado en payload")
            return {"status": "error", "detail": "telefono requerido"}

        # Cachear contact_id para envíos futuros
        if contact_id:
            proveedor._contact_cache[telefono] = contact_id
            _cache_fase_jose[contact_id] = "oferta_enviada"

        # Obtener email y parsear oferta
        email = await proveedor.obtener_ultimo_email(contact_id) if contact_id else None
        oferta = parsear_email_para_jose(email) if email else {}
        if contact_id:
            _cache_oferta[contact_id] = oferta

        # Enviar primer mensaje de José
        msg_inicial = mensaje_primer_contacto()
        await guardar_mensaje(telefono, "assistant", msg_inicial)
        await proveedor.enviar_mensaje(telefono, msg_inicial)

        # Programar recordatorios automáticos (1h y 22h)
        programar_recordatorios(telefono, _enviar_recordatorio_jose)

        # Actualizar GHL: registrar primer contacto de José
        await proveedor.actualizar_custom_field(telefono, "contact.jose_ultimo_mensaje", "activado")

        logger.info(f"José activado para {telefono} ({nombre}) — recordatorios programados")
        return {"status": "ok", "agente": "jose", "telefono": telefono}

    except Exception as e:
        logger.error(f"Error activando José: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
