"""
CORRECCIÓN COMPLETA — Excel individual + PDF individual + Consolidado
=======================================================================

Regenera los 3 tipos de reporte para los participantes grabados ANTES
del fix de yaw/pitch en session_controller.py, usando las MISMAS
funciones de utils/export_utils.py que ya usa tu sistema (save_individual_excel,
save_individual_pdf, save_consolidated_excel) -- para que el diseño,
colores, tipografía y estructura salgan idénticos al original, no una
versión reinventada.

IMPORTANTE - qué NO toca:
    - Ningún archivo de código del prototipo (session_controller.py,
      engagement_model.py, export_utils.py, main.py, video_player.py,
      calibration.py). Solo LEE esas funciones para reutilizarlas.
    - Los CSVs crudos de data/ (solo se leen).

Qué SÍ hace:
    - Recalcula correctamente engagement, tiempo mirando, distracciones
      (el bug de yaw/pitch) a partir del CSV crudo de cada participante.
    - Reconstruye sector_log y aoi_log de forma EXACTA (sin pérdida),
      leyéndolos de las hojas "Registro" y "AOI" del Excel ya existente
      -- esos datos nunca tuvieron el bug, así que son 100% confiables.
    - Reconstruye el heatmap de atención de forma APROXIMADA (ver
      limitación abajo).
    - Sobrescribe el Excel y el PDF individuales, y actualiza las filas
      correspondientes del consolidado.

LIMITACIÓN HONESTA -- el heatmap de atención:
    El heatmap original se genera a partir de coordenadas continuas de
    mirada (gaze_x, gaze_y) que NO se guardan en ningún reporte -- solo
    se guarda el sector (1 de 16 posibles) por segundo. Este script
    reconstruye un heatmap aproximado distribuyendo el tiempo por sector
    y suavizando el resultado, por lo que se verá con menos definición
    que el original (más "en bloques"), aunque conceptualmente correcto.
    Todo lo demás (métricas, AOI, tabla de sectores, emociones, línea de
    tiempo) se reconstruye de forma exacta.

ANTES DE CORRERLO:
    Haz un respaldo de tu carpeta data/ completa (cópiala a otro lado).
    Este script SOBREESCRIBE los Excel y PDF individuales y el
    consolidado.

Uso:
    python corregir_todo.py
"""

import pandas as pd
import numpy as np
import math
import glob
import os
import sys
import cv2
import openpyxl

# Para poder importar utils/export_utils.py igual que hace tu main.py
sys.path.insert(0, os.getcwd())
from utils.export_utils import save_individual_excel, save_individual_pdf

YAW_THRESHOLD_DEG = 35.0
PITCH_THRESHOLD_DEG = 30.0
PITCH_OFFSET_DEG = 0.0

DATA_DIR = "data"
CONSOLIDADO_PATH = "data/reporte_consolidado.xlsx"
LOG_PATH = "log_correccion_completa.txt"


# ──────────────────────────────────────────────────────────────
#  1. Métricas corregidas desde el CSV crudo (igual que antes)
# ──────────────────────────────────────────────────────────────

def yaw_pitch_are_in_radians(series_yaw, series_pitch):
    return max(series_yaw.abs().max(), series_pitch.abs().max()) < 3.3


def calcular_metricas_corregidas(csv_path):
    df = pd.read_csv(csv_path)
    pid = df["participant_id"].iloc[0] if "participant_id" in df.columns else None

    if yaw_pitch_are_in_radians(df["yaw"], df["pitch"]):
        yaw_deg = df["yaw"].apply(math.degrees)
        pitch_deg = df["pitch"].apply(math.degrees)
    else:
        yaw_deg, pitch_deg = df["yaw"], df["pitch"]

    corrected_pitch = pitch_deg - PITCH_OFFSET_DEG
    is_looking_corr = (yaw_deg.abs() < YAW_THRESHOLD_DEG) & (corrected_pitch.abs() < PITCH_THRESHOLD_DEG)

    tiene_eng = "engagement_index" in df.columns
    if tiene_eng and df["is_looking"].all():
        p_pos = ((df["engagement_index"] - 0.6) / 0.4).clip(0, 1)
    elif tiene_eng:
        p_pos = pd.Series(0.0, index=df.index)
        mask = df["is_looking"] == True
        p_pos[mask] = ((df.loc[mask, "engagement_index"] - 0.6) / 0.4).clip(0, 1)
    else:
        p_pos = pd.Series(0.0, index=df.index)

    engagement_corr = np.where(is_looking_corr, 0.6 + 0.4 * p_pos, 0.0)
    total = len(df)

    transiciones = is_looking_corr.astype(int).diff()
    num_distracciones = int((transiciones == -1).sum())

    ts = pd.to_datetime(df["timestamp"])
    duracion_seg = (ts.iloc[-1] - ts.iloc[0]).total_seconds()
    fps_real = total / duracion_seg if duracion_seg > 0 else 1

    frames_por_seg = max(1, total // 4)
    segmentos = {}
    for i in range(4):
        ini = i * frames_por_seg
        fin = ini + frames_por_seg if i < 3 else total
        segmentos[f"segmento_{i+1}"] = round(np.mean(engagement_corr[ini:fin]), 4)

    # --- FIX: estos campos no dependen del bug de yaw/pitch, pero se habían
    # quedado fuera del diccionario de retorno original, lo que hacía que
    # export_utils.py los reemplazara por sus valores por defecto ('—', 0.0%)
    # al regenerar el Excel. Se recuperan aquí desde el mismo CSV crudo.
    POSITIVAS = {"happy", "surprise"}
    NEGATIVAS = {"angry", "sad", "fear", "disgust", "contempt"}
    emocion_dominante = df["emotion"].mode()[0] if total > 0 else "—"
    positivas_pct = round(df["emotion"].isin(POSITIVAS).mean() * 100, 2)
    negativas_pct = round(df["emotion"].isin(NEGATIVAS).mean() * 100, 2)
    valencia = round(positivas_pct - negativas_pct, 2)
    cambios = (df["emotion"] != df["emotion"].shift()).sum()
    duracion_min = duracion_seg / 60
    variabilidad = round(cambios / duracion_min, 2) if duracion_min > 0 else 0.0
    tasa_deteccion = round(df["face_detected"].mean() * 100, 2) if "face_detected" in df.columns else 100.0

    return {
        "id": pid,
        "engagement_promedio": round(np.mean(engagement_corr), 4),
        "engagement_maximo": round(np.max(engagement_corr), 4),
        "engagement_minimo": round(np.min(engagement_corr), 4),
        "engagement_std": round(np.std(engagement_corr), 4),
        "engagement_segmentos": segmentos,
        "tiempo_mirando": round(is_looking_corr.mean() * 100, 2),
        "num_distracciones": num_distracciones,
        "dur_distraccion_prom": round(
            (int((~is_looking_corr).sum()) / fps_real) / num_distracciones, 2
        ) if num_distracciones > 0 else 0.0,
        "fps_promedio": round(fps_real, 2),
        "total_frames": total,
        "emocion_dominante": emocion_dominante,
        "valencia_emocional": valencia,
        "positivas_pct": positivas_pct,
        "negativas_pct": negativas_pct,
        "variabilidad_emocional": variabilidad,
        "tasa_deteccion": tasa_deteccion,
    }


# ──────────────────────────────────────────────────────────────
#  2. Reconstrucción SIN PÉRDIDA de sector_log y aoi_log
#     desde el Excel ya existente (Registro y AOI)
# ──────────────────────────────────────────────────────────────

def reconstruir_sector_log(wb):
    ws = wb["Registro"]
    sector_log = []
    for row in ws.iter_rows(min_row=3, values_only=True):
        if row[0] is None:
            continue
        segundo, sector, emocion = row[0], row[1], row[2]
        try:
            sector_num = int(str(sector).replace("S", ""))
        except (ValueError, TypeError):
            continue
        sector_log.append({"tiempo_seg": int(segundo), "sector": sector_num, "emocion": emocion})
    return sector_log


def reconstruir_aoi_log(wb):
    if "AOI" not in wb.sheetnames:
        return []
    ws = wb["AOI"]
    aoi_log = []
    aoi_actual = None
    modo_tabla = False
    for row in ws.iter_rows(values_only=True):
        primera = row[0]
        if isinstance(primera, str) and primera.startswith("AOI "):
            aoi_actual = primera.split(" (")[0].strip()
            modo_tabla = False
            continue
        if primera == "Segundo":
            modo_tabla = True
            continue
        if modo_tabla and isinstance(primera, (int, float)):
            segundo, sector, emocion = row[0], row[1], row[2]
            try:
                sector_num = int(str(sector).replace("S", ""))
            except (ValueError, TypeError):
                sector_num = None
            aoi_log.append({
                "segundo": int(segundo),
                "aoi_id": aoi_actual,
                "aoi_nombre": aoi_actual,
                "sector": sector_num,
                "emocion": emocion,
            })
        elif not isinstance(primera, (int, float)):
            modo_tabla = False
    return aoi_log


def reconstruir_participant_data(wb):
    ws = wb["Perfil"]
    campos = {}
    for row in ws.iter_rows(min_row=4, max_row=16, values_only=True):
        if len(row) >= 4 and row[2] and row[3] is not None:
            campos[row[2]] = row[3]
    return {
        "nombre":    campos.get("Nombre y apellido", ""),
        "codigo":    campos.get("Código ECOTEC", ""),
        "edad":      campos.get("Edad", ""),
        "sexo":      campos.get("Sexo", ""),
        "facultad":  campos.get("Facultad", ""),
        "carrera":   campos.get("Carrera", ""),
        "semestre":  campos.get("Semestre", ""),
        "gafas":     campos.get("Usa gafas", ""),
        "lentes":    campos.get("Lentes de contacto", ""),
        "distancia": campos.get("Distancia (auto)", ""),
        "fecha":     campos.get("Fecha sesión", ""),
        "hora":      "",
    }


def reconstruir_heatmap_aproximado(sector_log, shape=(400, 640), rows=4, cols=4):
    """Aproximación: distribuye el tiempo por sector en su celda del grid
    y suaviza. No es idéntico al original (que usaba coordenadas
    continuas), pero es conceptualmente correcto."""
    h, w = shape
    heat = np.zeros((h, w), dtype=np.float32)
    cell_h, cell_w = h // rows, w // cols
    conteo = {}
    for e in sector_log:
        conteo[e["sector"]] = conteo.get(e["sector"], 0) + 1
    for sector_num, count in conteo.items():
        idx = sector_num - 1
        r, c = idx // cols, idx % cols
        y0, y1 = r * cell_h, (r + 1) * cell_h
        x0, x1 = c * cell_w, (c + 1) * cell_w
        heat[y0:y1, x0:x1] += count
    heat = cv2.GaussianBlur(heat, (81, 81), 0)
    return heat


def buscar_foto(pid):
    candidatos = glob.glob(os.path.join(DATA_DIR, f"foto_{pid}_*.jpg"))
    return candidatos[0] if candidatos else None


def buscar_excel_existente(pid):
    candidatos = glob.glob(os.path.join(DATA_DIR, f"*_{pid}.xlsx"))
    return candidatos[0] if candidatos else None


# ──────────────────────────────────────────────────────────────
#  3. Consolidado -- igual que el script anterior (solo columnas
#     afectadas, sin regenerar todo el archivo)
# ──────────────────────────────────────────────────────────────

def corregir_consolidado(metrics_por_id, log):
    if not os.path.exists(CONSOLIDADO_PATH):
        log.append(f"[AVISO] No se encontró {CONSOLIDADO_PATH}.")
        return
    wb = openpyxl.load_workbook(CONSOLIDADO_PATH)
    ws = wb["Resumen"]
    corregidas = 0
    for row in ws.iter_rows(min_row=3):
        pid = row[1].value  # columna B = ID
        if pid in metrics_por_id:
            m = metrics_por_id[pid]
            row[8].value  = round(m["engagement_promedio"] * 100, 1)   # I
            row[9].value  = round(m["engagement_maximo"] * 100, 1)     # J
            row[10].value = m["tiempo_mirando"]                        # K
            row[12].value = m["num_distracciones"]                     # M
            corregidas += 1
    wb.save(CONSOLIDADO_PATH)
    log.append(f"OK: consolidado -> {corregidas} filas corregidas.")


# ──────────────────────────────────────────────────────────────
#  4. Orquestación principal
# ──────────────────────────────────────────────────────────────

def main():
    log = []
    csvs = sorted(glob.glob(os.path.join(DATA_DIR, "participante_*.csv")))
    if not csvs:
        print(f"No se encontraron CSVs en ./{DATA_DIR}/.")
        return

    print(f"Encontrados {len(csvs)} CSVs. Regenerando reportes...\n")
    metrics_por_id = {}

    for csv_path in csvs:
        m = calcular_metricas_corregidas(csv_path)
        pid = m["id"]
        if not pid:
            log.append(f"[AVISO] {csv_path}: sin ID, se omite.")
            continue
        metrics_por_id[pid] = m

        excel_path = buscar_excel_existente(pid)
        if not excel_path:
            log.append(f"[AVISO] {pid}: no se encontró Excel existente, se omite.")
            continue

        try:
            wb_viejo = openpyxl.load_workbook(excel_path, data_only=True)
            sector_log = reconstruir_sector_log(wb_viejo)
            aoi_log = reconstruir_aoi_log(wb_viejo)
            participant_data = reconstruir_participant_data(wb_viejo)
            heatmap_arr = reconstruir_heatmap_aproximado(sector_log)
            photo_path = buscar_foto(pid)

            pdf_path = excel_path.replace(".xlsx", ".pdf")

            save_individual_excel(
                pid, participant_data, m, sector_log,
                heatmap_arr, photo_path, excel_path, aoi_log=aoi_log
            )
            save_individual_pdf(
                pid, participant_data, m, sector_log,
                heatmap_arr, photo_path, pdf_path, aoi_log=aoi_log
            )
            log.append(f"OK: {pid} -> Excel y PDF regenerados ({os.path.basename(excel_path)}).")
        except Exception as e:
            log.append(f"[ERROR] {pid}: {e}")

    corregir_consolidado(metrics_por_id, log)

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(log))

    print("\n".join(log))
    print(f"\nListo. Log completo en: {LOG_PATH}")
    print("\nRecuerda: el heatmap de atención es una reconstrucción")
    print("aproximada (ver docstring del script) -- todo lo demás es exacto.")


if __name__ == "__main__":
    main()
