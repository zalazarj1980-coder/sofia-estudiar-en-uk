from pathlib import Path

import yaml


PROMPTS_PATH = Path(__file__).parents[1] / "config" / "prompts.yaml"


def _system_prompt() -> str:
    config = yaml.safe_load(PROMPTS_PATH.read_text(encoding="utf-8"))
    return config["system_prompt"]


def test_prequalification_contains_only_status_and_location_questions():
    prompt = _system_prompt()

    assert "SOLO estas DOS preguntas de precalificación" in prompt
    assert "¿Tienes Pre-Settled Status, Settled Status o pasaporte británico?" in prompt
    assert "¿En qué ciudad o zona vives actualmente?" in prompt

    retired_questions = (
        "¿Cuál es tu nivel de inglés?",
        "¿Qué te gustaría estudiar?",
        "¿Cuándo te gustaría empezar?",
        "¿Estás trabajando actualmente?",
        "¿Qué estudios o titulaciones tienes hasta ahora?",
        "¿Cuál es tu código postal?",
        "¿Recibes algún beneficio o Universal Credit?",
    )
    for question in retired_questions:
        assert question not in prompt


def test_location_qualification_supports_six_cities_and_online_option():
    prompt = _system_prompt()

    for city in ("Londres", "Birmingham", "Manchester", "Leeds", "Cardiff", "Swansea"):
        assert city in prompt

    assert "100 % online" in prompt
    assert "Si acepta la modalidad online, continúa como persona precalificada" in prompt
    assert "actualmente no tenemos cobertura presencial en su zona" in prompt


def test_prequalification_result_and_summary_use_status_and_location():
    prompt = _system_prompt()

    assert "Cuando tengas las respuestas de estatus migratorio y ubicación" in prompt
    assert "• Estatus migratorio: [estatus que mencionó]" in prompt
    assert "• Ubicación o modalidad: [ciudad o 100 % online]" in prompt
