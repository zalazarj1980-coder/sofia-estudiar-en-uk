# Safe Sofía appointment handling

Sofía never creates an appointment, confirms one, alerts the owner, or pauses a
conversation based on a chat request. Legacy or malformed booking/pausing
markers are not sent to the student. The separate manual pause/resume workflow
continues to work; retries of an existing pause no longer produce a duplicate
row error.

## Safe default
Leave `SOFIA_CHAT_BOOKING_ENABLED` unset or `false`. The app does not read or
update GHL booking fields and does not emit the booking trigger. It directs
people to the existing https://estudiarenuk.co.uk/agendamiento route, without
promising any available slot. Before rollout, verify the public link and choose
the correct booking widget. The existing link was not render-verified as part
of the isolated tests.

## Optional future request path (not ready to enable without end-to-end GHL verification)
1. Authenticate and isolate the incoming WhatsApp webhook as appropriate for this
   deployment. The legacy inbound route's authentication is not changed here.
2. Verify GHL's workflow before enabling. It must match the intended contact,
   location, calendar and London date/time, keep availability override off,
   and have no confirmation, operator alert, or booking-pause action until a
   matching appointment exists. A request flag alone must never qualify.
3. With existing access only, set `SOFIA_BOOKING_CALENDAR_ID` and three separate
   field IDs: `SOFIA_BOOKING_DATE_FIELD_ID` (`contact.cita_fecha_sofia` text ISO date),
   `SOFIA_BOOKING_TIME_FIELD_ID` (`contact.cita_hora_sofia` text HH:MM) and
   `SOFIA_BOOKING_TRIGGER_FIELD_ID` (`contact.cita_solicitada_sofia`, string `true`).
   The existing production mappings were verified separately by the operator;
   do not reuse production IDs in a test environment. Optionally set
   `SOFIA_BOOKING_CALENDAR_USER_ID` to the same verified member the workflow
   books; otherwise the free-slot check uses calendar-wide availability.
4. Verify the current key can read free slots and read/update contact fields.
   The calendar API header defaults to `v3`; use
   `SOFIA_BOOKING_CALENDAR_API_VERSION` only to match a known supported version.
   No calendar write or custom-field listing scope is used. Do not expand
   permissions to work around a failed check.
5. Only after staging proves the success and failure paths, opt in with
   `SOFIA_CHAT_BOOKING_ENABLED=true`.

The app verifies the exact free slot and field readback, then emits the trigger
last. Its reply still says pending. A duplicate active request is not restarted,
and a conflicting active request is not overwritten. The app's lock applies
within the running process; GHL must also prevent duplicate appointments and
track/reset or resolve the pending flag safely after processing. No automatic
retry after ambiguous trigger delivery. A free slot is not a reservation and
can be taken between validation and booking. GHL must stay fail-closed.

## Tests
With app requirements installed, run:
`python -m unittest discover -s tests -p 'test_booking_webhook.py' -v`

Tests exercise the actual FastAPI POST /webhook and buffer handler using
synthetic inbound GHL data, injected model output, in-memory ASGI HTTP and
mocked GHL HTTP/WhatsApp sends. They do not touch live fields, contacts or
calendars. The application's SQLite duplicate-pause handling is not exercised
by these webhook tests.
