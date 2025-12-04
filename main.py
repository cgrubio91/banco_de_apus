# ===============================
# 📦 main.py — MAPUS BOT IA SQL + APU + CONTROL DE USUARIOS
# ===============================

from fastapi import FastAPI, Request
from psycopg2.extras import RealDictCursor

import requests
import json
import re
import os
import time
from datetime import datetime
from dotenv import load_dotenv

# Import centralized database configuration
from db_config import get_db_connection, execute_query

try:
    from twilio.rest import Client
except Exception as e:
    print(f"⚠️ Twilio import failed: {e}")
    Client = None

# ===============================
# 🔑 CONFIGURACIÓN INICIAL
# ===============================
load_dotenv()
app = FastAPI()

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Twilio
if Client:
    ACCOUNT_SID = os.getenv("ACCOUNT_SID")
    AUTH_TOKEN = os.getenv("AUTH_TOKEN")
    FROM_WHATSAPP = os.getenv("FROM_WHATSAPP")
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
else:
    ACCOUNT_SID = AUTH_TOKEN = FROM_WHATSAPP = None
    client = None

# ===============================
# 🧠 FUNCIONES AUXILIARES
# ===============================
def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def gemini_generate(prompt: str) -> str:
    """Llama a la API de Gemini para generar texto."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        r = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=30)
        data = r.json()
        if "candidates" not in data:
            log(f"❌ Error Gemini: {json.dumps(data, indent=2)}")
            return "No se pudo procesar tu solicitud con la IA."
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        log(f"❌ Error conectando con Gemini: {e}")
        return "Error al conectar con la IA de Gemini."


def ejecutar_sql(query: str):
    """Ejecuta una consulta SQL y devuelve los resultados."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        return rows
    except Exception as e:
        log(f"❌ Error SQL: {e}")
        return [{"error": str(e)}]
    finally:
        if conn:
            conn.close()


def send_whatsapp_message(to, text):
    """Envía un mensaje de WhatsApp por Twilio."""
    try:
        client.messages.create(from_=FROM_WHATSAPP, to=to, body=text)
        log(f"✅ Mensaje enviado a {to}")
    except Exception as e:
        log(f"❌ Error enviando mensaje WhatsApp: {e}")


# ===============================
# � GESTIÓN DE MEMORIA CONVERSACIONAL
# ===============================
def guardar_conversacion(telefono: str, mensaje_usuario: str, sql_generado: str, respuesta_bot: str):
    """Guarda una interacción en el historial de conversaciones."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO historial_conversaciones (telefono, mensaje_usuario, sql_generado, respuesta_bot)
            VALUES (%s, %s, %s, %s)
        """, (telefono, mensaje_usuario, sql_generado, respuesta_bot))
        conn.commit()
        cursor.close()
        log(f"💾 Conversación guardada para {telefono}")
    except Exception as e:
        log(f"⚠️ Error guardando conversación: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def obtener_historial(telefono: str, limite: int = 5):
    """Recupera las últimas conversaciones del usuario."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT mensaje_usuario, sql_generado, respuesta_bot, timestamp
            FROM historial_conversaciones
            WHERE telefono = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """, (telefono, limite))
        historial = cursor.fetchall()
        cursor.close()
        # Invertir para tener orden cronológico (más antiguo primero)
        return list(reversed(historial))
    except Exception as e:
        log(f"⚠️ Error recuperando historial: {e}")
        return []
    finally:
        if conn:
            conn.close()


# ===============================
# �👥 CONTROL DE USUARIOS
# ===============================
def usuario_autorizado(telefono: str):
    """Verifica si el usuario está autorizado en la tabla 'usuarios'."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM usuarios WHERE telefono = %s AND activo = true", (telefono,))
        user = cursor.fetchone()
        cursor.close()
        return user
    except Exception as e:
        log(f"❌ Error verificando usuario: {e}")
        return None
    finally:
        if conn:
            conn.close()


# ===============================
# 🩺 HEALTH CHECK
# ===============================
@app.get("/")
def home():
    return {"status": "online", "message": "Bot de WhatsApp APUs activo 🚀"}

@app.get("/health")
def health_check():
    """Verifica la conexión a la base de datos."""
    status = {"status": "ok", "database": "connected"}
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
    except Exception as e:
        status["status"] = "error"
        status["database"] = str(e)
        log(f"❌ Health check falló: {e}")
    finally:
        if conn:
            conn.close()
    return status


# ===============================
# 💬 ENDPOINT WHATSAPP WEBHOOK
# ===============================
@app.post("/whatsapp_webhook")
async def whatsapp_webhook(request: Request):
    """Procesa mensajes entrantes desde Twilio WhatsApp."""
    data = await request.form()
    from_number = data.get("From")
    message_body = data.get("Body", "").strip()

    log(f"📩 Mensaje recibido de {from_number}: {message_body}")

    # 🛡️ Verificación de usuario
    user = usuario_autorizado(from_number)
    if not user:
        send_whatsapp_message(from_number, "🚫 Acceso restringido.\nNo tienes permiso para usar este asistente.\nContacta con el administrador para solicitar acceso.")
        log(f"❌ Acceso denegado a {from_number}")
        return "UNAUTHORIZED"

    log(f"✅ Usuario autorizado: {user['nombre']} ({user['rol']})")

    if not message_body:
        send_whatsapp_message(from_number, f"👋 Hola {user['nombre']}! Envíame una pregunta sobre tus APUs o ítems, y te ayudaré con gusto.")
        return "OK"

    # ===============================
    # 💭 RECUPERAR HISTORIAL
    # ===============================
    historial = obtener_historial(from_number, limite=5)
    contexto_historial = ""
    
    if historial:
        contexto_historial = "\n\nCONTEXTO DE CONVERSACIONES PREVIAS:\n"
        for i, conv in enumerate(historial, 1):
            contexto_historial += f"Usuario: {conv['mensaje_usuario']}\n"
            if conv['sql_generado']:
                contexto_historial += f"SQL generado: {conv['sql_generado'][:100]}...\n"
        contexto_historial += "\nUSA ESTE CONTEXTO para entender referencias como 'el anterior', 'ese mismo', 'compara con...', etc.\n"
        log(f"📚 Historial recuperado: {len(historial)} mensajes")

    # ===============================
    # 🧠 PROMPT PARA SQL
    # ===============================
    prompt_sql = f"""
    Actúa como un asistente experto en bases de datos PostgreSQL y en análisis de precios unitarios (APU) de obras civiles.
    Convierte la solicitud del usuario en una consulta SQL válida, considerando que el usuario NO conoce los nombres técnicos de las columnas.

    Tabla: apus
    Columnas disponibles:
    - fecha_aprobacion_apu, fecha_analisis_apu, ciudad, pais, entidad, contratista,
      nombre_proyecto, numero_contrato, item, items_descripcion, item_unidad,
      precio_unitario, precio_unitario_sin_aiu, codigo_insumo, tipo_insumo,
      insumo_descripcion, insumo_unidad, rendimiento_insumo, precio_unitario_apu,
      precio_parcial_apu, observacion, link_documento

    REGLAS CRÍTICAS PARA BÚSQUEDAS:
    
    1. **BÚSQUEDAS FLEXIBLES** - Siempre usa ILIKE (case-insensitive) con % para búsquedas parciales:
       - Usuario dice "proyecto X" → WHERE nombre_proyecto ILIKE '%X%'
       - Usuario dice "item de concreto" → WHERE items_descripcion ILIKE '%concreto%'
       - Usuario dice "insumo cemento" → WHERE insumo_descripcion ILIKE '%cemento%'
       - Usuario dice "ciudad Bogotá" → WHERE ciudad ILIKE '%bogotá%'
    
    2. **MAPEO DE LENGUAJE NATURAL A COLUMNAS**:
       - "proyecto" / "obra" → nombre_proyecto
       - "item" / "actividad" → items_descripcion
       - "insumo" / "material" → insumo_descripcion
       - "precio" / "valor" / "costo" → precio_unitario
       - "ciudad" / "lugar" → ciudad
       - "contratista" / "empresa" → contratista
       - "más caro" / "más costoso" → ORDER BY precio_unitario DESC
       - "más barato" / "más económico" → ORDER BY precio_unitario ASC
       - "cuántos" / "cantidad" → COUNT(*)
       - "promedio" → AVG(precio_unitario)
       - "total" → SUM(precio_unitario)
    
    3. **EJEMPLOS DE CONSULTAS COMUNES**:
       
       ❌ INCORRECTO:
       Usuario: "cuántos items tiene el proyecto la macarena"
       SQL MAL: SELECT * FROM apus WHERE nombre_proyecto = 'la macarena'
       
       ✅ CORRECTO:
       Usuario: "cuántos items tiene el proyecto la macarena"
       SQL: SELECT COUNT(DISTINCT items_descripcion) as total_items FROM apus WHERE nombre_proyecto ILIKE '%macarena%'
       
       ✅ CORRECTO:
       Usuario: "cuál es el item más costoso de la macarena"
       SQL: SELECT items_descripcion, precio_unitario FROM apus WHERE nombre_proyecto ILIKE '%macarena%' ORDER BY precio_unitario DESC LIMIT 1
       
       ✅ CORRECTO:
       Usuario: "dame los items de excavación"
       SQL: SELECT items_descripcion, precio_unitario FROM apus WHERE items_descripcion ILIKE '%excavación%' ORDER BY precio_unitario DESC LIMIT 20
       
       ✅ CORRECTO:
       Usuario: "proyectos en Bogotá"
       SQL: SELECT DISTINCT nombre_proyecto, ciudad FROM apus WHERE ciudad ILIKE '%bogotá%' LIMIT 20
    
    4. **OTRAS REGLAS**:
       - Limita resultados a 20 con LIMIT 20 (a menos que el usuario especifique otra cantidad)
       - Ordena de manera lógica (por precio, fecha, nombre, etc.)
       - Usa DISTINCT cuando sea necesario para evitar duplicados
       - Si pide conteo, usa COUNT(*)
       - Si pide promedio, usa AVG()
       - Para comparaciones, usa GROUP BY con la columna apropiada
       - Si el usuario hace referencia a consultas anteriores, usa el contexto previo
    
    5. **NUNCA USES**:
       - Igualdad exacta con = para textos (⚠️ casi siempre usar ILIKE)
       - Formato Markdown ni ```sql```
       - Consultas que no sean SELECT
    
    {contexto_historial}
    
    Usuario pregunta: "{message_body}"
    
    Genera SOLO la consulta SQL, sin explicaciones.
    """

    sql_query = gemini_generate(prompt_sql)
    sql_query = re.sub(r"```sql|```", "", sql_query).strip()
    log(f"🧠 SQL generado: {sql_query}")

    # ===============================
    # 🗃️ EJECUTAR CONSULTA SQL
    # ===============================
    if not sql_query.lower().startswith("select"):
        respuesta = "Solo se permiten consultas de lectura."
    else:
        resultados = ejecutar_sql(sql_query)
        log(f"📊 Resultados SQL: {resultados}")

        if not resultados or "error" in resultados[0]:
            respuesta = "No se encontraron resultados para tu consulta."
        else:
            prompt_resumen = f"""
            Eres un ingeniero experto en Análisis de Precios Unitarios (APU).
            Presenta los resultados SQL de manera clara, profesional y bien formateada para WhatsApp.
            
            INSTRUCCIONES DE FORMATO:
            1. Saluda brevemente al usuario por su nombre: {user['nombre']}
            2. Analiza el tipo de consulta y formatea la respuesta apropiadamente:
               - **LISTADOS**: Usa numeración (1., 2., 3., etc.) con los datos más relevantes
               - **COMPARACIONES**: Usa formato de tabla simple con alineación, separando columnas con | 
               - **TOTALES/AGREGACIONES**: Presenta el resultado de forma clara y destacada
               - **CONSULTA SIMPLE**: Responde en 1-2 párrafos concisos
            
            3. Formato de tabla para comparaciones (ejemplo):
            ```
            Item                    | Precio      | Ciudad
            ----------------------------------------
            Excavación manual       | $45,000     | Bogotá
            Relleno compactado      | $32,500     | Medellín
            ```
            
            4. Formato de listado (ejemplo):
            ```
            1. Excavación manual - $45,000 (Bogotá)
            2. Relleno compactado - $32,500 (Medellín)
            ```
            
            5. Incluye solo la información más relevante. Si hay más de 15 resultados, resume los primeros 10-15 más importantes.
            6. Al final, menciona el total de registros encontrados si son muchos.
            7. Usa emojis sutiles para mejorar la lectura: 📊 💰 🏗️ 📍 ✅
            8. NO uses formato Markdown (**, __, etc.), usa MAYÚSCULAS para títulos.
            9. Mantén las líneas cortas (máximo 60 caracteres) para que se vean bien en WhatsApp.
            
            Pregunta del usuario: "{message_body}"
            Resultados SQL: {json.dumps(resultados, ensure_ascii=False, default=str)}
            """
            respuesta = gemini_generate(prompt_resumen)

    # ===============================
    # 💾 GUARDAR EN HISTORIAL
    # ===============================
    guardar_conversacion(from_number, message_body, sql_query if sql_query.lower().startswith("select") else "", respuesta)

    # ===============================
    # 📤 ENVÍO DE RESPUESTA
    # ===============================
    if len(respuesta) > 1500:
        partes = [respuesta[i:i+1500] for i in range(0, len(respuesta), 1500)]
        for i, parte in enumerate(partes):
            send_whatsapp_message(from_number, parte)
            log(f"🗣️ Parte {i+1}/{len(partes)} enviada ({len(parte)} caracteres).")
            time.sleep(2)
    else:
        send_whatsapp_message(from_number, respuesta)
        log(f"🗣️ Respuesta enviada ({len(respuesta)} caracteres).")

    return "OK"


# ===============================
# 🏁 SERVIDOR LOCAL
# ===============================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    log(f"🚀 Iniciando servidor en puerto {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
