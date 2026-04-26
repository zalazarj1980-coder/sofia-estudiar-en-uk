# agent/providers/ghl.py — Adaptador para GoHighLevel (GHL)
# Generado por AgentKit

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")

GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-04-15"


class ProveedorGHL(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando GoHighLevel API v2."""

    def __init__(self):
        self.api_key = os.getenv("GHL_API_KEY")
        self.location_id = os.getenv("GHL_LOCATION_ID")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Version": GHL_API_VERSION,
        }

    def _extraer_texto(self, valor) -> str:
        """
        GHL puede enviar el campo 'body' como string o como dict
        (ej: {'type': 19, 'body': 'Hola'}).
        Esta función extrae siempre el texto limpio.
        """
        if isinstance(valor, dict):
            return str(valor.get("body") or valor.get("text") or "")
        if isinstance(valor, str):
            return valor
        return ""

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """
        Parsea el payload del webhook de GHL.
        Soporta el formato de workflow "Customer Reply" con Custom Data.
        """
        try:
            body = await request.json()
        except Exception:
            logger.warning("Webhook GHL sin cuerpo JSON válido")
            return []

        logger.info(f"Webhook GHL recibido: {body}")
        mensajes = []

        tipo = str(body.get("type") or body.get("messageType") or "")

        # Formato principal: workflow GHL "Customer Reply" con Custom Data plano
        # { "type": "CustomerReply", "phone": "...", "body": "..." | {...}, "direction": "inbound" }
        if tipo == "CustomerReply":
            texto = self._extraer_texto(
                body.get("body") or body.get("message") or body.get("text", "")
            )
            telefono = (
                body.get("phone")
                or body.get("contact.phone")
                or body.get("contactPhone", "")
            )
            mensaje_id = str(body.get("messageId") or body.get("id", ""))
            direccion = str(body.get("direction", "inbound"))

            if texto and telefono and direccion == "inbound":
                mensajes.append(MensajeEntrante(
                    telefono=self._normalizar_telefono(str(telefono)),
                    texto=texto,
                    mensaje_id=mensaje_id,
                    es_propio=False,
                ))
            return mensajes

        # Formato anidado: { "contact": {...}, "message": {...} }
        if "contact" in body and "message" in body:
            mensaje_obj = body.get("message", {})
            contacto_obj = body.get("contact", {})
            texto = self._extraer_texto(
                mensaje_obj.get("body") or mensaje_obj.get("text", "")
            )
            telefono = contacto_obj.get("phone") or body.get("phone", "")
            mensaje_id = str(mensaje_obj.get("id") or body.get("messageId", ""))
            direccion = str(mensaje_obj.get("direction", "inbound"))

            if texto and telefono and direccion == "inbound":
                mensajes.append(MensajeEntrante(
                    telefono=self._normalizar_telefono(str(telefono)),
                    texto=texto,
                    mensaje_id=mensaje_id,
                    es_propio=False,
                ))
            return mensajes

        # Formato plano genérico
        texto = self._extraer_texto(
            body.get("body") or body.get("message") or body.get("text", "")
        )
        telefono = body.get("phone") or body.get("from") or body.get("contactPhone", "")
        mensaje_id = str(body.get("messageId") or body.get("id", ""))
        direccion = str(body.get("direction", "inbound"))

        if texto and telefono and direccion == "inbound":
            mensajes.append(MensajeEntrante(
                telefono=self._normalizar_telefono(str(telefono)),
                texto=texto,
                mensaje_id=mensaje_id,
                es_propio=False,
            ))

        if not mensajes:
            logger.debug(f"Webhook GHL sin mensajes procesables. Keys: {list(body.keys())}")

        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """
        Envía un mensaje de WhatsApp a través de GHL.
        Busca el contacto, obtiene su conversación y envía el mensaje.
        """
        if not self.api_key or not self.location_id:
            logger.warning("GHL_API_KEY o GHL_LOCATION_ID no configurados")
            return False

        contact_id = await self._obtener_o_crear_contacto(telefono)
        if not contact_id:
            logger.error(f"No se pudo obtener contacto para {telefono}")
            return False

        conversation_id = await self._obtener_o_crear_conversacion(contact_id)
        if not conversation_id:
            logger.error(f"No se pudo obtener conversación para contacto {contact_id}")
            return False

        return await self._enviar_a_conversacion(conversation_id, contact_id, mensaje)

    async def _obtener_o_crear_contacto(self, telefono: str) -> str | None:
        """Busca un contacto por teléfono en GHL. Si no existe, lo crea."""
        telefono_normalizado = self._normalizar_telefono(telefono)

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{GHL_API_BASE}/contacts/search",
                headers=self._headers(),
                params={"locationId": self.location_id, "query": telefono_normalizado},
            )
            if r.status_code == 200:
                contactos = r.json().get("contacts", [])
                if contactos:
                    return contactos[0].get("id")

            r = await client.post(
                f"{GHL_API_BASE}/contacts/",
                headers=self._headers(),
                json={
                    "locationId": self.location_id,
                    "phone": telefono_normalizado,
                    "tags": ["sofia-agentkit"],
                },
            )
            if r.status_code in (200, 201):
                return r.json().get("contact", {}).get("id")

            logger.error(f"Error creando contacto GHL: {r.status_code} — {r.text}")
            return None

    async def _obtener_o_crear_conversacion(self, contact_id: str) -> str | None:
        """Obtiene o crea la conversación de WhatsApp para un contacto en GHL."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{GHL_API_BASE}/conversations/search",
                headers=self._headers(),
                params={"locationId": self.location_id, "contactId": contact_id},
            )
            if r.status_code == 200:
                conversaciones = r.json().get("conversations", [])
                if conversaciones:
                    return conversaciones[0].get("id")

            r = await client.post(
                f"{GHL_API_BASE}/conversations/",
                headers=self._headers(),
                json={
                    "locationId": self.location_id,
                    "contactId": contact_id,
                },
            )
            if r.status_code in (200, 201):
                data = r.json()
                # GHL puede devolver la conversación en distintos campos
                return (
                    data.get("id")
                    or data.get("conversation", {}).get("id")
                )

            logger.error(f"Error creando conversación GHL: {r.status_code} — {r.text}")
            return None

    async def _enviar_a_conversacion(self, conversation_id: str, contact_id: str, mensaje: str) -> bool:
        """Envía el mensaje a una conversación de GHL incluyendo el contactId requerido."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{GHL_API_BASE}/conversations/messages",
                headers=self._headers(),
                json={
                    "type": "WhatsApp",
                    "conversationId": conversation_id,
                    "contactId": contact_id,
                    "message": mensaje,
                },
            )
            if r.status_code not in (200, 201):
                logger.error(f"Error enviando mensaje GHL: {r.status_code} — {r.text}")
                return False
            logger.info(f"Mensaje enviado via GHL a conversación {conversation_id}")
            return True

    def _normalizar_telefono(self, telefono: str) -> str:
        """Asegura formato E.164 con código de país UK por defecto."""
        telefono = telefono.strip().replace(" ", "").replace("-", "")
        if not telefono.startswith("+"):
            if telefono.startswith("0"):
                telefono = "+44" + telefono[1:]
            elif telefono.startswith("44"):
                telefono = "+" + telefono
            else:
                telefono = "+44" + telefono
        return telefono
