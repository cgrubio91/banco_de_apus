"""
Script para crear la tabla de historial de conversaciones
"""

from db_config import get_db_connection

def create_historial_table():
    """Crea la tabla historial_conversaciones si no existe."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Crear tabla de historial
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historial_conversaciones (
                id SERIAL PRIMARY KEY,
                telefono VARCHAR(50) NOT NULL,
                mensaje_usuario TEXT NOT NULL,
                sql_generado TEXT,
                respuesta_bot TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Crear índice para optimizar búsquedas por teléfono y fecha
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_telefono_timestamp 
            ON historial_conversaciones (telefono, timestamp DESC)
        """)
        
        conn.commit()
        cursor.close()
        print("✅ Tabla historial_conversaciones creada exitosamente")
        
    except Exception as e:
        print(f"❌ Error creando tabla: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    create_historial_table()
