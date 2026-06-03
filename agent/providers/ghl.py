# agent/providers/ghl.py — Adaptador para GoHighLevel (GHL)
# Generado por AgentKit

import os
import json
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
        logger.debug(f"PAYLOAD COMPLETO JSON:\n{json.dumps(body, indent=2, ensure_ascii=False, default=str)}")

        # Extraer contact_id del payload estándar de GHL (siempre presente)
        contact_id = str(body.get("contact_id") or body.get("contactId") or "")

        # El mensaje viene en body["message"] = {"type": 19, "body": "texto"}
        mensaje_obj = body.get("message", {})
        if isinstance(mensaje_obj, dict):
            texto = self._extraer_texto(mensaje_obj.get("body") or mensaje_obj.get("text", ""))
        else:
            texto = self._extraer_texto(mensaje_obj)

        # Detectar imagen adjunta en el mensaje
        imagen_url = self._extraer_imagen_url(mensaje_obj, body)
        if imagen_url:
            logger.info(f"Imagen detectada en mensaje: {imagen_url[:80]}...")

        # El teléfono puede venir en varios lugares según el tipo de webhook
        contact = body.get("contact") if isinstance(body.get("contact"), dict) else {}
        customer = body.get("customer") if isinstance(body.get("customer"), dict) else {}
        custom_data = body.get("customData") if isinstance(body.get("customData"), dict) else {}

        # Buscar el teléfono en múltiples ubicaciones y con múltiples nombres de campo
        telefono_raw = (
            body.get("phone")
            or body.get("contactPhone")
            or body.get("contact_phone")
            or body.get("phoneNumber")
            or body.get("phone_number")
            or contact.get("phone")
            or contact.get("phoneNumber")
            or contact.get("phone_number")
            or customer.get("phone")
            or customer.get("phoneNumber")
            or custom_data.get("phone")
            or custom_data.get("phoneNumber")
            or ""
        )
        telefono = self._extraer_telefono(telefono_raw)

        mensaje_id = str(body.get("messageId") or body.get("id") or "")

        # Si no encontramos el teléfono en el payload pero tenemos contact_id,
        # buscarlo via la API de GHL (el payload real de GHL no siempre incluye phone)
        if not telefono and contact_id:
            logger.info(f"Teléfono no en payload — resolviendo via API para contact_id={contact_id}")
            telefono = await self._obtener_telefono_por_contact_id(contact_id)
            if telefono:
                logger.info(f"Teléfono resuelto via API: {telefono}")

        # Cachear contact_id para usarlo al enviar (evita búsqueda via API)
        if contact_id and telefono:
            self._contact_cache[telefono] = contact_id
            logger.info(f"Contact ID cacheado: {telefono} → {contact_id}")

        if not telefono or (not texto and not imagen_url):
            logger.debug(f"Webhook GHL sin contenido: texto='{texto}' imagen={imagen_url} phone='{telefono_raw}'")
            return []

        logger.info(f"Mensaje de {telefono}: {texto or '[imagen]'}")
        return [MensajeEntrante(
            telefono=telefono,
            texto=texto,
            mensaje_id=mensaje_id,
            es_propio=False,
            imagen_url=imagen_url,
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

    async def _obtener_telefono_por_contact_id(self, contact_id: str) -> str:
        """Consulta la API de GHL para obtener el teléfono de un contacto por su ID."""
        if not self.api_key or not contact_id:
            return ""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{GHL_API_BASE}/contacts/{contact_id}",
                    headers=self._headers(),
                )
                if r.status_code == 200:
                    data = r.json().get("contact", {})
                    phone_raw = data.get("phone") or data.get("phoneNumber") or ""
                    if phone_raw:
                        return self._normalizar_telefono(str(phone_raw))
                    logger.warning(f"Contacto {contact_id} no tiene teléfono en GHL")
                else:
                    logger.error(f"Error obteniendo contacto {contact_id}: {r.status_code} — {r.text[:200]}")
        except Exception as e:
            logger.error(f"Excepción resolviendo teléfono para {contact_id}: {e}")
        return ""

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

    async def actualizar_custom_field(self, telefono: str, nombre_campo: str, valor: str) -> bool:
        """
        Actualiza un custom field de un contacto en GHL.
        Usa el contact_id del cache si está disponible.

        IMPORTANTE: La API v2 de GHL espera el key SIN prefijo 'contact.'.
        Si llega con prefijo, lo strippea automáticamente. Si el PUT con `key` falla
        en actualizar (silent fail), intenta resolver el field ID y reintentar.
        """
        contact_id = self._contact_cache.get(telefono)

        if not contact_id:
            logger.warning(f"No hay contact_id en cache para {telefono} — no se puede actualizar custom field")
            return False

        if not self.api_key or not self.location_id:
            logger.warning("GHL_API_KEY o GHL_LOCATION_ID no configurados")
            return False

        # Strippear prefijo 'contact.' si viene (GHL API no lo acepta en body)
        key_limpia = nombre_campo
        if key_limpia.startswith("contact."):
            key_limpia = key_limpia[len("contact."):]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Intento 1: enviar como `key` (string slug)
                payload = {
                    "customFields": [
                        {
                            "key": key_limpia,
                            "field_value": valor,
                        }
                    ]
                }

                r = await client.put(
                    f"{GHL_API_BASE}/contacts/{contact_id}",
                    headers=self._headers(),
                    json=payload,
                )

                if r.status_code not in (200, 201, 204):
                    logger.error(f"Error actualizando custom field en GHL: {r.status_code} — {r.text}")
                    return False

                # Verificar si REALMENTE se actualizó (GHL puede retornar 200 sin actualizar)
                if await self._verificar_custom_field(contact_id, key_limpia, valor):
                    logger.info(f"Custom field '{key_limpia}' = '{valor}' verificado en GHL para {telefono}")
                    return True

                # Si no se actualizó con key como slug, intentar con field ID
                logger.warning(f"Custom field '{key_limpia}' no se actualizó con key slug — intentando con ID")
                field_id = await self._obtener_field_id(key_limpia)
                if not field_id:
                    logger.error(f"No se pudo obtener field ID para '{key_limpia}' — campo no actualizado")
                    return False

                payload_id = {
                    "customFields": [
                        {
                            "id": field_id,
                            "field_value": valor,
                        }
                    ]
                }
                r2 = await client.put(
                    f"{GHL_API_BASE}/contacts/{contact_id}",
                    headers=self._headers(),
                    json=payload_id,
                )
                if r2.status_code not in (200, 201, 204):
                    logger.error(f"Error con field ID: {r2.status_code} — {r2.text}")
                    return False

                if await self._verificar_custom_field(contact_id, key_limpia, valor):
                    logger.info(f"Custom field '{key_limpia}' actualizado vía field ID '{field_id}' para {telefono}")
                    return True

                logger.error(f"Custom field '{key_limpia}' NO se actualizó ni con key ni con ID")
                return False

        except Exception as e:
            logger.error(f"Excepción al actualizar custom field GHL: {e}")
            return False

    async def _verificar_custom_field(self, contact_id: str, key: str, valor_esperado: str) -> bool:
        """Lee el contacto y verifica si el custom field tiene el valor esperado."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{GHL_API_BASE}/contacts/{contact_id}",
                    headers=self._headers(),
                )
                if r.status_code != 200:
                    return False
                data = r.json().get("contact", {})
                for cf in data.get("customFields", []):
                    cf_key = cf.get("key", "")
                    cf_val = str(cf.get("field_value") or cf.get("value") or "")
                    if cf_key == key or cf_key == f"contact.{key}":
                        return cf_val == str(valor_esperado)
                return False
        except Exception as e:
            logger.debug(f"Error verificando custom field: {e}")
            return False

    async def _obtener_field_id(self, key: str) -> str | None:
        """Obtiene el ID de un custom field por su key/slug desde la location."""
        if not self.location_id:
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{GHL_API_BASE}/locations/{self.location_id}/customFields",
                    headers=self._headers(),
                )
                if r.status_code != 200:
                    logger.error(f"Error listando custom fields: {r.status_code} — {r.text}")
                    return None
                fields = r.json().get("customFields", [])
                for f in fields:
                    f_key = f.get("fieldKey", "") or f.get("key", "")
                    # GHL puede prefijar "contact." en la respuesta
                    if f_key == key or f_key == f"contact.{key}":
                        field_id = f.get("id")
                        logger.info(f"Field ID resuelto para '{key}': {field_id}")
                        return field_id
                logger.warning(f"No se encontró field ID para '{key}' en {len(fields)} campos disponibles")
                return None
        except Exception as e:
            logger.error(f"Excepción obteniendo field ID: {e}")
            return None

    async def agregar_tag(self, telefono: str, tag: str) -> bool:
        """
        Agrega un tag a un contacto en GHL.
        Usa el contact_id del cache; si no está, lo busca via API.
        """
        contact_id = self._contact_cache.get(telefono)
        if not contact_id:
            contact_id = await self._buscar_contacto(telefono)

        if not contact_id:
            logger.warning(f"No hay contact_id para {telefono} — no se puede agregar tag '{tag}'")
            return False

        if not self.api_key:
            logger.warning("GHL_API_KEY no configurado — no se puede agregar tag")
            return False

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{GHL_API_BASE}/contacts/{contact_id}/tags",
                    headers=self._headers(),
                    json={"tags": [tag]},
                )
                if r.status_code not in (200, 201):
                    logger.error(f"Error agregando tag GHL: {r.status_code} — {r.text}")
                    return False
                logger.info(f"Tag '{tag}' agregado en GHL al contacto {contact_id} ({telefono})")
                return True
        except Exception as e:
            logger.error(f"Excepción al agregar tag GHL: {e}")
            return False

    def _extraer_imagen_url(self, mensaje_obj: dict, body: dict) -> str | None:
        """
        Detecta si el mensaje contiene una imagen y retorna su URL.
        GHL puede enviar imágenes con distintos formatos según la versión.
        """
        if isinstance(mensaje_obj, dict):
            tipo = mensaje_obj.get("type")
            # Tipos de imagen de WhatsApp via GHL (numérico o string)
            es_imagen = tipo in (2, "image", "IMAGE") or str(tipo).lower() in ("image",)
            if es_imagen:
                url = (
                    mensaje_obj.get("url") or
                    mensaje_obj.get("mediaUrl") or
                    mensaje_obj.get("fileUrl")
                )
                if url:
                    return url
            # A veces el body mismo es la URL de la imagen
            cuerpo = mensaje_obj.get("body", "")
            if isinstance(cuerpo, str) and cuerpo.startswith("http"):
                ext = cuerpo.lower().split("?")[0]
                if any(ext.endswith(e) for e in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
                    return cuerpo

        # GHL también puede enviar attachments al nivel raíz
        attachments = body.get("attachments", [])
        if attachments:
            first = attachments[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("url") or first.get("mediaUrl")

        return None

    async def obtener_custom_field(self, contact_id: str, field_key: str) -> str:
        """
        Obtiene el valor de un custom field de un contacto en GHL.
        Retorna el valor como string, o cadena vacía si no existe.
        """
        if not self.api_key or not contact_id:
            logger.debug(f"obtener_custom_field: api_key o contact_id vacío")
            return ""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{GHL_API_BASE}/contacts/{contact_id}",
                    headers=self._headers(),
                )
                if r.status_code == 200:
                    data = r.json().get("contact", {})
                    custom_fields = data.get("customFields", [])
                    logger.info(f"GHL_DEBUG obtener_custom_field: buscando '{field_key}' en {len(custom_fields)} campos")
                    for cf in custom_fields:
                        cf_key = cf.get("key", "")
                        cf_val = cf.get("field_value") or cf.get("value") or ""
                        logger.info(f"GHL_DEBUG cf: key='{cf_key}' val='{cf_val}' raw={cf}")
                        if cf_key == field_key or cf_key == f"contact.{field_key}":
                            logger.info(f"GHL_DEBUG encontrado '{field_key}' = '{cf_val}'")
                            return str(cf_val)
                    logger.info(f"GHL_DEBUG '{field_key}' no encontrado")
                else:
                    logger.error(f"obtener_custom_field: status {r.status_code} — {r.text[:200]}")
        except Exception as e:
            logger.error(f"Error obteniendo custom field '{field_key}': {e}")
        return ""

    async def obtener_ultimo_email(self, contact_id: str) -> dict | None:
        """
        Obtiene el último email enviado a un contacto desde GHL.
        Retorna: {"asunto": "...", "cuerpo": "...", "fecha": "...", "remitente": "..."}
        """
        if not self.api_key or not self.location_id:
            logger.warning("GHL_API_KEY o GHL_LOCATION_ID no configurados")
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # GHL API para obtener timeline/actividad del contact
                # Busca emails enviados en los últimos días
                r = await client.get(
                    f"{GHL_API_BASE}/contacts/{contact_id}/emails",
                    headers=self._headers(),
                    params={"locationId": self.location_id, "limit": 5},  # Últimos 5 emails
                )

                if r.status_code == 200:
                    data = r.json()
                    emails = data.get("emails") or data.get("data") or []

                    if emails:
                        # Retornar el más reciente
                        ultimo = emails[0]
                        return {
                            "asunto": ultimo.get("subject") or ultimo.get("title") or "",
                            "cuerpo": ultimo.get("body") or ultimo.get("content") or "",
                            "fecha": ultimo.get("createdAt") or ultimo.get("date") or "",
                            "remitente": ultimo.get("from") or "agente@estudiarenuk.com",
                            "id": ultimo.get("id") or "",
                        }
                else:
                    logger.warning(f"Error obteniendo emails GHL: {r.status_code} — {r.text}")
                    return None

        except Exception as e:
            logger.error(f"Excepción al obtener emails de GHL: {e}")
            return None

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
