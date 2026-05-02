# agent/brain.py — Cerebro del agente: conexión con Claude API
# Generado por AgentKit

import os
import base64
import yaml
import logging
import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def cargar_config_prompts() -> dict:
    """Lee toda la configuración desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    config = cargar_config_prompts()
    return config.get("system_prompt", "Eres Sofía, una asistente útil. Responde siempre en español.")


def obtener_mensaje_error() -> str:
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas técnicos. Por favor intenta de nuevo en unos minutos.")


def obtener_mensaje_fallback() -> str:
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpa, no entendí tu mensaje. ¿Podrías reformularlo?")


async def _descargar_imagen_base64(url: str) -> tuple[str, str] | None:
    """
    Descarga una imagen desde una URL y la retorna como (base64, media_type).
    Retorna None si falla la descarga.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http:
            r = await http.get(url)
            if r.status_code != 200:
                logger.warning(f"No se pudo descargar imagen ({r.status_code}): {url[:80]}")
                return None
            content_type = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            # Normalizar a tipos que acepta Claude
            tipos_validos = {"image/jpeg", "image/png", "image/gif", "image/webp"}
            if content_type not in tipos_validos:
                content_type = "image/jpeg"
            datos_b64 = base64.standard_b64encode(r.content).decode("utf-8")
            return datos_b64, content_type
    except Exception as e:
        logger.error(f"Error descargando imagen: {e}")
        return None


async def generar_respuesta(
    mensaje: str,
    historial: list[dict],
    imagen_url: str | None = None,
    idioma_recien_detectado: str | None = None,
) -> str:
    """
    Genera una respuesta usando Claude API.

    Args:
        mensaje: El mensaje nuevo del usuario
        historial: Lista de mensajes anteriores [{"role": "user/assistant", "content": "..."}]
        imagen_url: URL de imagen adjunta enviada por el usuario (opcional)
        idioma_recien_detectado: Si acaba de detectarse un idioma (ej: "español"), se agrega al prompt

    Returns:
        La respuesta generada por Claude
    """
    tiene_imagen = bool(imagen_url)
    if not mensaje or len(mensaje.strip()) < 2:
        if not tiene_imagen:
            return obtener_mensaje_fallback()
        mensaje = ""  # Imagen sin caption — Claude analizará solo la imagen

    system_prompt = cargar_system_prompt()

    # Si acaba de detectarse un idioma, inyectar instrucción al prompt
    if idioma_recien_detectado:
        system_prompt += f"\n\n⚠️ IMPORTANTE: El cliente ACABA DE ELEGIR idioma '{idioma_recien_detectado}'. Responde su mensaje confirmando que hablarán en {idioma_recien_detectado}, y continúa en ÚNICAMENTE ese idioma."

    # Inyectar instrucción según avance de la conversación
    num_mensajes_usuario = sum(1 for m in historial if m["role"] == "user") + 1
    if num_mensajes_usuario >= 15:
        system_prompt += (
            "\n\n⚠️ URGENTE: Ya llevan 15+ mensajes. DEBES mostrar ahora un resumen "
            "del perfil con los datos recopilados (nombre si lo tienes, status migratorio, "
            "área de interés) y dirigir directamente al calendario para agendar: "
            "https://estudiarenuk.co.uk/agendamiento — No hagas más preguntas de precalificación."
        )
    elif num_mensajes_usuario >= 12:
        system_prompt += (
            "\n\n⚠️ IMPORTANTE: La conversación avanza. Intenta cerrar la "
            "precalificación en los próximos 1-2 mensajes y ofrece el agendamiento."
        )

    mensajes = []
    for msg in historial:
        mensajes.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Construir el contenido del mensaje del usuario
    if tiene_imagen:
        imagen_data = await _descargar_imagen_base64(imagen_url)
        if imagen_data:
            datos_b64, media_type = imagen_data
            content_usuario: list | str = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": datos_b64,
                    },
                },
                {
                    "type": "text",
                    "text": mensaje if mensaje else "El cliente envió esta imagen.",
                },
            ]
            logger.info(f"Imagen incluida en el mensaje ({media_type})")
        else:
            # Si no se pudo descargar, procesar como texto normal con aviso
            content_usuario = mensaje or "[El cliente intentó enviar una imagen pero no se pudo procesar]"
    else:
        content_usuario = mensaje

    mensajes.append({
        "role": "user",
        "content": content_usuario,
    })

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=mensajes
        )

        respuesta = response.content[0].text
        logger.info(f"Respuesta generada ({response.usage.input_tokens} in / {response.usage.output_tokens} out)")
        return respuesta

    except Exception as e:
        logger.error(f"Error Claude API: {e}")
        return obtener_mensaje_error()
