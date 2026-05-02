# 🚀 IMPLEMENTACIÓN DE JOSÉ — Paso a Paso Completo

> Agente de follow-up post-oferta que lee emails desde GHL automáticamente

---

## 📋 RESUMEN DE FLUJO

```
Asesor envía email desde GHL
  ↓ (Email se registra en GHL contact)
  ↓ Asesor marca contact.jose_fase = "oferta_enviada" en GHL
  ↓ GHL webhook notifica a FastAPI
  ↓ José consulta GHL API → obtiene último email
  ↓ José parsea email y extrae datos (cursos, universidades, links, costo)
  ↓ José envía mensaje inicial: "Hola, hemos enviado tu oferta..."
  ↓ 
  ↓ RECORDATORIOS AUTOMÁTICOS:
  ↓   +1h: Si no responde → "¿Pudiste revisar tu oferta?"
  ↓   +22h: Última píldora educativa antes de cerrar ventana Meta 24hs
  ↓
  ↓ Si responde con interés → agendar cita
  ↓ Si responde con objeción → escuchar y entender razón
```

---

## 🛠️ PASOS DE IMPLEMENTACIÓN

### **PASO 1 ✅ — Obtener emails desde GHL API**

**HECHO:** Ya agregamos `obtener_ultimo_email()` a `agent/providers/ghl.py`

Verifica que el método esté en:
```python
# agent/providers/ghl.py — línea ~306
async def obtener_ultimo_email(self, contact_id: str) -> dict | None:
    """Obtiene el último email enviado a un contacto desde GHL."""
```

---

### **PASO 2 — Crear config/josé.yaml con system prompt**

**Archivo:** `config/josé.yaml`

Este debe existir. El system prompt debe:
- Instrucciones específicas para José (no Sofía)
- Asumir que el estudiante YA recibió una oferta
- Enfoque en: confirmar interés, responder dudas, agendar cita
- NO hacer precalificación (eso ya lo hizo Sofía)

---

### **PASO 3 — Crear agent/josé/tools.py**

**Archivo:** `agent/josé/tools.py`

Herramientas específicas:
1. `parsear_email_para_jose(email_dict)` — extrae datos del email
2. `registrar_interesado(telefono, data)` — marca como interesado en SQLite
3. `registrar_objecion(telefono, objecion)` — guarda la objeción detectada
4. `crear_recordatorio_1h(telefono)` — programa recordatorio en 1h
5. `crear_recordatorio_22h(telefono)` — programa recordatorio en 22h
6. `agendar_cita(telefono, fecha, hora)` — guarda la cita

---

### **PASO 4 — Crear agent/josé/brain.py**

**Archivo:** `agent/josé/brain.py`

Similar a `agent/brain.py` pero:
- Lee `config/josé.yaml` en lugar de `config/prompts.yaml`
- Sistema prompt diferente (enfocado en follow-up/cierre)
- Misma integración con Anthropic Claude API

---

### **PASO 5 — Crear agent/utils/scheduler.py**

**Archivo:** `agent/utils/scheduler.py`

Gestiona recordatorios automáticos:
- `programar_recordatorio(telefono, segundos, tipo)` — programa un recordatorio
- Tipos: "recordatorio_1h", "recordatorio_22h"
- Usa `asyncio.create_task()` para no bloquear
- Almacena tareas en memoria (o Redis si escalas)

---

### **PASO 6 — Modificar agent/main.py**

**Cambios:**
- Agregar detección de `jose_fase` en el webhook
- Si `jose_fase == "oferta_enviada"` → activar José (no Sofía)
- Router: `if jose_fase → usar agent.josé.brain else → usar agent.brain`
- Activar scheduler de recordatorios (1h, 22h)

---

### **PASO 7 — Configurar memoria (SQLite existente)**

**NO necesita cambios:** Usa la misma `agent/memory.py`

José y Sofía comparten el mismo historial de mensajes por teléfono.
Solo diferencia: custom fields en GHL marcan la fase (josé_fase, sofía_fase, etc.)

---

### **PASO 8 — Custom Fields en GHL**

Agregar estos custom fields (contact level):

```
PARA LA OFERTA (crea el ASESOR cuando envía email):
  contact.oferta_nombre_curso: "text"
  contact.oferta_universidad: "text"
  contact.oferta_duracion: "text"
  contact.oferta_costo: "text"
  contact.oferta_links: "text"
  contact.oferta_fecha_envio: "date"

PARA JOSÉ (se actualizan automáticamente):
  contact.jose_fase: "text"  → valores: "oferta_enviada", "interesado", "cita_agendada", "no_interesado"
  contact.jose_ultimo_mensaje: "date"
  contact.jose_mensaje_1h_enviado: "checkbox"
  contact.jose_mensaje_22h_enviado: "checkbox"
  contact.jose_objecion: "text"
  contact.jose_cita_fecha: "date"
  contact.jose_cita_hora: "text"
```

---

### **PASO 9 — Testing**

Test local con `python tests/test_local_jose.py`:
1. Simula webhook de GHL (contact entra con `jose_fase = "oferta_enviada"`)
2. José obtiene último email
3. José parsea email y extrae datos
4. José envía primer mensaje
5. Usuario responde
6. José responde
7. Verificar recordatorios (1h, 22h)

---

## 🔧 ARQUITECTURA DETALLADA

```
WhatsApp (cliente escribe)
  ↓
GHL Webhook POST /webhook
  ↓
agent/main.py webhook_handler():
  - Detecta: contact.jose_fase == "oferta_enviada"
  - Llama a: obtener_ultimo_email(contact_id) ← ProveedorGHL
  - Parsea email con: parsear_email_para_jose(email)
  - Si es primer mensaje de José:
      - Envía: "Hola [Nombre], recibí tu oferta..."
      - Incluye resumen de email parseado
      - Programa recordatorios (1h, 22h)
      - Registra en GHL: jose_ultimo_mensaje = ahora
  ↓
agent/josé/brain.py generar_respuesta():
  - Sistema prompt de José (follow-up, cierre)
  - Historial de conversación (desde memory.py)
  - Genera respuesta empática
  ↓
agent/josé/tools.py:
  - Si detecta interés → registrar_interesado()
  - Si detecta objeción → registrar_objecion()
  - Si propone cita → agendar_cita()
  ↓
Actualizar GHL custom fields
  ↓
Enviar mensaje via ProveedorGHL.enviar_mensaje()
```

---

## 📝 CUSTOM FIELDS — Valores posibles

### `contact.jose_fase`
- `"pendiente"` — aún no entra José
- `"oferta_enviada"` — asesor envió oferta, José se activa
- `"interesado"` — estudiante confirmó interés
- `"cita_agendada"` — siguiente paso agendado
- `"no_interesado"` — rechazó la oferta
- `"objecion"` — tiene una pregunta/objeción

### `contact.jose_objecion` (si existe)
- `"no_entiendo_student_finance"` — dudas sobre préstamo
- `"miedo_a_costos"` — costo del curso
- `"falta_tiempo"` — no tienen tiempo para estudiar
- `"falta_requisitos"` — creen que no califican
- `"otra"` — otra razón (José describe)

---

## ⚙️ CONFIGURACIÓN .env

Agregar (si no existe):
```env
# José scheduling (recordatorios)
JOSE_RECORDATORIO_1H_SEGUNDOS=3600
JOSE_RECORDATORIO_22H_SEGUNDOS=79200
```

---

## 🎯 MÉTRICAS DE ÉXITO

- ✅ José se activa cuando `jose_fase = "oferta_enviada"`
- ✅ Lee email automáticamente desde GHL API
- ✅ Parsea datos del email (curso, universidad, links)
- ✅ Envía primer mensaje en <5 segundos
- ✅ Recordatorios se envían en 1h y 22h
- ✅ Registra respuestas en SQLite (historial)
- ✅ Actualiza GHL custom fields según conversación
- ✅ Agrupa interés + agendamiento de cita

---

## 🚀 SIGUIENTES PASOS

1. Crear `config/josé.yaml` con system prompt
2. Crear `agent/josé/tools.py` con funciones
3. Crear `agent/josé/brain.py`
4. Crear `agent/utils/scheduler.py`
5. Modificar `agent/main.py` con router
6. Probar en local
7. Desplegar a Railway

