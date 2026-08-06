# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit — Estudiar en UK

import os
import re
import json
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

# brain.py carga .env al importarse
from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    pausar_conversacion, reanudar_conversacion, conversacion_esta_pausada
)
from agent.providers import obtener_proveedor

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
BUFFER_DELAY_SEGUNDOS = 7
OWNER_WHATSAPP = os.getenv("OWNER_WHATSAPP", "+447596099207")
_PATRON_PAUSA = re.compile(r'\[PAUSA:\s*(\w+)\s*\]')


def _extraer_bloque_booking(texto: str) -> tuple[dict | None, str]:
    """
    Extrae [BOOKING:{...}] del texto de forma robusta.
    Tolera espacios, JSON con llaves anidadas, y comillas variadas.
    Loggea cuando aparece 'BOOKING' pero el bloque está mal formado.

    Retorna: (datos_dict, texto_sin_bloque) — datos_dict es None si no hay booking válido.
    """
    # Buscar inicio del bloque (case-insensitive y permite espacios)
    match_inicio = re.search(r'\[BOOKING\s*:\s*', texto, re.IGNORECASE)
    if not match_inicio:
        # Log si la palabra aparece pero el patrón está roto
        if "BOOKING" in texto.upper():
            posicion = texto.upper().find("BOOKING")
            snippet = texto[max(0, posicion - 20):posicion + 200]
            logger.warning(f"'BOOKING' aparece en respuesta pero sin patrón válido. Snippet: {snippet}")
        return None, texto

    inicio_bloque = match_inicio.start()
    json_inicio = texto.find("{", match_inicio.end() - 1)
    if json_inicio == -1:
        logger.warning(f"[BOOKING: encontrado pero sin '{{' del JSON. Snippet: {texto[inicio_bloque:inicio_bloque + 200]}")
        return None, texto

    # Encontrar la llave de cierre balanceada (soporta JSON anidado)
    depth = 0
    json_fin = -1
    for i in range(json_inicio, len(texto)):
        if texto[i] == "{":
            depth += 1
        elif texto[i] == "}":
            depth -= 1
            if depth == 0:
                json_fin = i + 1
                break

    if json_fin == -1:
        logger.warning(f"[BOOKING:{{ sin llave de cierre. Snippet: {texto[inicio_bloque:inicio_bloque + 300]}")
        return None, texto

    # Buscar el "]" final (puede tener espacios antes)
    resto = texto[json_fin:]
    match_cierre = re.match(r'\s*\]', resto)
    if not match_cierre:
        logger.warning(f"[BOOKING:{{...}} sin ']' de cierre. Snippet: {texto[inicio_bloque:inicio_bloque + 300]}")
        return None, texto
    fin_bloque = json_fin + match_cierre.end()

    json_str = texto[json_inicio:json_fin]
    try:
        datos = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"Error parseando JSON de BOOKING: {e}. JSON crudo: {json_str}")
        return None, texto

    # Remover el bloque completo
    texto_limpio = (texto[:inicio_bloque] + texto[fin_bloque:]).strip()
    return datos, texto_limpio


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
    y actualiza custom fields en GHL para disparar el workflow de booking.
    """
    datos, respuesta_limpia = _extraer_bloque_booking(respuesta)
    if datos is None:
        return respuesta

    try:
        nombre = datos.get("nombre", "N/D")
        fecha = datos.get("fecha", "N/D")
        hora = datos.get("hora", "N/D")

        logger.info(f"BOOKING detectado — nombre={nombre}, fecha={fecha}, hora={hora}, telefono={telefono_cliente}")

        mensaje_notif = (
            f"🔔 Nueva cita confirmada en WhatsApp\n"
            f"• Nombre: {nombre}\n"
            f"• Fecha: {fecha}\n"
            f"• Hora: {hora}\n"
            f"• WhatsApp cliente: {telefono_cliente}"
        )
        await proveedor.enviar_mensaje(OWNER_WHATSAPP, mensaje_notif)
        logger.info(f"Notificación de booking enviada al owner")

        # Actualizar 3 custom fields en GHL:
        # 1) cita_fecha_sofia: fecha de la cita
        # 2) cita_hora_sofia: hora de la cita
        # 3) cita_solicitada_sofia = "true" → trigger único para el workflow GHL
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
        exito_trigger = await proveedor.actualizar_custom_field(
            telefono_cliente,
            "contact.cita_solicitada_sofia",
            "true"
        )

        logger.info(
            f"GHL custom fields — fecha={exito_fecha}, hora={exito_hora}, trigger={exito_trigger}"
        )

        if not (exito_fecha and exito_hora and exito_trigger):
            logger.warning(f"Algunos custom fields fallaron al actualizar para {telefono_cliente}")

    except Exception as e:
        logger.error(f"Error procesando booking: {e}")

    return respuesta_limpia


async def _extraer_y_pausar(respuesta: str, telefono_cliente: str) -> str:
    """
    Busca [PAUSA:razón] en la respuesta, lo elimina del texto que ve el usuario,
    pausa la conversación localmente y agrega el tag 'agente_pausado' en GHL
    para que quede visible en el CRM.
    """
    match = _PATRON_PAUSA.search(respuesta)
    if not match:
        return respuesta

    try:
        razon = match.group(1)
        await pausar_conversacion(telefono_cliente, razon)
        logger.info(f"Conversación pausada para {telefono_cliente} — razón: {razon}")

        # Sincronizar con GHL: agregar tag 'agente_pausado' para que sea visible en el CRM
        try:
            await proveedor.agregar_tag(telefono_cliente, "agente_pausado")
        except AttributeError:
            logger.debug("El proveedor no soporta agregar_tag — saltando sincronización GHL")
        except Exception as e:
            logger.error(f"Error agregando tag agente_pausado en GHL: {e}")

    except Exception as e:
        logger.error(f"Error pausando conversación: {e}")

    return _PATRON_PAUSA.sub("", respuesta).strip()


async def _procesar_buffer(telefono: str):
    """Espera BUFFER_DELAY_SEGUNDOS y procesa todos los mensajes acumulados."""
    try:
        await asyncio.sleep(BUFFER_DELAY_SEGUNDOS)
    except asyncio.CancelledError:
        return

    mensajes_pendientes = _buffer_mensajes.pop(telefono, [])
    _buffer_timers.pop(telefono, None)

    if not mensajes_pendientes:
        return

    if await conversacion_esta_pausada(telefono):
        logger.info(f"Conversación pausada para {telefono} — mensaje ignorado")
        return

    imagen_url = next((img for _, _, img in mensajes_pendientes if img), None)
    if len(mensajes_pendientes) == 1:
        texto_combinado = mensajes_pendientes[0][0]
    else:
        texto_combinado = "\n".join(texto for texto, _, _ in mensajes_pendientes if texto)
        logger.info(f"Combinando {len(mensajes_pendientes)} mensajes de {telefono}")

    try:
        logger.info(f"Procesando mensaje de {telefono}: {texto_combinado[:100] or '[imagen]'}...")
        historial = await obtener_historial(telefono)

        respuesta = await generar_respuesta(
            texto_combinado,
            historial,
            imagen_url=imagen_url,
        )
        # Log de la respuesta CRUDA antes de procesar (para diagnostico de booking)
        respuesta_cruda = respuesta
        logger.info(f"RESPUESTA CRUDA de Sofía ({len(respuesta_cruda)} chars): {respuesta_cruda!r}")

        # Detección "fantasma": Sofía dijo "cita confirmada" en la respuesta CRUDA pero NO emitió bloque
        frases_confirmacion = ["cita confirmada", "cita agendada", "confirmada para el"]
        dijo_confirmacion = any(frase in respuesta_cruda.lower() for frase in frases_confirmacion)
        emitio_bloque = "[BOOKING" in respuesta_cruda.upper()
        if dijo_confirmacion and not emitio_bloque:
            logger.critical(
                f"⚠️ ALERTA FANTASMA — Sofía dijo 'cita confirmada' pero NO emitió [BOOKING:...] para {telefono}. "
                f"Respuesta cruda: {respuesta_cruda!r}"
            )

        respuesta = await _extraer_y_notificar_booking(respuesta, telefono)
        respuesta = await _extraer_y_pausar(respuesta, telefono)

        await guardar_mensaje(telefono, "user", texto_combinado or "[imagen]")
        await guardar_mensaje(telefono, "assistant", respuesta)
        await proveedor.enviar_mensaje(telefono, respuesta)
        logger.info(f"Respuesta enviada a {telefono}")
    except Exception:
        # Incluye el traceback completo (por ejemplo, timeout al enviar a GHL).
        logger.exception(f"Error procesando mensaje de {telefono}")


async def _encolar_mensaje(telefono: str, texto: str, mensaje_id: str, imagen_url: str | None = None):
    """Agrega mensaje al buffer y reinicia el timer de 7 segundos."""
    if telefono not in _buffer_mensajes:
        _buffer_mensajes[telefono] = []
    _buffer_mensajes[telefono].append((texto, mensaje_id, imagen_url))

    timer_actual = _buffer_timers.get(telefono)
    if timer_actual and not timer_actual.done():
        timer_actual.cancel()

    _buffer_timers[telefono] = asyncio.create_task(_procesar_buffer(telefono))
    logger.debug(f"Encolado para {telefono} — {len(_buffer_mensajes[telefono])} en buffer")


def _extraer_telefono_desde_payload_ghl(body: dict) -> str:
    """
    Intenta extraer el teléfono del payload de GHL, probando múltiples nombres
    de campo (GHL varía según el tipo de webhook).
    """
    contact = body.get("contact") if isinstance(body.get("contact"), dict) else {}
    customer = body.get("customer") if isinstance(body.get("customer"), dict) else {}
    custom_data = body.get("customData") if isinstance(body.get("customData"), dict) else {}

    return (
        body.get("phone")
        or body.get("contactPhone")
        or body.get("contact_phone")
        or body.get("Phone")
        or contact.get("phone")
        or customer.get("phone")
        or custom_data.get("phone")
        or ""
    )


@app.post("/webhook/pausar-contacto")
async def pausar_contacto(request: Request):
    """
    GHL llama aquí cuando se agrega el tag 'agente_pausado' a un contacto.
    Pausa la conversación: Sofía dejará de responder hasta que se reactive.
    """
    try:
        body = await request.json()
        logger.info(f"PAUSA webhook payload completo: {body}")

        telefono_raw = _extraer_telefono_desde_payload_ghl(body)
        if not telefono_raw:
            logger.warning(f"pausar-contacto: no se recibió teléfono. Keys del payload: {list(body.keys())}")
            return {"status": "error", "detail": "telefono requerido", "payload_keys": list(body.keys())}

        telefono = proveedor._normalizar_telefono(telefono_raw)
        await pausar_conversacion(telefono, "pausa_manual_ghl")
        logger.info(f"Sofía PAUSADA manualmente vía GHL para {telefono} (raw: {telefono_raw})")
        return {"status": "ok", "telefono": telefono, "accion": "pausada"}
    except Exception as e:
        logger.error(f"Error pausando contacto: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook/reanudar-contacto")
async def reanudar_contacto(request: Request):
    """
    GHL llama aquí cuando se quita el tag 'agente_pausado' de un contacto.
    Reanuda la conversación: Sofía volverá a responder en el próximo mensaje.
    """
    try:
        body = await request.json()
        logger.info(f"REANUDAR webhook payload completo: {body}")

        telefono_raw = _extraer_telefono_desde_payload_ghl(body)
        if not telefono_raw:
            logger.warning(f"reanudar-contacto: no se recibió teléfono. Keys del payload: {list(body.keys())}")
            return {"status": "error", "detail": "telefono requerido", "payload_keys": list(body.keys())}

        telefono = proveedor._normalizar_telefono(telefono_raw)
        await reanudar_conversacion(telefono)
        logger.info(f"Sofía REANUDADA manualmente vía GHL para {telefono} (raw: {telefono_raw})")
        return {"status": "ok", "telefono": telefono, "accion": "reanudada"}
    except Exception as e:
        logger.error(f"Error reanudando contacto: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp via GHL.
    Responde 200 inmediatamente y acumula mensajes 7s antes de procesar.
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
