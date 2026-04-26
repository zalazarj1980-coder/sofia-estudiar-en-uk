# tests/test_local.py — Simulador de chat en terminal
# Generado por AgentKit — Estudiar en UK

"""
Prueba tu agente Sofía sin necesitar WhatsApp.
Simula una conversación en la terminal.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, limpiar_historial

TELEFONO_TEST = "test-local-001"


async def main():
    await inicializar_db()

    print()
    print("=" * 60)
    print("   Sofía — Agente de Estudiar en UK (Test Local)")
    print("=" * 60)
    print()
    print("  Escribe mensajes como si fueras un cliente hispanohablante")
    print("  que quiere saber más sobre estudiar en una universidad en UK.")
    print()
    print("  Comandos especiales:")
    print("    'limpiar'  — borra el historial y empieza de nuevo")
    print("    'salir'    — termina el test")
    print()
    print("-" * 60)
    print()

    while True:
        try:
            mensaje = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nTest finalizado.")
            break

        if not mensaje:
            continue

        if mensaje.lower() == "salir":
            print("\nTest finalizado. ¡Hasta pronto!")
            break

        if mensaje.lower() == "limpiar":
            await limpiar_historial(TELEFONO_TEST)
            print("[Historial borrado — nueva conversación]\n")
            continue

        historial = await obtener_historial(TELEFONO_TEST)

        print("\nSofía: ", end="", flush=True)
        respuesta = await generar_respuesta(mensaje, historial)
        print(respuesta)
        print()

        await guardar_mensaje(TELEFONO_TEST, "user", mensaje)
        await guardar_mensaje(TELEFONO_TEST, "assistant", respuesta)


if __name__ == "__main__":
    asyncio.run(main())
