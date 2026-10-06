from pathlib import Path

import yaml


PROMPTS_PATH = Path(__file__).parents[1] / "config" / "prompts.yaml"


def _system_prompt() -> str:
    config = yaml.safe_load(PROMPTS_PATH.read_text(encoding="utf-8"))
    return config["system_prompt"]


def test_first_english_message_asks_language_before_prequalification():
    prompt = _system_prompt()

    preference_question = "Would you prefer to continue in English or Spanish?"
    first_prequalification = "¿Tienes Pre-Settled Status, Settled Status o pasaporte británico?"

    assert "detecta el idioma" in prompt
    assert "atendemos principalmente en español" in prompt
    assert preference_question in prompt
    assert prompt.index(preference_question) < prompt.index(first_prequalification)


def test_spanish_first_message_keeps_the_existing_spanish_flow():
    prompt = _system_prompt()

    assert "Si el primer mensaje está en español, responde en español" in prompt
    assert "continúa directamente con la precalificación" in prompt


def test_selected_language_is_used_for_the_rest_of_the_conversation():
    prompt = _system_prompt()

    assert "mantén ese idioma durante el resto de la conversación" in prompt
    assert "No empieces la precalificación hasta que la persona elija el idioma" in prompt
