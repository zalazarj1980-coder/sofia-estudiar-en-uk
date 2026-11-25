"""Sofía only requests a slot; the verified calendar workflow owns confirmation."""
import json
import logging
import re
import unicodedata
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

BOOKING_URL = "https://estudiarenuk.co.uk/agendamiento"
LONDON = ZoneInfo("Europe/London")
logger = logging.getLogger("agentkit")
_MARKER = re.compile(r"\[\s*BOOKING\b", re.IGNORECASE)
_PAUSE = re.compile(r"\[\s*PAUSA\b[^\]\n]*\]?", re.IGNORECASE)
_PAUSE_BOOKING = re.compile(r"\[\s*PAUSA\s*:\s*(?:booking|cita|reserva|appointment)", re.IGNORECASE)
_BOOKING_WORD = re.compile(r"\b(?:cita|reserva|appointment|booking|consultation)s?\b", re.IGNORECASE)


def _plain(text):
    return "".join(char for char in unicodedata.normalize("NFKD", text.lower())
                   if not unicodedata.combining(char))


def _booking_claim(text):
    # Detect contradictory legacy/hallucinated confirmations with or without a control block.
    plain = _plain(text)
    claim = (r"\b(?:confirm\w*|agendad\w*|reservad\w*|reserv[eo]|agend[eo]|qued[ao]|"
             r"tienes\s+(?:tu|una)|esta\s+(?:fijad[ao]|programad[ao])|booked|scheduled|"
             r"all set|secured|set for|see you|te esperamos|te espero)\b")
    return bool((_BOOKING_WORD.search(plain) and re.search(claim, plain))
                or re.search(r"\b(?:te (?:agende|reserve|confirme)|i(?: have|'ve) booked you)\b", plain))


def _in_english(text):
    return bool(re.search(r"\b(?:hello|thanks|thank you|please|appointment|booking|"
                          r"book|schedule|would like|you can|sorry)\b", text, re.IGNORECASE)
                and not re.search(r"\b(?:hola|gracias|por favor|cita|reserva|puedes)\b",
                                  text, re.IGNORECASE))


def _slot(response):
    marker = _MARKER.search(response)
    if not marker or len(_MARKER.findall(response)) != 1:
        return None
    prefix = re.match(r":\s*", response[marker.end():].lstrip())
    if not prefix:
        return None
    block = response[marker.end():].lstrip()[prefix.end():]
    try:
        data, end = json.JSONDecoder().raw_decode(block)
        if not re.match(r"\s*\]", block[end:]) or not isinstance(data, dict):
            return None
        name, day, hour = (data[k] for k in ("nombre", "fecha", "hora"))
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            return None
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            return None
        if not isinstance(hour, str) or not re.fullmatch(r"\d{2}:\d{2}", hour):
            return None
        naive = datetime.combine(date.fromisoformat(day), time.fromisoformat(hour))
        requested = naive.replace(tzinfo=LONDON)
        # Refuse ambiguous/repeated or nonexistent times on DST transition days.
        if requested.utcoffset() != naive.replace(tzinfo=LONDON, fold=1).utcoffset():
            return None
        return requested if requested > datetime.now(timezone.utc) else None
    except (ValueError, KeyError, TypeError, OverflowError):
        return None


async def process_booking_response(response, phone, provider, user_text=""):
    """Return (safe reply, processed booking). No owner notification or booking pause."""
    marker = _MARKER.search(response)
    if not marker and not _PAUSE_BOOKING.search(response) and not _booking_claim(response):
        return response, False

    english = _in_english(user_text) or (_in_english(response[:marker.start()] if marker else response)
                                       and not re.search(r"\b(?:quiero|puedo|me viene|mañana|necesito)\b",
                                                         user_text, re.IGNORECASE))
    requested = _slot(response)
    outcome = "unavailable"
    if requested is not None:
        submit = getattr(provider, "solicitar_cita_sofia", None)
        if submit is not None:
            try:
                outcome = await submit(phone, requested)
            except Exception:
                logger.warning("Sofia booking request state unknown")
                outcome = "uncertain"

    if outcome in ("queued", "pending"):
        if english:
            return ("Your request for " + requested.strftime("%d/%m/%Y at %H:%M") +
                    " (London time) is pending. This does not reserve the slot. "
                    "Please wait for a separate calendar confirmation."), True
        return ("Tu solicitud para el " + requested.strftime("%d/%m/%Y a las %H:%M") +
                " (hora de Londres) está pendiente. Esto no reserva el horario. "
                "Espera la confirmación del calendario por separado."), True

    if outcome in ("other_pending", "uncertain") or (not marker and _booking_claim(response)):
        if english:
            return ("I can't confirm this appointment. A request may already be in progress. "
                    "Please wait for a separate confirmation or ask our team to check before booking again."), True
        return ("No puedo confirmar esta cita. Puede haber una solicitud en curso. "
                "Espera una confirmación por separado o pide al equipo que la revise antes de reservar de nuevo."), True

    if english:
        return ("I couldn't confirm a booking here. You can check available times "
                f"and book your free consultation at {BOOKING_URL}."), True
    return ("No pude confirmar una reserva por aquí. Puedes consultar los horarios disponibles "
            f"y reservar tu asesoría gratuita en {BOOKING_URL}."), True


def strip_untrusted_pause(response):
    """Only the separate GHL/operator pause webhook can pause the conversation."""
    return _PAUSE.sub("", response).strip()
