# tests/test_local_jose.py — Simulador de chat para el agente José
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.jose.brain import generar_respuesta_jose, mensaje_primer_contacto
from agent.jose.tools import parsear_email_para_jose
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, limpiar_historial

TELEFONO_TEST = "test-jose-local-001"

# Email de prueba simulado (equivalente al que envía el asesor desde GHL)
EMAIL_PRUEBA = {
    "asunto": "4 opciones para estudiar Business y Marketing en Londres 🎓",
    "cuerpo": """
        Hola,
        Quiero compartir contigo 4 opciones de estudio en Business y Marketing en Londres.

        1. Business and Human Resource Management – ARU London
        Duración: 3 años (o 4 con Foundation)
        https://london.aru.ac.uk/courses/bsc-hons-business-and-human-resource-management

        2. Digital Marketing and Management – ARU London
        Duración: 3 años
        https://london.aru.ac.uk/courses/bsc-hons-digital-marketing-and-management

        3. Business and Management – Bath Spa University (London)
        Duración: 3 años
        https://www.bathspa.ac.uk/courses/ug-business-and-management-london/

        4. BSc (Hons) Digital Marketing – Arden University
        Duración: 3 años
        https://arden.ac.uk/our-courses/undergraduate/marketing-degree/bsc-hons-digital-marketing

        Costo: Aproximadamente £9,250 (financiable con Student Finance England)

        Puedes acceder a Student Finance England (si cumples requisitos).

        Un saludo,
        Jose Zalazar
    """,
    "fecha": "2026-05-01",
    "remitente": "zalazarj1980@gmail.com",
}


async def main():
    await inicializar_db()

    # Parsear el email de prueba
    oferta = parsear_email_para_jose(EMAIL_PRUEBA)

    print()
    print("=" * 60)
    print("   AgentKit — Test Local: JOSÉ (Follow-up Post-Oferta)")
    print("=" * 60)
    print()
    print("  Simula la conversación de José con un estudiante.")
    print("  Comandos especiales:")
    print("    'limpiar'  — borra historial")
    print("    'oferta'   — muestra los datos del email parseado")
    print("    'salir'    — termina el test")
    print()

    if oferta.get("cursos"):
        print(f"  Email parseado: {len(oferta['cursos'])} curso(s) detectado(s)")
        for c in oferta["cursos"]:
            print(f"   • {c['nombre']} — {c['universidad']}")
    else:
        print("  ⚠ Email de prueba no parseó cursos (revisar parsear_email_para_jose)")

    print()
    print("-" * 60)
    print()

    # José envía el primer mensaje automáticamente
    primer = mensaje_primer_contacto()
    print(f"José: {primer}")
    print()
    await guardar_mensaje(TELEFONO_TEST, "assistant", primer)

    while True:
        try:
            entrada = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nTest finalizado.")
            break

        if not entrada:
            continue

        if entrada.lower() == "salir":
            print("\nTest finalizado.")
            break

        if entrada.lower() == "limpiar":
            await limpiar_historial(TELEFONO_TEST)
            print("[Historial borrado — José enviará primer mensaje de nuevo]\n")
            primer = mensaje_primer_contacto()
            print(f"José: {primer}\n")
            await guardar_mensaje(TELEFONO_TEST, "assistant", primer)
            continue

        if entrada.lower() == "oferta":
            print("\n--- Datos del email parseado ---")
            print(f"Asunto: {oferta.get('asunto', 'N/D')}")
            print(f"Costo: {oferta.get('costo', 'N/D')}")
            print(f"Cursos: {len(oferta.get('cursos', []))}")
            for c in oferta.get("cursos", []):
                print(f"  • {c['nombre']} | {c['universidad']} | {c['duracion']}")
                if c.get("link"):
                    print(f"    {c['link']}")
            print("--------------------------------\n")
            continue

        historial = await obtener_historial(TELEFONO_TEST)
        print("\nJosé: ", end="", flush=True)
        respuesta = await generar_respuesta_jose(entrada, historial, oferta=oferta)
        print(respuesta)
        print()

        await guardar_mensaje(TELEFONO_TEST, "user", entrada)
        await guardar_mensaje(TELEFONO_TEST, "assistant", respuesta)


if __name__ == "__main__":
    asyncio.run(main())
