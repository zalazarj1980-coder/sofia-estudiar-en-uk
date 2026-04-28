# agent/brain.py — Cerebro del agente: conexión con Claude API
# Generado por AgentKit

import os
import yaml
import logging
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


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera una respuesta usando Claude API.

    Args:
        mensaje: El mensaje nuevo del usuario
        historial: Lista de mensajes anteriores [{"role": "user/assistant", "content": "..."}]

    Returns:
        La respuesta generada por Claude
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()

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

    mensajes.append({
        "role": "user",
        "content": mensaje
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
