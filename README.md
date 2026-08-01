# Sistema de Eye Tracking y Análisis de Sentimientos en Tiempo Real

Sistema inteligente que integra **eye tracking** y **análisis de sentimientos** para medir de forma objetiva y continua el engagement publicitario, desarrollado como Trabajo de Integración Curricular.

**Autores:** José Jhonny Sánchez Solís, Nickole Cristina Lee Sagbay
**Tutor:** Ing. Giraldo de Lacaridad León Rodríguez
**Institución:** Universidad ECOTEC — Ingeniería en Sistemas Inteligentes, Campus Samborondón (2026)

---

## 📋 Descripción

El sistema mide el engagement publicitario de un espectador en tiempo real, combinando dos señales biométricas capturadas por cámara web:

- **Atención visual** — estimación de la dirección de mirada (yaw/pitch) mediante L2CS-Net
- **Emoción facial** — clasificación de 8 categorías emocionales mediante HSEmotion

Ambas señales se combinan en un índice de engagement mediante ponderación lineal:

```
E = 0.6 · Atención + 0.4 · Emoción positiva
```

El sistema fue validado experimentalmente con 30 participantes reales de la Universidad ECOTEC, expuestos al comercial "La Carta" (Coca-Cola, 2020), y sus resultados objetivos fueron contrastados contra un instrumento de autorreporte (Google Forms) para evaluar su validez de criterio.

## 🏗️ Arquitectura

El sistema sigue el patrón **Modelo-Vista-Controlador (MVC)**:

- **`models/`** — lógica de negocio: cálculo del índice de engagement, integración con L2CS-Net
- **`views/`** — interfaz gráfica (PyQt6): calibración, reproducción del estímulo, panel de control del investigador
- **`controllers/`** — coordinación entre modelo y vista, orquestación de la sesión experimental (`session_controller.py`)
- **`utils/`** — módulos de soporte: detección facial (Haar Cascade), calibración de mirada, exportación de reportes (PDF/Excel), reproducción de video
- **`assets/`** — recursos estáticos (logo institucional)
- **`main.py`** — punto de entrada de la aplicación
- **`corregir_todo.py`** — script de corrección retroactiva de datos (documentado más abajo)

## 🛠️ Stack tecnológico

| Componente | Tecnología |
|---|---|
| Estimación de mirada | L2CS-Net (PyTorch) |
| Reconocimiento emocional | HSEmotion |
| Detección facial | Haar Cascade (OpenCV) |
| Interfaz gráfica | PyQt6 |
| Procesamiento de video | OpenCV |
| Reportes | ReportLab (PDF), openpyxl (Excel) |
| Instrumento de validez | Google Forms |

**Hardware de referencia:** HP OMEN 16 (GPU NVIDIA RTX 5070), cámara Logitech Brio 4K (1920×1080 @ 30fps).

## ⚠️ Contenido no incluido en este repositorio

Por motivos de tamaño y, sobre todo, de **protección de datos personales** de los participantes del estudio (consentimiento informado, Ley Orgánica de Protección de Datos Personales del Ecuador), las siguientes carpetas están excluidas vía `.gitignore` y **no deben subirse nunca**:

- **`data/`** — reportes individuales, fotografías y registros CSV de los 30 participantes
- **`videos/`** — estímulo publicitario y anotaciones asociadas
- **`tesis_env/`** — entorno virtual de Python
- **`models/*.pkl`** — pesos pre-entrenados del modelo L2CS-Net (gaze360)

### Descarga del modelo L2CS-Net

Para ejecutar el sistema, descarga el modelo pre-entrenado `L2CSNet_gaze360.pkl` desde el repositorio oficial de L2CS-Net y colócalo en `models/`:

> _Completar con el enlace de descarga original del modelo._

## 🐛 Nota sobre `corregir_todo.py`

Durante el desarrollo se identificó un error de unidades (radianes vs. grados) en `session_controller.py` que infló artificialmente los valores de atención en los primeros 20 participantes grabados. `corregir_todo.py` regenera retroactivamente los reportes (PDF individual, Excel individual y consolidado) recuperando algebraicamente los valores reales de emoción a partir de los índices de engagement almacenados, usando las mismas funciones de exportación del sistema (`utils/export_utils.py`), sin alterar el diseño ni la estructura de los reportes originales. Se conserva en el repositorio como parte del historial de control de calidad de los datos.

## 📄 Licencia

Proyecto académico desarrollado como Trabajo de Integración Curricular para la Universidad ECOTEC. Uso restringido a fines educativos y de investigación.
