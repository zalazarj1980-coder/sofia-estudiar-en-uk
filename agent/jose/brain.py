# agent/jose/brain.py — Cerebro del agente Jose
import os
import yaml
import logging
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from agent.jose.tools import formatear_oferta_para_contexto

load_dotenv()
logger = logging.getLogger("agentkit.jose")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def _cargar_config() -> dict:
    try:
        with open("config/jose.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/jose.yaml no encontrado")
        return {}


def cargar_system_prompt_jose(oferta: dict | None = None) -> str:
    """
    Construye el system prompt de Jose inyectando los datos de la oferta
    al final del prompt base. Asi Jose conoce el contexto de cada estudiante.
    """
    config = _cargar_config()
    base = config.get("system_prompt", "Eres Jose, asesor de seguimiento de Estudiar en UK.")

    if oferta:
        contexto_oferta = formatear_oferta_para_contexto(oferta)
        return (
            f"{base}\n\n"
            f"## Oferta enviada a este estudiante\n"
            f"{contexto_oferta}"
        )
    return base


def mensaje_primer_contacto() -> str:
    config = _cargar_config()
    return config.get("mensajes", {}).get(
        "primer_contacto",
        "Hola, soy Jose de Estudiar en UK. Pudiste revisar la oferta que te enviamos?"
    )


def mensaje_recordatorio_1h() -> str:
    config = _cargar_config()
    return config.get("mensajes", {}).get(
        "recordatorio_1h",
        "Hola de nuevo! Pudiste ver la informacion que te enviamos? Cualquier duda, aqui estoy."
    )


def mensaje_recordatorio_22h() -> str:
    config = _cargar_config()
    return config.get("mensajes", {}).get(
        "recordatorio_22h",
        "Dato importante: el Student Finance cubre el costo completo. Tienes alguna duda sobre tu oferta?"
    )


async def generar_respuesta_jose(
    mensaje: str,
    historial: list[dict],
    oferta: dict | None = None,
) -> str:
    """
    Genera una respuesta de Jose usando Claude API.

    Args:
        mensaje: Mensaje del estudiante
        historial: Historial de conversacion
        oferta: Datos parseados del email de oferta (opcional)

    Returns:
        Respuesta generada por Claude como Jose
    """
    config = _cargar_config()

    if not mensaje or len(mensaje.strip()) < 2:
        return config.get("fallback_message", "Puedes contarme mas? Estoy aqui para ayudarte.")

    system_prompt = cargar_system_prompt_jose(oferta)

    mensajes = [{"role": m["role"], "content": m["content"]} for m in historial]
    mensajes.append({"role": "user", "content": mensaje})

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=system_prompt,
            messages=mensajes,
        )
        respuesta = response.content[0].text
        logger.info(
            f"Jose respondio ({response.usage.input_tokens} in / "
            f"{response.usage.output_tokens} out)"
        )
        return respuesta

    except Exception as e:
        logger.error(f"Error Claude API (Jose): {e}")
        return config.get(
            "error_message",
            "Tuve un problema tecnico. Escribeme de nuevo en un momento."
        )
