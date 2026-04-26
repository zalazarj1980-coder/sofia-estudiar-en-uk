# agent/providers/ghl.py — Adaptador para GoHighLevel (GHL)
# Generado por AgentKit

"""
Integración con GoHighLevel usando su API v2.
GHL envía webhooks con los mensajes entrantes de WhatsApp.
Nosotros respondemos usando la API de Conversations de GHL.

Flujo:
  1. GHL recibe un mensaje de WhatsApp del cliente
  2. GHL dispara un webhook POST a nuestro /webhook con los datos del mensaje
  3. Nosotros procesamos el mensaje y generamos respuesta con Claude
  4. Enviamos la respuesta de vuelta a GHL via su API de mensajes
  5. GHL lo envía al cliente por WhatsApp
"""

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

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """
        Parsea el payload del webhook de GHL.
        GHL envía distintos formatos según el tipo de evento.
        Soportamos: InboundMessage y el formato de workflow webhook.
        """
        try:
            body = await request.json()
        except Exception:
            logger.warning("Webhook GHL sin cuerpo JSON válido")
            return []

        logger.debug(f"Webhook GHL recibido: {body}")
        mensajes = []

        # Formato 1: webhook de tipo "InboundMessage" directo de GHL
        tipo = body.get("type") or body.get("messageType") or ""
        if tipo in ("InboundMessage", "WhatsApp"):
            texto = body.get("body") or body.get("message") or body.get("text", "")
            telefono = (
                body.get("phone")
                or body.get("from")
                or body.get("contactPhone", "")
            )
            mensaje_id = body.get("messageId") or body.get("id", "")
            direccion = body.get("direction", "inbound")

            if texto and telefono and direccion == "inbound":
                mensajes.append(MensajeEntrante(
                    telefono=self._normalizar_telefono(telefono),
                    texto=texto,
                    mensaje_id=mensaje_id,
                    es_propio=False,
                ))
            return mensajes

        # Formato 2: webhook de conversación/contacto con campo "messages"
        if "messages" in body:
            for msg in body.get("messages", []):
                if msg.get("direction") != "inbound":
                    continue
                tipo_msg = msg.get("messageType", "").lower()
                if tipo_msg not in ("whatsapp", "sms", ""):
                    continue
                texto = msg.get("body", "")
                telefono = msg.get("phone") or body.get("phone", "")
                mensaje_id = msg.get("id", "")
                if texto and telefono:
                    mensajes.append(MensajeEntrante(
                        telefono=self._normalizar_telefono(telefono),
                        texto=texto,
                        mensaje_id=mensaje_id,
                        es_propio=False,
                    ))
            return mensajes

        # Formato 3: payload plano con campos "body" y "phone"
        texto = body.get("body") or body.get("message") or body.get("text", "")
        telefono = body.get("phone") or body.get("from") or body.get("contactPhone", "")
        mensaje_id = body.get("messageId") or body.get("id", "")
        direccion = body.get("direction", "inbound")

        if texto and telefono and direccion == "inbound":
            mensajes.append(MensajeEntrante(
                telefono=self._normalizar_telefono(telefono),
                texto=texto,
                mensaje_id=mensaje_id,
                es_propio=False,
            ))

        if not mensajes:
            logger.debug(f"Webhook GHL sin mensajes procesables: {list(body.keys())}")

        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """
        Envía un mensaje de WhatsApp a través de GHL.
        Primero busca o crea el contacto, luego envía el mensaje a su conversación.
        """
        if not self.api_key or not self.location_id:
            logger.warning("GHL_API_KEY o GHL_LOCATION_ID no configurados — mensaje no enviado")
            return False

        contact_id = await self._obtener_o_crear_contacto(telefono)
        if not contact_id:
            logger.error(f"No se pudo obtener contacto para {telefono}")
            return False

        conversation_id = await self._obtener_o_crear_conversacion(contact_id)
        if not conversation_id:
            logger.error(f"No se pudo obtener conversación para contacto {contact_id}")
            return False

        return await self._enviar_a_conversacion(conversation_id, mensaje)

    async def _obtener_o_crear_contacto(self, telefono: str) -> str | None:
        """Busca un contacto por teléfono en GHL. Si no existe, lo crea."""
        telefono_normalizado = self._normalizar_telefono(telefono)

        async with httpx.AsyncClient() as client:
            # Buscar contacto existente
            r = await client.get(
                f"{GHL_API_BASE}/contacts/search",
                headers=self._headers(),
                params={"locationId": self.location_id, "query": telefono_normalizado},
            )
            if r.status_code == 200:
                data = r.json()
                contactos = data.get("contacts", [])
                if contactos:
                    return contactos[0].get("id")

            # Crear contacto si no existe
            r = await client.post(
                f"{GHL_API_BASE}/contacts/",
                headers=self._headers(),
                json={
                    "locationId": self.location_id,
                    "phone": telefono_normalizado,
                    "tags": ["agentkit", "sofia-whatsapp"],
                },
            )
            if r.status_code in (200, 201):
                return r.json().get("contact", {}).get("id")

            logger.error(f"Error creando contacto GHL: {r.status_code} — {r.text}")
            return None

    async def _obtener_o_crear_conversacion(self, contact_id: str) -> str | None:
        """Obtiene o crea la conversación de WhatsApp para un contacto en GHL."""
        async with httpx.AsyncClient() as client:
            # Buscar conversación existente
            r = await client.get(
                f"{GHL_API_BASE}/conversations/search",
                headers=self._headers(),
                params={"locationId": self.location_id, "contactId": contact_id},
            )
            if r.status_code == 200:
                data = r.json()
                conversaciones = data.get("conversations", [])
                if conversaciones:
                    return conversaciones[0].get("id")

            # Crear conversación nueva
            r = await client.post(
                f"{GHL_API_BASE}/conversations/",
                headers=self._headers(),
                json={
                    "locationId": self.location_id,
                    "contactId": contact_id,
                },
            )
            if r.status_code in (200, 201):
                return r.json().get("conversation", {}).get("id")

            logger.error(f"Error creando conversación GHL: {r.status_code} — {r.text}")
            return None

    async def _enviar_a_conversacion(self, conversation_id: str, mensaje: str) -> bool:
        """Envía el mensaje a una conversación de GHL."""
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{GHL_API_BASE}/conversations/messages",
                headers=self._headers(),
                json={
                    "type": "WhatsApp",
                    "conversationId": conversation_id,
                    "message": mensaje,
                },
            )
            if r.status_code not in (200, 201):
                logger.error(f"Error enviando mensaje GHL: {r.status_code} — {r.text}")
                return False
            return True

    def _normalizar_telefono(self, telefono: str) -> str:
        """Asegura que el teléfono tenga formato E.164 con código de país UK."""
        telefono = telefono.strip().replace(" ", "").replace("-", "")
        if not telefono.startswith("+"):
            if telefono.startswith("0"):
                telefono = "+44" + telefono[1:]
            elif telefono.startswith("44"):
                telefono = "+" + telefono
            else:
                telefono = "+44" + telefono
        return telefono
