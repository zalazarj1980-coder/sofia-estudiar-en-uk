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
        # Cache: teléfono → contact_id (GHL lo envía en el webhook)
        self._contact_cache: dict[str, str] = {}

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
        """
        if isinstance(valor, dict):
            return str(valor.get("body") or valor.get("text") or "")
        if isinstance(valor, str):
            return valor.strip()
        return ""

    def _extraer_telefono(self, valor) -> str:
        """Extrae el teléfono y lo normaliza."""
        if not valor:
            return ""
        return self._normalizar_telefono(str(valor))

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """
        Parsea el payload del webhook de GHL.
        GHL envía standard data + customData al mismo nivel.
        El payload incluye contact_id, phone, y message.body.
        """
        try:
            body = await request.json()
        except Exception:
            logger.warning("Webhook GHL sin cuerpo JSON válido")
            return []

        logger.info(f"Webhook GHL recibido — keys: {list(body.keys())}")

        # Extraer contact_id del payload estándar de GHL (siempre presente)
        contact_id = str(body.get("contact_id") or body.get("contactId") or "")

        # El mensaje viene en body["message"] = {"type": 19, "body": "texto"}
        mensaje_obj = body.get("message", {})
        if isinstance(mensaje_obj, dict):
            texto = self._extraer_texto(mensaje_obj.get("body") or mensaje_obj.get("text", ""))
        else:
            texto = self._extraer_texto(mensaje_obj)

        # El teléfono viene en body["phone"]
        telefono_raw = body.get("phone") or body.get("contactPhone") or ""
        telefono = self._extraer_telefono(telefono_raw)

        mensaje_id = str(body.get("messageId") or body.get("id") or "")

        # Cachear contact_id para usarlo al enviar (evita búsqueda via API)
        if contact_id and telefono:
            self._contact_cache[telefono] = contact_id
            logger.info(f"Contact ID cacheado: {telefono} → {contact_id}")

        if not texto or not telefono:
            logger.debug(f"Webhook GHL sin texto o teléfono: texto='{texto}' phone='{telefono_raw}'")
            return []

        logger.info(f"Mensaje de {telefono}: {texto}")
        return [MensajeEntrante(
            telefono=telefono,
            texto=texto,
            mensaje_id=mensaje_id,
            es_propio=False,
        )]

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """
        Envía un mensaje de WhatsApp a través de GHL.
        Usa el contact_id del cache (enviado por GHL en el webhook).
        Si no está en cache, lo busca via API.
        """
        if not self.api_key or not self.location_id:
            logger.warning("GHL_API_KEY o GHL_LOCATION_ID no configurados")
            return False

        # Usar contact_id del cache primero (más rápido y fiable)
        contact_id = self._contact_cache.get(telefono)

        if not contact_id:
            contact_id = await self._buscar_contacto(telefono)

        if not contact_id:
            logger.error(f"No se encontró contact_id para {telefono}")
            return False

        conversation_id = await self._obtener_o_crear_conversacion(contact_id)
        if not conversation_id:
            logger.error(f"No se pudo obtener conversación para {contact_id}")
            return False

        return await self._enviar_a_conversacion(conversation_id, contact_id, mensaje)

    async def _buscar_contacto(self, telefono: str) -> str | None:
        """Busca un contacto por teléfono. Maneja el error 400 de duplicado."""
        telefono_normalizado = self._normalizar_telefono(telefono)

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Intentar con el endpoint correcto de GHL
            r = await client.get(
                f"{GHL_API_BASE}/contacts/",
                headers=self._headers(),
                params={"locationId": self.location_id, "query": telefono_normalizado},
            )
            if r.status_code == 200:
                data = r.json()
                contactos = data.get("contacts", [])
                if contactos:
                    cid = contactos[0].get("id")
                    if cid:
                        self._contact_cache[telefono] = cid
                        return cid

            # Si falla la búsqueda, intentar crear (el 400 de duplicado nos da el ID)
            r = await client.post(
                f"{GHL_API_BASE}/contacts/",
                headers=self._headers(),
                json={
                    "locationId": self.location_id,
                    "phone": telefono_normalizado,
                },
            )
            if r.status_code in (200, 201):
                cid = r.json().get("contact", {}).get("id")
                if cid:
                    self._contact_cache[telefono] = cid
                return cid

            # GHL retorna 400 con el contactId cuando ya existe el contacto
            if r.status_code == 400:
                try:
                    error_data = r.json()
                    cid = error_data.get("meta", {}).get("contactId")
                    if cid:
                        logger.info(f"Contacto duplicado encontrado: {cid}")
                        self._contact_cache[telefono] = cid
                        return cid
                except Exception:
                    pass

            logger.error(f"No se pudo obtener contacto GHL para {telefono}: {r.status_code}")
            return None

    async def _obtener_o_crear_conversacion(self, contact_id: str) -> str | None:
        """Obtiene o crea la conversación de WhatsApp para un contacto."""
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
                data = r.json()
                return data.get("id") or data.get("conversation", {}).get("id")

            logger.error(f"Error obteniendo conversación GHL: {r.status_code} — {r.text}")
            return None

    async def _enviar_a_conversacion(self, conversation_id: str, contact_id: str, mensaje: str) -> bool:
        """Envía el mensaje a una conversación de GHL."""
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
            logger.info(f"Mensaje enviado correctamente via GHL")
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
