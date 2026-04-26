# agent/tools.py — Herramientas del agente Sofía
# Generado por AgentKit — Estudiar en UK

import os
import yaml
import logging
from datetime import datetime

logger = logging.getLogger("agentkit")


def cargar_info_negocio() -> dict:
    """Carga la información del negocio desde business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_info_agencia() -> dict:
    """Retorna información básica de la agencia."""
    return {
        "nombre": "Estudiar en UK",
        "servicio": "Consultoría universitaria gratuita para hispanohablantes en Londres",
        "horario": "24/7",
        "costo": "Completamente gratuito",
    }


def buscar_en_knowledge(consulta: str) -> str:
    """
    Busca información relevante en los archivos de /knowledge.
    Retorna el contenido más relevante encontrado.
    """
    resultados = []
    knowledge_dir = "knowledge"

    if not os.path.exists(knowledge_dir):
        return "No hay archivos de conocimiento disponibles."

    for archivo in os.listdir(knowledge_dir):
        ruta = os.path.join(knowledge_dir, archivo)
        if archivo.startswith(".") or not os.path.isfile(ruta):
            continue
        if not archivo.endswith((".txt", ".md", ".csv", ".json")):
            continue
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                contenido = f.read()
                if consulta.lower() in contenido.lower():
                    resultados.append(f"[{archivo}]: {contenido[:800]}")
        except (UnicodeDecodeError, IOError):
            continue

    if resultados:
        return "\n---\n".join(resultados)
    return "No encontré información específica sobre eso en mis archivos."


def precalificar_lead(datos: dict) -> dict:
    """
    Evalúa si un potencial estudiante puede acceder a la universidad
    basándose en los datos recopilados en la conversación.

    Args:
        datos: Diccionario con campos del lead:
            - status_migratorio: "pre-settled" | "settled" | "british" | "otro"
            - nivel_ingles: "basico" | "intermedio" | "avanzado" | "nativo"
            - edad: int
            - tiene_cualificaciones: bool
            - años_experiencia_laboral: int

    Returns:
        Diccionario con resultado de precalificación
    """
    status = datos.get("status_migratorio", "").lower()
    edad = datos.get("edad", 0)
    tiene_cualificaciones = datos.get("tiene_cualificaciones", False)
    años_experiencia = datos.get("años_experiencia_laboral", 0)

    # Verificar elegibilidad para Student Finance
    elegible_student_finance = status in ("pre-settled", "settled", "british")

    # Verificar ruta de acceso
    if tiene_cualificaciones:
        ruta = "estandar"
        posibilidad = "alta"
        mensaje = "Tienes cualificaciones que te permiten aplicar por la ruta estándar."
    elif edad >= 21 and años_experiencia >= 2:
        ruta = "no-estandar"
        posibilidad = "alta"
        mensaje = "Puedes aplicar por la ruta no estándar con tu experiencia laboral."
    elif edad >= 21 and años_experiencia >= 1:
        ruta = "no-estandar"
        posibilidad = "media"
        mensaje = "Podría ser posible, depende del tipo de experiencia. Vale la pena explorar."
    else:
        ruta = "por-determinar"
        posibilidad = "por-evaluar"
        mensaje = "Necesitamos más información para evaluar tu caso correctamente."

    return {
        "elegible_student_finance": elegible_student_finance,
        "ruta_acceso": ruta,
        "posibilidad": posibilidad,
        "mensaje": mensaje,
        "requiere_asesoria": posibilidad in ("media", "por-evaluar"),
    }


def registrar_lead(datos_contacto: dict) -> dict:
    """
    Registra los datos de un lead interesado para seguimiento.
    En producción esto se integraría con GHL CRM directamente.

    Args:
        datos_contacto: nombre, telefono, disponibilidad, interes

    Returns:
        Confirmación del registro
    """
    logger.info(f"Nuevo lead registrado: {datos_contacto.get('nombre', 'Sin nombre')} — {datos_contacto.get('telefono', '')}")
    return {
        "registrado": True,
        "mensaje": f"Perfecto, {datos_contacto.get('nombre', '')}. Un asesor de Estudiar en UK te contactará pronto.",
        "timestamp": datetime.utcnow().isoformat(),
    }


def obtener_universidades_disponibles() -> list[dict]:
    """Retorna la lista de universidades disponibles para el intake actual."""
    return [
        {"nombre": "Bath Spa University (BSU)", "nivel": "Tier 1", "campus": "Bath", "ruta": ["estándar", "no estándar"]},
        {"nombre": "Newcastle College Group (NCG)", "nivel": "Tier 1", "campus": "Newcastle/Londres", "ruta": ["estándar", "no estándar"]},
        {"nombre": "Victoria College of Arts and Design (VCAD)", "nivel": "Tier 1", "campus": "Londres", "ruta": ["estándar", "no estándar", "mixta"]},
        {"nombre": "William College (WC)", "nivel": "Tier 1", "campus": "Londres", "ruta": ["estándar", "no estándar"]},
        {"nombre": "University of Sunderland London", "nivel": "Externo", "campus": "Londres", "ruta": ["estándar"]},
        {"nombre": "University of Wales Trinity Saint David (UWTSD)", "nivel": "Externo", "campus": "Londres", "ruta": ["estándar"]},
        {"nombre": "London Metropolitan University (LMU)", "nivel": "Externo", "campus": "Londres", "ruta": ["estándar"]},
        {"nombre": "Middlesex University", "nivel": "Externo", "campus": "Londres", "ruta": ["estándar"]},
        {"nombre": "London Professional College (LPC)", "nivel": "Externo", "campus": "Londres", "ruta": ["estándar"]},
    ]
