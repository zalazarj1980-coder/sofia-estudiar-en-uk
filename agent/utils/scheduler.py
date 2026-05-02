# agent/utils/scheduler.py — Recordatorios automáticos para José
import asyncio
import logging
from typing import Callable, Coroutine, Any

logger = logging.getLogger("agentkit.scheduler")

# Almacena las tareas activas: telefono → {"1h": Task, "22h": Task}
_tareas: dict[str, dict[str, asyncio.Task]] = {}

DELAY_1H = int(3600)    # 1 hora en segundos
DELAY_22H = int(79200)  # 22 horas en segundos


async def _ejecutar_con_delay(
    telefono: str,
    tipo: str,
    delay: int,
    callback: Callable[..., Coroutine[Any, Any, None]],
):
    """Espera el delay y ejecuta el callback si la tarea no fue cancelada."""
    try:
        logger.info(f"Recordatorio '{tipo}' programado para {telefono} en {delay}s")
        await asyncio.sleep(delay)
        logger.info(f"Ejecutando recordatorio '{tipo}' para {telefono}")
        await callback(telefono, tipo)
    except asyncio.CancelledError:
        logger.info(f"Recordatorio '{tipo}' cancelado para {telefono}")
    except Exception as e:
        logger.error(f"Error en recordatorio '{tipo}' para {telefono}: {e}")
    finally:
        # Limpiar referencia
        if telefono in _tareas and tipo in _tareas[telefono]:
            del _tareas[telefono][tipo]


def programar_recordatorios(
    telefono: str,
    callback: Callable[..., Coroutine[Any, Any, None]],
    delay_1h: int = DELAY_1H,
    delay_22h: int = DELAY_22H,
):
    """
    Programa los dos recordatorios automáticos de José para un teléfono.

    callback recibe: (telefono: str, tipo: str) donde tipo es "1h" o "22h"

    Cancela recordatorios previos del mismo teléfono si existen.
    """
    cancelar_recordatorios(telefono)

    if telefono not in _tareas:
        _tareas[telefono] = {}

    _tareas[telefono]["1h"] = asyncio.create_task(
        _ejecutar_con_delay(telefono, "1h", delay_1h, callback)
    )
    _tareas[telefono]["22h"] = asyncio.create_task(
        _ejecutar_con_delay(telefono, "22h", delay_22h, callback)
    )
    logger.info(f"Recordatorios 1h y 22h programados para {telefono}")


def cancelar_recordatorio(telefono: str, tipo: str):
    """Cancela un recordatorio específico (tipo = '1h' o '22h')."""
    tarea = _tareas.get(telefono, {}).get(tipo)
    if tarea and not tarea.done():
        tarea.cancel()
        logger.info(f"Recordatorio '{tipo}' cancelado para {telefono}")


def cancelar_recordatorios(telefono: str):
    """Cancela TODOS los recordatorios de un teléfono."""
    for tipo, tarea in list(_tareas.get(telefono, {}).items()):
        if not tarea.done():
            tarea.cancel()
    if telefono in _tareas:
        del _tareas[telefono]


def tiene_recordatorios_activos(telefono: str) -> bool:
    """Retorna True si hay recordatorios activos para ese teléfono."""
    return any(
        not t.done()
        for t in _tareas.get(telefono, {}).values()
    )
