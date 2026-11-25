"""Synthetic WhatsApp /webhook tests. No external requests or credentials."""
import asyncio
import importlib
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx

from agent.booking import BOOKING_URL, LONDON
from agent.providers.ghl import ProveedorGHL

PHONE = "+447700900152"
CONTACT_ID = "test-contact"
LOCATION = "test-location"
DATE_ID, TIME_ID, FLAG_ID = "test-date", "test-time", "test-trigger"
REAL_CLIENT = httpx.AsyncClient


class BookingWebhookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.day = (datetime.now(LONDON) + timedelta(days=1)).date().isoformat()
        self.slot = datetime.fromisoformat(self.day + "T14:00:00").replace(tzinfo=LONDON)
        self.fields = {}
        self.writes = []
        self.reads = []
        self.sends = []
        self.offered_slots = [self.slot.isoformat()]
        self.free_status = 200
        self.skip_date_write = False
        self.flag_write_fails = False
        self.contact_location = LOCATION

        self.provider = ProveedorGHL()
        self.provider.api_key = "synthetic-not-a-real-key"
        self.provider.location_id = LOCATION
        self.provider.enviar_mensaje = AsyncMock(side_effect=self._capture_send)

        brain = types.ModuleType("agent.brain")
        self.model = brain.generar_respuesta = AsyncMock()
        memory = types.ModuleType("agent.memory")
        for name in ("inicializar_db", "guardar_mensaje", "obtener_historial", "pausar_conversacion",
                     "reanudar_conversacion", "conversacion_esta_pausada"):
            setattr(memory, name, AsyncMock())
        memory.conversacion_esta_pausada.return_value = False
        memory.obtener_historial.return_value = []
        self.memory = memory
        modules = patch.dict(sys.modules, {"agent.brain": brain, "agent.memory": memory})
        modules.start()
        self.addCleanup(modules.stop)
        self.app = importlib.import_module("agent.main")
        self.app.proveedor = self.provider
        self.app.BUFFER_DELAY_SEGUNDOS = 0
        self.app.generar_respuesta = self.model
        for name in ("guardar_mensaje", "obtener_historial", "pausar_conversacion",
                     "conversacion_esta_pausada"):
            setattr(self.app, name, getattr(memory, name))
        self.env = {
            "SOFIA_CHAT_BOOKING_ENABLED": "true",
            "SOFIA_BOOKING_CALENDAR_ID": "test-calendar",
            "SOFIA_BOOKING_DATE_FIELD_ID": DATE_ID,
            "SOFIA_BOOKING_TIME_FIELD_ID": TIME_ID,
            "SOFIA_BOOKING_TRIGGER_FIELD_ID": FLAG_ID,
        }

    async def _capture_send(self, phone, text):
        self.sends.append((phone, text))
        return True

    def _mock_ghl(self, request):
        path = request.url.path
        if request.method == "GET":
            self.reads.append(path)
            if path == "/calendars/test-calendar/free-slots":
                self.assertEqual(request.url.params["timezone"], "Europe/London")
                return httpx.Response(self.free_status, json={self.day: {"slots": self.offered_slots}})
            self.assertEqual(path, f"/contacts/{CONTACT_ID}")
            return httpx.Response(200, json={"contact": {
                "id": CONTACT_ID, "locationId": self.contact_location, "phone": PHONE,
                "customFields": [{"id": k, "value": v} for k, v in self.fields.items()]
            }})
        self.assertEqual(request.method, "PUT")
        self.assertEqual(path, f"/contacts/{CONTACT_ID}")
        data = {f["id"]: f["field_value"] for f in json.loads(request.content)["customFields"]}
        self.writes.append(data)
        if self.flag_write_fails and FLAG_ID in data:
            return httpx.Response(503)
        if not self.skip_date_write or FLAG_ID in data:
            self.fields.update(data)
        return httpx.Response(200, json={})

    def _model_booking(self, *, hour="14:00", tail="[PAUSA:booking_confirmado]"):
        payload = json.dumps({"nombre": "Synthetic {name}", "fecha": self.day, "hora": hour})
        return f"Tu cita está confirmada. [BOOKING:{payload}] {tail}"

    async def interact(self, model_response, *, text="Quiero reservar a las 14:00", enabled=True):
        self.model.return_value = model_response
        with patch.dict(os.environ, {**self.env, "SOFIA_CHAT_BOOKING_ENABLED":
                                    "true" if enabled else "false"}):
            fake = httpx.MockTransport(self._mock_ghl)
            with patch("agent.providers.ghl.httpx.AsyncClient",
                       side_effect=lambda **kw: REAL_CLIENT(transport=fake, **kw)):
                transport = httpx.ASGITransport(app=self.app.app)
                async with REAL_CLIENT(transport=transport, base_url="http://asgi.test") as client:
                    result = await client.post("/webhook", json={"contact_id": CONTACT_ID,
                                               "phone": PHONE, "messageId": str(len(self.sends)),
                                               "message": {"body": text}})
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json(), {"status": "ok"})
                    task = self.app._buffer_timers.get(PHONE)
                    self.assertIsNotNone(task)
                    await asyncio.wait_for(task, timeout=3)
        self.assertEqual(self.sends[-1][0], PHONE)
        self.memory.pausar_conversacion.assert_not_awaited()
        self.assertNotIn("[BOOKING", self.sends[-1][1])
        self.assertNotIn("[PAUSA", self.sends[-1][1])
        return self.sends[-1][1]

    async def test_disabled_by_default_links_and_never_touches_crm(self):
        answer = await self.interact(self._model_booking(), enabled=False)
        self.assertIn(BOOKING_URL, answer)
        self.assertFalse(self.writes or self.reads)
        self.assertEqual(len(self.sends), 1)

    async def test_readback_by_field_id_only_acknowledges_pending(self):
        answer = await self.interact(self._model_booking())
        self.assertIn("pendiente", answer)
        self.assertIn("no reserva", answer)
        self.assertEqual(self.writes, [{DATE_ID: self.day, TIME_ID: "14:00"}, {FLAG_ID: "true"}])
        self.assertEqual(len(self.sends), 1)

    async def test_duplicate_and_conflicting_requests_do_not_write(self):
        self.fields = {DATE_ID: self.day, TIME_ID: "14:00", FLAG_ID: True}
        self.assertIn("pendiente", await self.interact(self._model_booking()))
        self.assertIn("antes de reservar", await self.interact(self._model_booking(hour="15:00")))
        self.assertFalse(self.writes)

    async def test_all_malformed_requests_are_neutral_and_never_pause(self):
        for model in ("Cita confirmada [BOOKING:broken [PAUSA:booking_confirmado]",
                      "Cita confirmada [BOOKING {broken", self._model_booking(hour="25:00"),
                      "Tu cita está confirmada", "[PAUSA:booking_confirmado]",
                      self._model_booking() + " " + self._model_booking()):
            with self.subTest(model=model):
                answer = await self.interact(model)
                self.assertNotIn("está confirmada", answer)
                self.assertFalse(self.writes or self.reads)

    async def test_unavailable_and_unauthorized_slots_never_trigger(self):
        self.offered_slots = [self.slot.replace(hour=15).isoformat()]
        self.assertIn(BOOKING_URL, await self.interact(self._model_booking()))
        self.free_status = 401
        self.assertIn(BOOKING_URL, await self.interact(self._model_booking()))
        self.assertFalse(self.writes)

    async def test_unverified_fields_never_trigger(self):
        self.skip_date_write = True
        self.assertIn(BOOKING_URL, await self.interact(self._model_booking()))
        self.assertEqual(self.writes, [{DATE_ID: self.day, TIME_ID: "14:00"}])

    async def test_ambiguous_write_does_not_invite_to_rebook(self):
        self.flag_write_fails = True
        answer = await self.interact(self._model_booking())
        self.assertNotIn(BOOKING_URL, answer)
        self.assertIn("antes de reservar", answer)
        self.assertEqual(len(self.sends), 1)

    async def test_reject_contact_from_other_location(self):
        self.contact_location = "wrong-location"
        self.assertIn(BOOKING_URL, await self.interact(self._model_booking()))
        self.assertFalse(self.writes)

    async def test_english_and_normal_conversation_still_work(self):
        self.assertIn("London time", await self.interact(self._model_booking(),
                                                         text="Please book my appointment at 2pm"))
        self.assertEqual(await self.interact("Puedes estudiar presencialmente.", text="Modalidad"),
                         "Puedes estudiar presencialmente.")


if __name__ == "__main__":
    unittest.main()
