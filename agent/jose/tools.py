# agent/josé/tools.py — Herramientas específicas para el agente José
import re
import logging
from html.parser import HTMLParser

logger = logging.getLogger("agentkit.jose")


class _StripHTML(HTMLParser):
    """Convierte HTML a texto plano."""
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str):
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(p.strip() for p in self._parts if p.strip())


def _html_a_texto(html: str) -> str:
    """Convierte HTML del email a texto plano legible."""
    if not html:
        return ""
    parser = _StripHTML()
    try:
        parser.feed(html)
        return parser.get_text()
    except Exception:
        # Si falla el parsing, eliminamos tags básicos con regex
        texto = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", texto).strip()


def parsear_email_para_jose(email: dict) -> dict:
    """
    Extrae datos estructurados del email de oferta enviado por el asesor.

    Patrones buscados:
    - Nombres de cursos: CertHE, BSc, BA, HND, Foundation, Digital Marketing, Business...
    - Universidades: ARU, Bath Spa, UWTSD, Arden, Middlesex, London Met...
    - Costo: £X,XXX o £X.XXX
    - Duración: X años / X year
    - Links: URLs completas (http/https)

    Retorna:
        {
            "asunto": str,
            "cursos": [{"nombre": str, "universidad": str, "duracion": str, "link": str}],
            "costo": str,
            "fecha": str,
            "resumen_texto": str,
        }
    """
    if not email:
        return {}

    asunto = email.get("asunto", "")
    cuerpo_html = email.get("cuerpo", "")
    fecha = email.get("fecha", "")

    # Convertir HTML a texto para búsqueda de patrones
    texto = _html_a_texto(cuerpo_html)

    resultado = {
        "asunto": asunto,
        "cursos": [],
        "costo": "",
        "fecha": fecha,
        "resumen_texto": texto[:800] if texto else "",
    }

    # Extraer costo (£X,XXX o £X.XXX)
    costo_match = re.search(r"£[\d,\.]+", texto)
    if costo_match:
        resultado["costo"] = costo_match.group(0)

    # Extraer URLs de los cursos
    links = re.findall(r"https?://[^\s<>\"']+", cuerpo_html or texto)
    # Filtrar solo links de universidades (excluir logos, imágenes, etc.)
    links_cursos = [
        l for l in links
        if any(kw in l.lower() for kw in ["course", "courses", "undergraduate", "ug-", "bsc", "ba-", "degree"])
    ]

    # Detectar nombres de universidades mencionadas
    universidades_conocidas = [
        ("ARU London", "ARU"),
        ("Bath Spa University", "Bath Spa"),
        ("Arden University", "Arden"),
        ("Middlesex University", "Middlesex"),
        ("University of Wales Trinity Saint David", "UWTSD"),
        ("London Metropolitan University", "London Met"),
        ("University of Sunderland London", "Sunderland London"),
        ("Newcastle College Group", "NCG"),
        ("London Professional College", "LPC"),
        ("William College", "William College"),
    ]
    universidades_en_email = [
        nombre_completo for nombre_completo, abrev in universidades_conocidas
        if abrev.lower() in texto.lower() or nombre_completo.lower() in texto.lower()
    ]

    # Detectar tipos de curso mencionados
    tipos_curso = [
        "Certificate of Higher Education", "CertHE",
        "Foundation Year", "Foundation Degree", "FdA",
        "BSc (Hons)", "BSc", "BA (Hons)", "BA",
        "HND", "HNC", "Top-up",
        "Digital Marketing", "Business and Management",
        "Human Resource Management",
    ]
    cursos_en_email = [t for t in tipos_curso if t.lower() in texto.lower()]

    # Detectar duración
    duracion_match = re.search(r"(\d+)\s*años?|(\d+)\s*years?", texto, re.IGNORECASE)
    duracion = ""
    if duracion_match:
        num = duracion_match.group(1) or duracion_match.group(2)
        duracion = f"{num} año(s)"

    # Construir lista de cursos detectados
    for i, universidad in enumerate(universidades_en_email):
        nombre_curso = cursos_en_email[i] if i < len(cursos_en_email) else asunto
        link = links_cursos[i] if i < len(links_cursos) else ""
        resultado["cursos"].append({
            "nombre": nombre_curso,
            "universidad": universidad,
            "duracion": duracion,
            "link": link,
        })

    # Si no detectó cursos pero hay texto, agregar uno genérico con el asunto
    if not resultado["cursos"] and asunto:
        resultado["cursos"].append({
            "nombre": asunto,
            "universidad": universidades_en_email[0] if universidades_en_email else "",
            "duracion": duracion,
            "link": links_cursos[0] if links_cursos else "",
        })

    logger.info(
        f"Email parseado — {len(resultado['cursos'])} curso(s), "
        f"costo={resultado['costo']}, links={len(links_cursos)}"
    )
    return resultado


def formatear_oferta_para_contexto(oferta: dict) -> str:
    """
    Convierte los datos parseados del email en un bloque de texto
    que se inyecta en el system prompt de José para que conozca la oferta.
    """
    if not oferta:
        return "No hay datos de la oferta disponibles."

    lineas = []

    if oferta.get("asunto"):
        lineas.append(f"Asunto del email: {oferta['asunto']}")

    if oferta.get("costo"):
        lineas.append(f"Costo del curso: {oferta['costo']} (financiable con Student Finance)")

    if oferta.get("cursos"):
        lineas.append(f"\nOpciones enviadas al estudiante ({len(oferta['cursos'])} curso(s)):")
        for i, c in enumerate(oferta["cursos"], 1):
            lineas.append(f"  {i}. {c['nombre']}")
            if c.get("universidad"):
                lineas.append(f"     Universidad: {c['universidad']}")
            if c.get("duracion"):
                lineas.append(f"     Duración: {c['duracion']}")
            if c.get("link"):
                lineas.append(f"     Link: {c['link']}")

    if oferta.get("fecha"):
        lineas.append(f"\nFecha de envío: {oferta['fecha']}")

    if oferta.get("resumen_texto"):
        lineas.append(f"\nContenido del email:\n{oferta['resumen_texto'][:500]}")

    return "\n".join(lineas) if lineas else "Oferta enviada al estudiante. Detalles no disponibles."
