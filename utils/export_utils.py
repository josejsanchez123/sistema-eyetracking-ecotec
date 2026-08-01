"""
export_utils.py
Funciones de exportación para el sistema de Eye Tracking ECOTEC 2026.
  - save_individual_excel()   → Excel por participante (6 hojas + gráficos)
  - save_individual_pdf()     → PDF por participante (reportlab)
  - save_consolidated_excel() → Excel acumulativo de los 30 participantes
"""

import os
import cv2
import numpy as np
import pandas as pd
import tempfile
from datetime import datetime

ROWS = 4
COLS = 4
POSITIVAS = {'happy', 'surprise'}
NEGATIVAS  = {'angry', 'sad', 'fear', 'disgust', 'contempt'}


# ─────────────────────────────────────────────────────────────────────────────
#  UTILIDADES INTERNAS
# ─────────────────────────────────────────────────────────────────────────────

def _cargar_aois_json():
    """Carga las AOIs desde el JSON de anotaciones del comercial."""
    import glob, json
    json_files = glob.glob('videos/*.json')
    if not json_files:
        return []
    try:
        with open(json_files[0], encoding='utf-8') as f:
            data = json.load(f)
        aois = []
        for aoi in data.get('aois', []):
            segs = [int(s) for s in aoi.get('segundos_cuadrantes', {}).keys()]
            aois.append({
                'id':       aoi['id'],
                'nombre':   aoi['nombre'],
                'tipo':     aoi.get('tipo', 'estatica'),
                'descripcion': aoi.get('descripcion', ''),
                'segundos': segs,
            })
        return aois
    except Exception as e:
        print(f"[AOI] Error cargando JSON: {e}")
        return []


def _calcular_metricas_aoi(aoi_id, aois_def, aoi_log, sector_log):
    """Calcula métricas de una AOI específica."""
    aoi_def   = next((a for a in aois_def if a['id'] == aoi_id), None)
    if not aoi_def:
        return {}
    entradas  = [e for e in aoi_log if e.get('aoi_id') == aoi_id]
    seg_disp  = len(aoi_def['segundos'])
    seg_mira  = len(entradas)
    pct       = round(seg_mira / seg_disp * 100, 1) if seg_disp > 0 else 0
    emo_dom   = max(set([e['emocion'] for e in entradas]),
                    key=[e['emocion'] for e in entradas].count) if entradas else '—'
    eng_prom  = round(sum(_eng_estimado(e['emocion']) for e in entradas) / len(entradas), 1) if entradas else 0
    primera   = min([e['segundo'] for e in entradas]) if entradas else None
    ultima    = max([e['segundo'] for e in entradas]) if entradas else None
    return {
        'nombre':    aoi_def['nombre'],
        'tipo':      aoi_def['tipo'],
        'desc':      aoi_def['descripcion'],
        'seg_inicio': aoi_def['segundos'][0] if aoi_def['segundos'] else 0,
        'seg_fin':    aoi_def['segundos'][-1] if aoi_def['segundos'] else 0,
        'seg_disp':  seg_disp,
        'seg_mira':  seg_mira,
        'pct':       pct,
        'emo_dom':   emo_dom,
        'eng_prom':  eng_prom,
        'primera_fij': primera,
        'ultima_fij':  ultima,
        'vio':       'Sí ✅' if entradas else 'No ❌',
        'entradas':  entradas,
    }

def _top_sector(sector_log):
    if not sector_log:
        return '—'
    df = pd.DataFrame(sector_log)
    return f"S{df['sector'].mode()[0]}"


def _eng_estimado(emocion):
    """Engagement estimado cuando is_looking=True."""
    pos = 1.0 if emocion in POSITIVAS else 0.0
    return round((0.6 + 0.4 * pos) * 100, 1)


def _heatmap_tmp(heatmap_arr):
    """Guarda el heatmap como PNG temporal y retorna la ruta."""
    if heatmap_arr is None or heatmap_arr.max() <= 0:
        return None
    hn = cv2.normalize(heatmap_arr, None, 0, 255, cv2.NORM_MINMAX)
    hc = cv2.applyColorMap(hn.astype(np.uint8), cv2.COLORMAP_JET)
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    tmp.close()
    cv2.imwrite(tmp.name, hc)
    return tmp.name


def _sector_map_tmp(sector_log):
    """Dibuja el mapa de sectores y retorna la ruta del PNG temporal."""
    if not sector_log:
        return None
    df_s  = pd.DataFrame(sector_log)
    simg  = np.zeros((240, 480, 3), dtype=np.uint8)
    simg[:] = (30, 30, 30)
    cw, ch = 480 // COLS, 240 // ROWS
    sc_map = df_s['sector'].value_counts()
    mx = sc_map.max() if len(sc_map) > 0 else 1
    for r in range(ROWS):
        for c in range(COLS):
            sn  = r * COLS + c + 1
            cnt = sc_map.get(sn, 0)
            x1, y1 = c*cw, r*ch
            x2, y2 = x1+cw-2, y1+ch-2
            cv2.rectangle(simg, (x1,y1), (x2,y2),
                          (0, int(cnt/mx*255), 255-int(cnt/mx*255)), -1)
            cv2.rectangle(simg, (x1,y1), (x2,y2), (255,255,255), 1)
            cv2.putText(simg, str(sn), (x1+6,y1+24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 1)
            if cnt > 0:
                cv2.putText(simg, f"{round(cnt/27,1)}s", (x1+6,y1+46),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255,255,0), 1)
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    tmp.close()
    cv2.imwrite(tmp.name, simg)
    return tmp.name


# ─────────────────────────────────────────────────────────────────────────────
#  EXCEL INDIVIDUAL — 6 HOJAS
# ─────────────────────────────────────────────────────────────────────────────

def save_individual_excel(pid, participant_data, summary, sector_log,
                          heatmap_arr, photo_path, filepath, aoi_log=None):
    """
    Genera el Excel individual con 6 hojas.

    participant_data: dict con {nombre, codigo, edad, sexo, facultad,
                                carrera, semestre, gafas, lentes,
                                distancia, fecha, hora}
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.chart import BarChart, PieChart, LineChart, Reference

    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else '.', exist_ok=True)

    thin = Side(style='thin', color='B8C0CC')
    brd  = Border(left=thin, right=thin, top=thin, bottom=thin)

    def hcell(ws, row, col, val, bg='002060', fg='FFFFFF', sz=10, bold=True, align='center'):
        c = ws.cell(row=row, column=col, value=val)
        c.font      = Font(bold=bold, color=fg, name='Arial', size=sz)
        c.fill      = PatternFill('solid', start_color=bg)
        c.alignment = Alignment(horizontal=align, vertical='center', wrap_text=True)
        c.border    = brd
        return c

    def dcell(ws, row, col, val, fmt=None, bold=False, bg=None,
              align='left', sz=10, fg='1A1A2E'):
        c = ws.cell(row=row, column=col, value=val)
        c.font      = Font(name='Arial', size=sz, bold=bold, color=fg)
        c.alignment = Alignment(horizontal=align, vertical='center', wrap_text=True)
        c.border    = brd
        if bg:  c.fill = PatternFill('solid', start_color=bg)
        if fmt: c.number_format = fmt
        return c

    wb = openpyxl.Workbook()
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']

    tmp_files = []

    # ══════════════════════════════════════════════════════════
    # HOJA 1 — PERFIL
    # ══════════════════════════════════════════════════════════
    ws1 = wb.create_sheet('Perfil')

    ws1.merge_cells('A1:F1')
    t = ws1['A1']
    t.value     = 'UNIVERSIDAD ECOTEC — Ficha del Participante'
    t.font      = Font(bold=True, color='FFFFFF', name='Arial', size=14)
    t.fill      = PatternFill('solid', start_color='002060')
    t.alignment = Alignment(horizontal='center', vertical='center')
    ws1.row_dimensions[1].height = 36

    ws1.merge_cells('A2:F2')
    t2 = ws1['A2']
    t2.value     = 'Ingeniería en Sistemas Inteligentes — Engagement Publicitario — Coca-Cola La Carta 2026 — Campus Samborondón'
    t2.font      = Font(color='FFFFFF', name='Arial', size=9)
    t2.fill      = PatternFill('solid', start_color='003087')
    t2.alignment = Alignment(horizontal='center', vertical='center')
    ws1.row_dimensions[2].height = 20

    # Foto
    if photo_path and os.path.exists(photo_path):
        try:
            img = XLImage(photo_path)
            img.width = 140; img.height = 140
            ws1.add_image(img, 'A4')
        except Exception:
            pass

    # Datos del participante
    datos = [
        ('ID',                 pid),
        ('Código ECOTEC',      participant_data.get('codigo', '—')),
        ('Nombre y apellido',  participant_data.get('nombre', '—')),
        ('Edad',               participant_data.get('edad', '—')),
        ('Sexo',               participant_data.get('sexo', '—')),
        ('Facultad',           participant_data.get('facultad', '—')),
        ('Carrera',            participant_data.get('carrera', '—')),
        ('Semestre',           participant_data.get('semestre', '—')),
        ('Usa gafas',          'Sí' if participant_data.get('gafas') else 'No'),
        ('Lentes de contacto', 'Sí' if participant_data.get('lentes') else 'No'),
        ('Distancia (auto)',   f"{participant_data.get('distancia', '—')} cm"),
        ('Fecha sesión',       participant_data.get('fecha', '—')),
        ('Hora sesión',        participant_data.get('hora', '—')),
        ('Campus',             'Samborondón'),
    ]
    for i, (label, val) in enumerate(datos, 4):
        c_l = ws1.cell(row=i, column=3, value=label)
        c_l.font   = Font(bold=True, name='Arial', size=10, color='002060')
        c_l.fill   = PatternFill('solid', start_color='D9E1F2')
        c_l.border = brd
        c_l.alignment = Alignment(vertical='center')
        ws1.merge_cells(f'D{i}:F{i}')
        c_v = ws1.cell(row=i, column=4, value=str(val))
        c_v.font   = Font(name='Arial', size=10)
        c_v.border = brd
        c_v.alignment = Alignment(vertical='center')

    # KPIs rápidos
    r_kpi = len(datos) + 5
    ws1.merge_cells(f'A{r_kpi}:F{r_kpi}')
    k = ws1[f'A{r_kpi}']
    k.value     = 'Resumen de Resultados'
    k.font      = Font(bold=True, color='FFFFFF', name='Arial', size=11)
    k.fill      = PatternFill('solid', start_color='00A651')
    k.alignment = Alignment(horizontal='center', vertical='center')
    ws1.row_dimensions[r_kpi].height = 26

    kpis = [
        ('Engagement\npromedio',  f"{summary.get('engagement_promedio',0)*100:.1f}%"),
        ('Engagement\nmáximo',    f"{summary.get('engagement_maximo',0)*100:.1f}%"),
        ('Emoción\ndominante',    summary.get('emocion_dominante','—')),
        ('Tiempo\nmirando',       f"{summary.get('tiempo_mirando',0):.1f}%"),
        ('Valencia\nemocional',   f"{summary.get('valencia_emocional',0):+.1f}%"),
        ('Sector\nmás visto',     _top_sector(sector_log)),
    ]
    for j, (label, val) in enumerate(kpis, 1):
        hcell(ws1, r_kpi+1, j, label, bg='D9E1F2', fg='002060', sz=9)
        ws1.row_dimensions[r_kpi+1].height = 32
        c = ws1.cell(row=r_kpi+2, column=j, value=val)
        c.font      = Font(bold=True, name='Arial', size=13, color='002060')
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = brd
        ws1.row_dimensions[r_kpi+2].height = 28

    for col, w in [('A',20),('B',4),('C',22),('D',18),('E',12),('F',12)]:
        ws1.column_dimensions[col].width = w
    ws1.freeze_panes = 'C4'

    # ══════════════════════════════════════════════════════════
    # HOJA 2 — MÉTRICAS
    # ══════════════════════════════════════════════════════════
    ws2 = wb.create_sheet('Métricas')

    ws2.merge_cells('A1:D1')
    t = ws2['A1']
    t.value     = f"Métricas Completas — {participant_data.get('nombre','—')} ({pid})"
    t.font      = Font(bold=True, color='FFFFFF', name='Arial', size=12)
    t.fill      = PatternFill('solid', start_color='002060')
    t.alignment = Alignment(horizontal='center', vertical='center')
    ws2.row_dimensions[1].height = 28

    segs = summary.get('engagement_segmentos', {})

    # Calcular primera fijación a cualquier AOI
    _aoi_log_m = aoi_log or []
    _primera_fij_aoi = f"{min(e['segundo'] for e in _aoi_log_m)}s" if _aoi_log_m else '—'

    secciones = [
        ('Engagement', '0057B8', 'D0E4F7', [
            ('Engagement promedio',     f"{summary.get('engagement_promedio',0)*100:.1f}%",  'Índice 60% atención + 40% emoción positiva'),
            ('Engagement máximo',       f"{summary.get('engagement_maximo',0)*100:.1f}%",   'Pico más alto durante el comercial'),
            ('Engagement mínimo',       f"{summary.get('engagement_minimo',0)*100:.1f}%",   'Valor más bajo registrado'),
            ('Desviación estándar',     f"{summary.get('engagement_std',0)*100:.1f}%",      'Variabilidad del engagement'),
            ('Segmento 1 (0–15s)',      f"{segs.get('segmento_1',0)*100:.1f}%",             'Introducción del comercial'),
            ('Segmento 2 (15–30s)',     f"{segs.get('segmento_2',0)*100:.1f}%",             'Desarrollo del comercial'),
            ('Segmento 3 (30–45s)',     f"{segs.get('segmento_3',0)*100:.1f}%",             'Clímax del comercial'),
            ('Segmento 4 (45–60s)',     f"{segs.get('segmento_4',0)*100:.1f}%",             'Cierre del comercial'),
        ]),
        ('Atención Visual', '00A651', 'D5EFDF', [
            ('Tiempo mirando pantalla',  f"{summary.get('tiempo_mirando',0):.1f}%",         'Del total del comercial'),
            ('Primera fijación al AOI', _primera_fij_aoi, '1er segundo en que miró el Logo Coca-Cola en cualquier AOI'),
            ('Número de distracciones',  str(summary.get('num_distracciones',0)),           'Veces que apartó la mirada de la pantalla'),
            ('Dur. prom. distracción',   f"{summary.get('dur_distraccion_prom',0):.2f}s",   'Duración promedio de cada distracción'),
        ]),
        ('Emociones', '7030A0', 'EDE0F5', [
            ('Emoción dominante',        summary.get('emocion_dominante','—'),              'Emoción más frecuente durante el comercial'),
            ('Valencia emocional',       f"{summary.get('valencia_emocional',0):+.1f}%",   'Positivas % − Negativas %'),
            ('Emociones positivas',      f"{summary.get('positivas_pct',0):.1f}%",         'Happy + Surprise'),
            ('Emociones negativas',      f"{summary.get('negativas_pct',0):.1f}%",         'Angry + Sad + Fear + Disgust + Contempt'),
            ('Variabilidad emocional',   f"{summary.get('variabilidad_emocional',0):.1f}/min", 'Cambios de emoción por minuto'),
        ]),
        ('Sistema', '888888', 'F2F2F2', [
            ('Total de frames',          str(summary.get('total_frames',0)),               'Frames analizados por el sistema'),
            ('FPS promedio',             f"{summary.get('fps_promedio',0):.1f}",           'Frames por segundo reales'),
            ('Tasa detección facial',    f"{summary.get('tasa_deteccion',0):.1f}%",       '% de frames con rostro detectado'),
            ('Fecha',                    summary.get('fecha','—'),                          ''),
            ('Hora',                     summary.get('hora','—'),                           ''),
        ]),
    ]

    row = 2
    for sec_name, color_h, color_f, metricas in secciones:
        ws2.merge_cells(f'A{row}:D{row}')
        sc = ws2[f'A{row}']
        sc.value     = sec_name
        sc.font      = Font(bold=True, color='FFFFFF', name='Arial', size=11)
        sc.fill      = PatternFill('solid', start_color=color_h)
        sc.alignment = Alignment(horizontal='left', vertical='center')
        sc.border    = brd
        ws2.row_dimensions[row].height = 24
        row += 1

        for j, h in enumerate(['Métrica', 'Valor', 'Descripción'], 1):
            hcell(ws2, row, j, h, bg=color_h, sz=9)
        ws2.row_dimensions[row].height = 22
        row += 1

        for met_n, met_v, met_d in metricas:
            bg = color_f if row % 2 == 0 else 'FFFFFF'
            dcell(ws2, row, 1, met_n, bold=True, bg=bg)
            dcell(ws2, row, 2, met_v, align='center', bg=bg, bold=True)
            dcell(ws2, row, 3, met_d, bg=bg)
            ws2.row_dimensions[row].height = 20
            row += 1
        row += 1

    for col, w in [('A',28),('B',18),('C',48)]:
        ws2.column_dimensions[col].width = w

    # ══════════════════════════════════════════════════════════
    # HOJA 3 — ATENCIÓN VISUAL
    # ══════════════════════════════════════════════════════════
    ws3 = wb.create_sheet('Atención visual')

    ws3.merge_cells('A1:F1')
    t = ws3['A1']
    t.value     = 'Atención Visual — Distribución por Sector'
    t.font      = Font(bold=True, color='FFFFFF', name='Arial', size=12)
    t.fill      = PatternFill('solid', start_color='0057B8')
    t.alignment = Alignment(horizontal='center', vertical='center')
    ws3.row_dimensions[1].height = 28

    for j, h in enumerate(['Sector','Fila (1-4)','Columna (1-4)','Segundos','% del total','Barra visual'], 1):
        hcell(ws3, 2, j, h, bg='0057B8', sz=9)
    ws3.row_dimensions[2].height = 26
    ws3.freeze_panes = 'A3'

    if sector_log:
        df_s  = pd.DataFrame(sector_log)
        sc_vc = df_s['sector'].value_counts().sort_values(ascending=False)
        tot   = len(df_s)
        for i, (sec, cnt) in enumerate(sc_vc.items(), 3):
            pct  = cnt/tot*100 if tot > 0 else 0
            fila = ((int(sec)-1)//4)+1
            col  = ((int(sec)-1)%4)+1
            bg   = 'E0EDF8' if i % 2 == 0 else 'FFFFFF'
            dcell(ws3, i, 1, f'S{int(sec)}', bold=True, align='center', bg=bg)
            dcell(ws3, i, 2, fila, align='center', bg=bg)
            dcell(ws3, i, 3, col,  align='center', bg=bg)
            dcell(ws3, i, 4, round(cnt/27, 1), align='right', bg=bg, fmt='0.0')
            dcell(ws3, i, 5, round(pct, 1),    align='right', bg=bg, fmt='0.0"%"')
            dcell(ws3, i, 6, '█' * max(1, int(pct/4)), bg=bg, fg='0057B8')
            ws3.row_dimensions[i].height = 20

        # Gráfico de barras nativo
        bar = BarChart()
        bar.type    = "col"
        bar.title   = "Atención por Sector (segundos)"
        bar.style   = 10
        bar.y_axis.title = "Segundos mirados"
        bar.x_axis.title = "Sector"
        num_s = len(sc_vc)
        data_ref   = Reference(ws3, min_col=4, min_row=2, max_row=2+num_s)
        labels_ref = Reference(ws3, min_col=1, min_row=3, max_row=2+num_s)
        bar.add_data(data_ref, titles_from_data=True)
        bar.set_categories(labels_ref)
        bar.width = 16; bar.height = 12
        ws3.add_chart(bar, "H2")

    # Heatmap
    hm_path = _heatmap_tmp(heatmap_arr)
    if hm_path:
        tmp_files.append(hm_path)
        try:
            hm_img = XLImage(hm_path)
            hm_img.width = 320; hm_img.height = 200
            lbl = ws3.cell(row=22, column=8, value='Mapa de Calor (grid 4×4)')
            lbl.font = Font(bold=True, color='0057B8', name='Arial', size=10)
            ws3.add_image(hm_img, 'H23')
        except Exception as e:
            print(f"Heatmap en Excel: {e}")

    for col, w in [('A',12),('B',12),('C',14),('D',16),('E',14),('F',22)]:
        ws3.column_dimensions[col].width = w

    # ══════════════════════════════════════════════════════════
    # HOJA 4 — EMOCIONES
    # ══════════════════════════════════════════════════════════
    ws4 = wb.create_sheet('Emociones')

    ws4.merge_cells('A1:E1')
    t = ws4['A1']
    t.value     = 'Distribución Emocional'
    t.font      = Font(bold=True, color='FFFFFF', name='Arial', size=12)
    t.fill      = PatternFill('solid', start_color='7030A0')
    t.alignment = Alignment(horizontal='center', vertical='center')
    ws4.row_dimensions[1].height = 28

    for j, h in enumerate(['Emoción','Segundos','% del total','Categoría','Barra visual'], 1):
        hcell(ws4, 2, j, h, bg='7030A0', sz=9)
    ws4.row_dimensions[2].height = 26
    ws4.freeze_panes = 'A3'

    if sector_log:
        df_e = pd.DataFrame(sector_log)
        ec   = df_e['emocion'].value_counts()
        tot  = len(df_e)
        for i, (emo, cnt) in enumerate(ec.items(), 3):
            pct = cnt/tot*100 if tot > 0 else 0
            cat = 'Positiva' if emo in POSITIVAS else ('Negativa' if emo in NEGATIVAS else 'Neutral')
            bg  = 'F3E5F5' if i % 2 == 0 else 'FFFFFF'
            dcell(ws4, i, 1, emo, bold=True, bg=bg)
            dcell(ws4, i, 2, round(cnt/27,1), align='right', bg=bg, fmt='0.0')
            dcell(ws4, i, 3, round(pct,1),    align='right', bg=bg, fmt='0.0"%"')
            dcell(ws4, i, 4, cat, align='center', bg=bg)
            dcell(ws4, i, 5, '█' * max(1,int(pct/4)), bg=bg, fg='7030A0')
            ws4.row_dimensions[i].height = 20

        # Gráfico de pastel
        pie = PieChart()
        pie.title  = "Distribución Emocional"
        pie.style  = 10
        num_e    = len(ec)
        data_ref = Reference(ws4, min_col=2, min_row=2, max_row=2+num_e)
        lbl_ref  = Reference(ws4, min_col=1, min_row=3, max_row=2+num_e)
        pie.add_data(data_ref, titles_from_data=True)
        pie.set_categories(lbl_ref)
        pie.width = 14; pie.height = 12
        ws4.add_chart(pie, "G2")

    for col, w in [('A',16),('B',14),('C',14),('D',14),('E',22)]:
        ws4.column_dimensions[col].width = w

    # ══════════════════════════════════════════════════════════
    # HOJA 5 — ENGAGEMENT
    # ══════════════════════════════════════════════════════════
    ws5 = wb.create_sheet('Engagement')

    ws5.merge_cells('A1:D1')
    t = ws5['A1']
    t.value     = 'Engagement — Análisis Temporal por Segmento'
    t.font      = Font(bold=True, color='FFFFFF', name='Arial', size=12)
    t.fill      = PatternFill('solid', start_color='00A651')
    t.alignment = Alignment(horizontal='center', vertical='center')
    ws5.row_dimensions[1].height = 28

    # Segmentos
    segs = summary.get('engagement_segmentos', {})
    seg_names = ['Seg. 1 (0–15s)\nIntroducción',
                 'Seg. 2 (15–30s)\nDesarrollo',
                 'Seg. 3 (30–45s)\nClímax',
                 'Seg. 4 (45–60s)\nCierre']
    seg_keys  = ['segmento_1','segmento_2','segmento_3','segmento_4']

    ws5.merge_cells('A2:D2')
    ws5['A2'].value     = 'Engagement por Segmento del Comercial'
    ws5['A2'].font      = Font(bold=True, color='002060', name='Arial', size=11)
    ws5['A2'].alignment = Alignment(horizontal='left', vertical='center')
    ws5.row_dimensions[2].height = 22

    for j, name in enumerate(seg_names, 1):
        hcell(ws5, 3, j, name, bg='00A651', sz=9)
        ws5.row_dimensions[3].height = 36
    for j, key in enumerate(seg_keys, 1):
        val = segs.get(key, 0) * 100
        c = ws5.cell(row=4, column=j, value=round(val,1))
        c.font      = Font(bold=True, name='Arial', size=16, color='002060')
        c.number_format = '0.0"%"'
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = brd
        ws5.row_dimensions[4].height = 32

    # Tabla de engagement segundo a segundo
    ws5.merge_cells('A6:C6')
    ws5['A6'].value     = 'Engagement Segundo a Segundo'
    ws5['A6'].font      = Font(bold=True, color='002060', name='Arial', size=11)
    ws5['A6'].alignment = Alignment(horizontal='left', vertical='center')
    ws5.row_dimensions[6].height = 22

    for j, h in enumerate(['Segundo','Sector','Engagement est. (%)'], 1):
        hcell(ws5, 7, j, h, bg='00A651', sz=9)
    ws5.row_dimensions[7].height = 24

    if sector_log:
        for i, entry in enumerate(sector_log, 8):
            eng = _eng_estimado(entry.get('emocion','neutral'))
            bg  = 'E2EFDA' if i % 2 == 0 else 'FFFFFF'
            dcell(ws5, i, 1, entry.get('tiempo_seg', i-8), align='center', bg=bg, fmt='0"s"')
            dcell(ws5, i, 2, f"S{entry.get('sector','—')}", align='center', bg=bg)
            dcell(ws5, i, 3, eng, align='right', bg=bg, fmt='0.0"%"')
            ws5.row_dimensions[i].height = 18

        # Gráfico de línea
        line = LineChart()
        line.title  = "Curva de Engagement"
        line.style  = 10
        line.y_axis.title   = "Engagement (%)"
        line.x_axis.title   = "Segundo"
        line.y_axis.numFmt  = '0"%"'
        line.y_axis.scaling.min = 0
        line.y_axis.scaling.max = 100
        num_r   = len(sector_log)
        data_r  = Reference(ws5, min_col=3, min_row=7, max_row=7+num_r)
        line.add_data(data_r, titles_from_data=True)
        line.width = 22; line.height = 14
        ws5.add_chart(line, 'E6')

    for col, w in [('A',14),('B',12),('C',22)]:
        ws5.column_dimensions[col].width = w

    # ══════════════════════════════════════════════════════════
    # HOJA 6 — REGISTRO
    # ══════════════════════════════════════════════════════════
    ws6 = wb.create_sheet('Registro')

    ws6.merge_cells('A1:F1')
    t = ws6['A1']
    t.value     = 'Registro Segundo a Segundo — Datos Completos'
    t.font      = Font(bold=True, color='FFFFFF', name='Arial', size=12)
    t.fill      = PatternFill('solid', start_color='002060')
    t.alignment = Alignment(horizontal='center', vertical='center')
    ws6.row_dimensions[1].height = 28

    for j, h in enumerate(['Segundo','Sector','Emoción','Categoría','Engagement est. (%)','Mirando'], 1):
        hcell(ws6, 2, j, h, bg='002060', sz=9)
    ws6.row_dimensions[2].height = 26
    ws6.freeze_panes = 'A3'

    if sector_log:
        for i, entry in enumerate(sector_log, 3):
            emo = entry.get('emocion','neutral')
            cat = 'Positiva' if emo in POSITIVAS else ('Negativa' if emo in NEGATIVAS else 'Neutral')
            eng = _eng_estimado(emo)
            bg  = 'F0F4F8' if i % 2 == 0 else 'FFFFFF'
            dcell(ws6, i, 1, entry.get('tiempo_seg', i-3), align='center', bg=bg, fmt='0"s"')
            dcell(ws6, i, 2, f"S{entry.get('sector','—')}", align='center', bg=bg)
            dcell(ws6, i, 3, emo, bg=bg)
            dcell(ws6, i, 4, cat, align='center', bg=bg)
            dcell(ws6, i, 5, eng, align='right', bg=bg, fmt='0.0"%"')
            dcell(ws6, i, 6, 'Sí', align='center', bg=bg)
            ws6.row_dimensions[i].height = 18

    for col, w in [('A',12),('B',12),('C',14),('D',14),('E',20),('F',12)]:
        ws6.column_dimensions[col].width = w

    # ══════════════════════════════════════════════════════════
    # HOJA 7 — LÍNEA DE TIEMPO EMOCIONAL (GANTT)
    # ══════════════════════════════════════════════════════════
    ws7 = wb.create_sheet('Línea de Tiempo')

    ws7.merge_cells('A1:BH1')
    t7 = ws7['A1']
    t7.value     = 'Línea de Tiempo Emocional — Emociones vs Tiempo (segundo a segundo)'
    t7.font      = Font(bold=True, color='FFFFFF', name='Arial', size=12)
    t7.fill      = PatternFill('solid', start_color='002060')
    t7.alignment = Alignment(horizontal='center', vertical='center')
    ws7.row_dimensions[1].height = 28

    # ── Definición de colores por emoción ─────────────────────
    color_emo_gantt = {
        'happy':    ('1BAF7A', 'E1F5EE'),
        'surprise': ('EDA100', 'FAEEDA'),
        'neutral':  ('2A78D6', 'E6F1FB'),
        'sad':      ('E34948', 'FCEBEB'),
        'fear':     ('4A3AA7', 'EEEDFE'),
        'angry':    ('888780', 'F1EFE8'),
        'disgust':  ('008300', 'EAF3DE'),
    }

    # ── Cargar anotaciones ───────────────────────────────────
    import glob as _glob, json as _json
    anotaciones = {}
    json_files = _glob.glob('videos/*.json')
    if json_files:
        try:
            with open(json_files[0], encoding='utf-8') as jf:
                data_json = _json.load(jf)
                for item in data_json.get('anotaciones', []):
                    anotaciones[item['segundo']] = item.get('escena', '—')
        except Exception as e:
            print(f"Error cargando anotaciones: {e}")

    if sector_log:
        total_segs = max(e.get('tiempo_seg', 0) for e in sector_log) + 1
        total_segs = max(total_segs, 1)

        # ── Sección 1: Gráfico Gantt ─────────────────────────
        ws7.merge_cells('A2:BH2')
        ws7['A2'].value     = 'Diagrama Gantt — Bloques de emoción por tiempo'
        ws7['A2'].font      = Font(bold=True, color='002060', name='Arial', size=10)
        ws7['A2'].alignment = Alignment(horizontal='left', vertical='center')
        ws7.row_dimensions[2].height = 22

        # Columna A = etiqueta emoción, columnas B en adelante = segundos (1 col = 1 segundo)
        # Ajustar ancho de columnas de tiempo
        ws7.column_dimensions['A'].width = 14
        for col_idx in range(2, total_segs + 2):
            col_letter = get_column_letter(col_idx)
            ws7.column_dimensions[col_letter].width = 1.8

        # Fila de encabezado de segundos
        hcell(ws7, 3, 1, 'Emoción', bg='002060', sz=8)
        for seg_i in range(total_segs):
            col_i = seg_i + 2
            if seg_i % 5 == 0:
                c = ws7.cell(row=3, column=col_i)
                c.value     = f'{seg_i}s'
                c.font      = Font(bold=False, name='Arial', size=7, color='444441')
                c.alignment = Alignment(horizontal='center')
        ws7.row_dimensions[3].height = 14

        # Obtener emociones presentes en el log
        emociones_orden = ['neutral','happy','surprise','sad','fear','angry','disgust']
        emociones_presentes = []
        for emo in emociones_orden:
            if any(e.get('emocion') == emo for e in sector_log):
                emociones_presentes.append(emo)

        # Dibujar fila por emoción
        for fila_emo, emo in enumerate(emociones_presentes, 4):
            color_hex, bg_hex = color_emo_gantt.get(emo, ('888780', 'F1EFE8'))

            # Etiqueta
            c_lbl = ws7.cell(row=fila_emo, column=1)
            c_lbl.value     = emo
            c_lbl.font      = Font(bold=True, name='Arial', size=9, color=color_hex)
            c_lbl.alignment = Alignment(horizontal='right', vertical='center')
            ws7.row_dimensions[fila_emo].height = 16

            # Pintar celdas donde esta emoción está activa
            for entry in sector_log:
                seg_t = entry.get('tiempo_seg', 0)
                emo_t = entry.get('emocion', 'neutral')
                if emo_t == emo and seg_t < total_segs:
                    col_i = seg_t + 2
                    c = ws7.cell(row=fila_emo, column=col_i)
                    c.fill      = PatternFill('solid', start_color=color_hex)
                    c.value     = ''

        # Fila de eje X al final del Gantt
        fila_eje = 4 + len(emociones_presentes)
        for seg_i in range(0, total_segs, 5):
            col_i = seg_i + 2
            c = ws7.cell(row=fila_eje, column=col_i)
            c.value     = f'{seg_i}s'
            c.font      = Font(name='Arial', size=7, color='898781')
            c.alignment = Alignment(horizontal='center')
        ws7.row_dimensions[fila_eje].height = 12

        # ── Sección 2: Tabla segundo a segundo ───────────────
        fila_tabla = fila_eje + 3
        ws7.merge_cells(f'A{fila_tabla}:G{fila_tabla}')
        ws7[f'A{fila_tabla}'].value     = 'Detalle segundo a segundo'
        ws7[f'A{fila_tabla}'].font      = Font(bold=True, color='002060', name='Arial', size=10)
        ws7[f'A{fila_tabla}'].alignment = Alignment(horizontal='left', vertical='center')
        ws7.row_dimensions[fila_tabla].height = 22

        fila_cab = fila_tabla + 1
        for j, h in enumerate(['Segundo','Escena del Comercial','Emoción','Categoría','Engagement est. (%)','Mirando'], 1):
            hcell(ws7, fila_cab, j, h, bg='002060', sz=9)
        ws7.row_dimensions[fila_cab].height = 24

        for i, entry in enumerate(sector_log, fila_cab + 1):
            seg = entry.get('tiempo_seg', 0)
            emo = entry.get('emocion', 'neutral')
            cat = 'Positiva' if emo in POSITIVAS else ('Negativa' if emo in NEGATIVAS else 'Neutral')
            eng = _eng_estimado(emo)
            escena = anotaciones.get(int(seg), '—')
            color_hex, bg_hex = color_emo_gantt.get(emo, ('888780', 'F1EFE8'))

            dcell(ws7, i, 1, seg,    align='center', bg=bg_hex, fmt='0"s"')
            dcell(ws7, i, 2, escena, align='left',   bg=bg_hex)
            dcell(ws7, i, 3, emo,    align='center', bg=bg_hex)
            dcell(ws7, i, 4, cat,    align='center', bg=bg_hex)
            dcell(ws7, i, 5, eng,    align='right',  bg=bg_hex, fmt='0.0"%"')
            dcell(ws7, i, 6, 'Sí',   align='center', bg=bg_hex)
            ws7.row_dimensions[i].height = 16

        # Anchos columnas tabla
        for col, w in [('A',10),('B',40),('C',14),('D',12),('E',18),('F',12)]:
            ws7.column_dimensions[col].width = w

    # ══════════════════════════════════════════════════════════
    # HOJA 8 — REPORTE DE ÁREAS DE INTERÉS (AOI)
    # ══════════════════════════════════════════════════════════
    if aoi_log is None:
        aoi_log = []

    ws8 = wb.create_sheet('AOI')

    # Título
    ws8.merge_cells('A1:H1')
    t8 = ws8['A1']
    t8.value     = 'Reporte de Áreas de Interés (AOI) — Logo Coca-Cola'
    t8.font      = Font(bold=True, color='FFFFFF', name='Arial', size=12)
    t8.fill      = PatternFill('solid', start_color='C00000')
    t8.alignment = Alignment(horizontal='center', vertical='center')
    ws8.row_dimensions[1].height = 28

    # Cargar AOIs desde JSON
    aois_def = _cargar_aois_json()
    if not aois_def:
        # Fallback si no hay JSON
        aois_def = [
            {'id':'aoi_logo_camion',  'nombre':'AOI 1 — Logo en camión lateral',  'tipo':'dinamica',  'segundos':list(range(41,51))},
            {'id':'aoi_logo_familia', 'nombre':'AOI 2 — Logo de fondo con familia','tipo':'dinamica',  'segundos':list(range(51,58))},
            {'id':'aoi_logo_estatico','nombre':'AOI 3 — Logo estático final',      'tipo':'estatica',  'segundos':[58,59]},
        ]

    colores_aoi = ['002060','C00000','006400']
    bg_aoi      = ['D0E4F7','FFE2E2','E2EFDA']

    fila_actual = 2

    for idx_aoi, aoi_def in enumerate(aois_def):
        met = _calcular_metricas_aoi(aoi_def['id'], aois_def, aoi_log, sector_log)
        c_hdr = colores_aoi[idx_aoi % 3]
        c_bg  = bg_aoi[idx_aoi % 3]

        # Encabezado de AOI
        ws8.merge_cells(f'A{fila_actual}:H{fila_actual}')
        c = ws8[f'A{fila_actual}']
        tipo_txt = '(Dinámica)' if aoi_def.get('tipo') == 'dinamica' else '(Estática)'
        c.value     = f"{aoi_def['nombre']} {tipo_txt} — Segundos {aoi_def['segundos'][0]} al {aoi_def['segundos'][-1]}"
        c.font      = Font(bold=True, color='FFFFFF', name='Arial', size=10)
        c.fill      = PatternFill('solid', start_color=c_hdr)
        c.alignment = Alignment(horizontal='left', vertical='center')
        ws8.row_dimensions[fila_actual].height = 24
        fila_actual += 1

        # Descripción
        ws8.merge_cells(f'A{fila_actual}:H{fila_actual}')
        cd = ws8[f'A{fila_actual}']
        cd.value     = f"📍 {aoi_def.get('descripcion','')}"
        cd.font      = Font(italic=True, name='Arial', size=8, color='444441')
        cd.alignment = Alignment(horizontal='left', vertical='center')
        ws8.row_dimensions[fila_actual].height = 16
        fila_actual += 1

        # Métricas resumen
        for j, h in enumerate(['Métrica','Valor','Métrica','Valor','Métrica','Valor'], 1):
            hcell(ws8, fila_actual, j, h, bg=c_hdr, sz=8)
        ws8.row_dimensions[fila_actual].height = 20
        fila_actual += 1

        met_rows = [
            ('¿Vio el AOI?',       met.get('vio','—'),
             'Segundos disponibles', f"{met.get('seg_disp',0)}s",
             'Segundos mirando',    f"{met.get('seg_mira',0)}s"),
            ('% tiempo disponible', f"{met.get('pct',0)}%",
             'Emoción dominante',   met.get('emo_dom','—'),
             'Engagement promedio', f"{met.get('eng_prom',0)}%"),
            ('Primera fijación',   f"{met.get('primera_fij','—')}s" if met.get('primera_fij') is not None else '—',
             'Última fijación',    f"{met.get('ultima_fij','—')}s" if met.get('ultima_fij') is not None else '—',
             'Inicio AOI',         f"{met.get('seg_inicio',0)}s"),
        ]
        for r_idx, row_data in enumerate(met_rows):
            bg = c_bg if r_idx % 2 == 0 else 'FFFFFF'
            for j, val in enumerate(row_data, 1):
                bold_col = j % 2 != 0
                dcell(ws8, fila_actual, j, val, bold=bold_col, bg=bg)
            ws8.row_dimensions[fila_actual].height = 18
            fila_actual += 1

        # Detalle segundo a segundo de esta AOI
        entradas_aoi = met.get('entradas', [])
        if entradas_aoi:
            for j, h in enumerate(['Segundo','Sector','Emoción','Categoría','Eng. est.(%)'], 1):
                hcell(ws8, fila_actual, j, h, bg=c_hdr, sz=8)
            ws8.row_dimensions[fila_actual].height = 18
            fila_actual += 1

            for entry in entradas_aoi:
                emo = entry.get('emocion','neutral')
                cat = 'Positiva' if emo in POSITIVAS else ('Negativa' if emo in NEGATIVAS else 'Neutral')
                eng = _eng_estimado(emo)
                dcell(ws8, fila_actual, 1, entry.get('segundo',0), align='center', bg=c_bg, fmt='0"s"')
                dcell(ws8, fila_actual, 2, f"S{entry.get('sector','—')}", align='center', bg=c_bg)
                dcell(ws8, fila_actual, 3, emo, align='center', bg=c_bg)
                dcell(ws8, fila_actual, 4, cat, align='center', bg=c_bg)
                dcell(ws8, fila_actual, 5, eng, align='right',  bg=c_bg, fmt='0.0"%"')
                ws8.row_dimensions[fila_actual].height = 15
                fila_actual += 1
        else:
            ws8.merge_cells(f'A{fila_actual}:H{fila_actual}')
            ws8[f'A{fila_actual}'].value     = f'⚠ El participante no miró esta AOI durante los segundos disponibles.'
            ws8[f'A{fila_actual}'].font      = Font(italic=True, color='C00000', name='Arial', size=8)
            ws8[f'A{fila_actual}'].fill      = PatternFill('solid', start_color='FFE2E2')
            ws8[f'A{fila_actual}'].alignment = Alignment(horizontal='center')
            ws8.row_dimensions[fila_actual].height = 18
            fila_actual += 1

        fila_actual += 1  # Espacio entre AOIs

    for col, w in [('A',28),('B',14),('C',16),('D',16),('E',16),('F',18),('G',12),('H',12)]:
        ws8.column_dimensions[col].width = w

    # Reordenar hojas
    for idx, name in enumerate(['Perfil','Métricas','Atención visual','Emociones','Engagement','Registro','Línea de Tiempo','AOI']):
        if name in wb.sheetnames:
            wb.move_sheet(name, offset=wb.sheetnames.index(name)-idx)

    wb.save(filepath)

    for tf in tmp_files:
        try: os.unlink(tf)
        except: pass

    return filepath


# ─────────────────────────────────────────────────────────────────────────────
#  PDF INDIVIDUAL
# ─────────────────────────────────────────────────────────────────────────────

def save_individual_pdf(pid, participant_data, summary, sector_log,
                        heatmap_arr, photo_path, filepath, aoi_log=None):
    """Genera el PDF individual usando reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Table,
                                     TableStyle, Spacer, Image as RLImage,
                                     HRFlowable, PageBreak)
    from reportlab.lib.styles  import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.colors  import HexColor, white, black
    from reportlab.lib.units   import cm
    from reportlab.lib.enums   import TA_CENTER, TA_LEFT, TA_RIGHT

    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else '.', exist_ok=True)

    # ── colores ──────────────────────────────────────────────
    AZUL   = HexColor('#002060')
    AZUL2  = HexColor('#0057B8')
    VERDE  = HexColor('#00A651')
    GRIS   = HexColor('#F0F4F8')
    GRIS2  = HexColor('#D0D8E4')
    MORADO = HexColor('#7030A0')

    doc = SimpleDocTemplate(
        filepath, pagesize=A4,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        leftMargin=2*cm,  rightMargin=2*cm
    )

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle('title',
        fontName='Helvetica-Bold', fontSize=16, textColor=white,
        alignment=TA_CENTER, leading=20)
    style_sec = ParagraphStyle('sec',
        fontName='Helvetica-Bold', fontSize=11, textColor=AZUL,
        spaceBefore=10, spaceAfter=4)
    style_body = ParagraphStyle('body',
        fontName='Helvetica', fontSize=9, textColor=black,
        leading=13)
    style_small = ParagraphStyle('small',
        fontName='Helvetica', fontSize=8, textColor=HexColor('#555555'),
        alignment=TA_CENTER)

    story = []
    tmp_files = []

    # ── Encabezado ───────────────────────────────────────────
    header_data = [
        [Paragraph('<font color="white"><b>UNIVERSIDAD ECOTEC</b><br/>'
                   'Ingeniería en Sistemas Inteligentes<br/>'
                   'Medición de Engagement Publicitario — Coca-Cola La Carta 2026</font>', style_title)]
    ]
    header_tbl = Table(header_data, colWidths=[17*cm])
    header_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), AZUL),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [AZUL]),
        ('TOPPADDING',    (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 0.4*cm))

    # ── Foto + datos del participante ────────────────────────
    foto_cell = ''
    if photo_path and os.path.exists(photo_path):
        try:
            foto_cell = RLImage(photo_path, width=3.5*cm, height=3.5*cm)
        except Exception:
            foto_cell = ''

    datos_lines = [
        f"<b>ID:</b> {pid}",
        f"<b>Código ECOTEC:</b> {participant_data.get('codigo','—')}",
        f"<b>Nombre:</b> {participant_data.get('nombre','—')}",
        f"<b>Edad:</b> {participant_data.get('edad','—')} | <b>Sexo:</b> {participant_data.get('sexo','—')}",
        f"<b>Facultad:</b> {participant_data.get('facultad','—')}",
        f"<b>Carrera:</b> {participant_data.get('carrera','—')}",
        f"<b>Semestre:</b> {participant_data.get('semestre','—')}",
        f"<b>Gafas:</b> {'Sí' if participant_data.get('gafas') else 'No'} | "
        f"<b>Lentes:</b> {'Sí' if participant_data.get('lentes') else 'No'}",
        f"<b>Distancia:</b> {participant_data.get('distancia','—')} cm (auto)",
        f"<b>Fecha:</b> {participant_data.get('fecha','—')} &nbsp; <b>Hora:</b> {participant_data.get('hora','—')}",
    ]
    datos_paragraph = Paragraph('<br/>'.join(datos_lines), style_body)

    perfil_data = [[foto_cell, datos_paragraph]]
    perfil_tbl  = Table(perfil_data, colWidths=[4*cm, 13*cm])
    perfil_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), GRIS),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING',(0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING',(0,0),(-1,-1), 8),
        ('ROUNDEDCORNERS', (0,0), (-1,-1), 4),
    ]))
    story.append(perfil_tbl)
    story.append(Spacer(1, 0.4*cm))
    story.append(HRFlowable(width='100%', thickness=1.5, color=AZUL2))
    story.append(Spacer(1, 0.3*cm))

    # ── Métricas generales ───────────────────────────────────
    story.append(Paragraph('Métricas Generales', style_sec))

    segs = summary.get('engagement_segmentos', {})
    _aoi_log_pdf_m = aoi_log or []
    _primera_fij_aoi_pdf = f"{min(e['segundo'] for e in _aoi_log_pdf_m)}s" if _aoi_log_pdf_m else '—'

    met_data = [
        ['Métrica', 'Valor', 'Métrica', 'Valor'],
        ['Engagement promedio',     f"{summary.get('engagement_promedio',0)*100:.1f}%",
         'Engagement máximo',       f"{summary.get('engagement_maximo',0)*100:.1f}%"],
        ['Tiempo mirando pantalla', f"{summary.get('tiempo_mirando',0):.1f}%",
         'Primera fijación al AOI',  _primera_fij_aoi_pdf],
        ['Emoción dominante',       summary.get('emocion_dominante','—'),
         'Valencia emocional',      f"{summary.get('valencia_emocional',0):+.1f}%"],
        ['Distracciones',           str(summary.get('num_distracciones',0)),
         'Variabilidad emocional',  f"{summary.get('variabilidad_emocional',0):.1f}/min"],
        ['Seg. 1 (0–15s)',          f"{segs.get('segmento_1',0)*100:.1f}%",
         'Seg. 2 (15–30s)',         f"{segs.get('segmento_2',0)*100:.1f}%"],
        ['Seg. 3 (30–45s)',         f"{segs.get('segmento_3',0)*100:.1f}%",
         'Seg. 4 (45–60s)',         f"{segs.get('segmento_4',0)*100:.1f}%"],
    ]
    met_tbl = Table(met_data, colWidths=[5.5*cm, 3*cm, 5.5*cm, 3*cm])
    met_style = [
        ('BACKGROUND',  (0,0), (-1,0),  AZUL2),
        ('TEXTCOLOR',   (0,0), (-1,0),  white),
        ('FONTNAME',    (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',    (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [GRIS, white]),
        ('FONTNAME',    (0,1), (-1,-1), 'Helvetica'),
        ('FONTNAME',    (0,1), (0,-1),  'Helvetica-Bold'),
        ('FONTNAME',    (2,1), (2,-1),  'Helvetica-Bold'),
        ('ALIGN',       (1,1), (1,-1),  'CENTER'),
        ('ALIGN',       (3,1), (3,-1),  'CENTER'),
        ('TEXTCOLOR',   (1,1), (1,-1),  AZUL),
        ('TEXTCOLOR',   (3,1), (3,-1),  AZUL),
        ('FONTNAME',    (1,1), (1,-1),  'Helvetica-Bold'),
        ('FONTNAME',    (3,1), (3,-1),  'Helvetica-Bold'),
        ('GRID',        (0,0), (-1,-1), 0.5, GRIS2),
        ('TOPPADDING',  (0,0), (-1,-1), 4),
        ('BOTTOMPADDING',(0,0),(-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
    ]
    met_tbl.setStyle(TableStyle(met_style))
    story.append(met_tbl)
    story.append(Spacer(1, 0.4*cm))

    # ── Mapas visuales (heatmap + sectores) ──────────────────
    story.append(Paragraph('Atención Visual', style_sec))

    hm_path  = _heatmap_tmp(heatmap_arr)
    sm_path  = _sector_map_tmp(sector_log)
    if hm_path:  tmp_files.append(hm_path)
    if sm_path:  tmp_files.append(sm_path)

    map_cells = []
    map_labels = []
    if hm_path:
        map_cells.append(RLImage(hm_path, width=7.5*cm, height=4.5*cm))
        map_labels.append(Paragraph('Mapa de Calor', style_small))
    else:
        map_cells.append('')
        map_labels.append('')
    if sm_path:
        map_cells.append(RLImage(sm_path, width=7.5*cm, height=4.5*cm))
        map_labels.append(Paragraph('Mapa de Sectores', style_small))
    else:
        map_cells.append('')
        map_labels.append('')

    if any(map_cells):
        maps_tbl = Table([map_cells, map_labels], colWidths=[8.5*cm, 8.5*cm])
        maps_tbl.setStyle(TableStyle([
            ('ALIGN',   (0,0), (-1,-1), 'CENTER'),
            ('VALIGN',  (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING',    (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(maps_tbl)

    story.append(Spacer(1, 0.3*cm))

    # ── Distribución emocional ───────────────────────────────
    if sector_log:
        story.append(Paragraph('Distribución Emocional', style_sec))
        df_e    = pd.DataFrame(sector_log)
        ec      = df_e['emocion'].value_counts()
        tot_e   = len(df_e)
        emo_data = [['Emoción', 'Segundos', '% del total', 'Categoría']]
        for emo, cnt in ec.items():
            pct = cnt/tot_e*100 if tot_e > 0 else 0
            cat = 'Positiva' if emo in POSITIVAS else ('Negativa' if emo in NEGATIVAS else 'Neutral')
            emo_data.append([emo, f"{cnt/27:.1f}s", f"{pct:.1f}%", cat])
        emo_tbl = Table(emo_data, colWidths=[4.5*cm, 3.5*cm, 3.5*cm, 5.5*cm])
        emo_tbl.setStyle(TableStyle([
            ('BACKGROUND',  (0,0), (-1,0),  MORADO),
            ('TEXTCOLOR',   (0,0), (-1,0),  white),
            ('FONTNAME',    (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',    (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[HexColor('#F3E5F5'), white]),
            ('FONTNAME',    (0,1), (-1,-1), 'Helvetica'),
            ('ALIGN',       (1,0), (2,-1),  'CENTER'),
            ('GRID',        (0,0), (-1,-1), 0.5, GRIS2),
            ('TOPPADDING',  (0,0), (-1,-1), 4),
            ('BOTTOMPADDING',(0,0),(-1,-1), 4),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(emo_tbl)
        story.append(Spacer(1, 0.3*cm))

    # ── Registro (primeras 20 filas) ─────────────────────────
    if sector_log:
        story.append(Paragraph('Registro Segundo a Segundo (muestra)', style_sec))
        reg_data = [['Segundo', 'Sector', 'Emoción', 'Engagement est.']]
        for entry in sector_log[:20]:
            reg_data.append([
                f"{entry.get('tiempo_seg',0)}s",
                f"S{entry.get('sector','—')}",
                entry.get('emocion','—'),
                f"{_eng_estimado(entry.get('emocion','neutral'))}%"
            ])
        reg_tbl = Table(reg_data, colWidths=[3.5*cm, 3.5*cm, 5*cm, 5*cm])
        reg_tbl.setStyle(TableStyle([
            ('BACKGROUND',  (0,0), (-1,0),  AZUL),
            ('TEXTCOLOR',   (0,0), (-1,0),  white),
            ('FONTNAME',    (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',    (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[GRIS, white]),
            ('FONTNAME',    (0,1), (-1,-1), 'Helvetica'),
            ('ALIGN',       (0,0), (-1,-1), 'CENTER'),
            ('GRID',        (0,0), (-1,-1), 0.5, GRIS2),
            ('TOPPADDING',  (0,0), (-1,-1), 3),
            ('BOTTOMPADDING',(0,0),(-1,-1), 3),
        ]))
        story.append(reg_tbl)
        if len(sector_log) > 20:
            story.append(Paragraph(
                f'* Se muestran 20 de {len(sector_log)} segundos. El Excel contiene el registro completo.',
                style_small))

    # ── Áreas de Interés (AOI) ───────────────────────────────
    if aoi_log is None:
        aoi_log = []

    story.append(PageBreak())
    story.append(Paragraph('Reporte de Áreas de Interés (AOI) — Logo Coca-Cola', style_sec))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        'Se definieron 3 AOIs sobre el logo de Coca-Cola: AOI 1 (dinámica, seg. 41-50), '
        'AOI 2 (dinámica, seg. 51-57) y AOI 3 (estática, seg. 58-59).',
        style_small))
    story.append(Spacer(1, 0.3*cm))

    # Cargar AOIs desde JSON
    aois_def_pdf = _cargar_aois_json()
    if not aois_def_pdf:
        aois_def_pdf = [
            {'id':'aoi_logo_camion',  'nombre':'AOI 1 — Logo en camión lateral',  'tipo':'dinamica',  'segundos':list(range(41,51))},
            {'id':'aoi_logo_familia', 'nombre':'AOI 2 — Logo de fondo con familia','tipo':'dinamica',  'segundos':list(range(51,58))},
            {'id':'aoi_logo_estatico','nombre':'AOI 3 — Logo estático final',      'tipo':'estatica',  'segundos':[58,59]},
        ]

    colores_hdr_pdf = [HexColor('#002060'), HexColor('#C00000'), HexColor('#006400')]
    bg_pdf          = [HexColor('#D0E4F7'), HexColor('#FFE8E8'), HexColor('#E2EFDA')]

    # Tabla resumen de las 3 AOIs
    res_data = [['AOI','Tipo','Inicio','Fin','¿Lo vio?','Seg. miró','% tiempo','Emoción dom.','Eng. prom.']]
    for aoi_def in aois_def_pdf:
        met = _calcular_metricas_aoi(aoi_def['id'], aois_def_pdf, aoi_log, sector_log)
        res_data.append([
            aoi_def['nombre'],
            aoi_def.get('tipo','—').capitalize(),
            f"{aoi_def['segundos'][0]}s",
            f"{aoi_def['segundos'][-1]}s",
            met.get('vio','—'),
            f"{met.get('seg_mira',0)}s",
            f"{met.get('pct',0)}%",
            met.get('emo_dom','—'),
            f"{met.get('eng_prom',0)}%",
        ])

    res_tbl = Table(res_data, colWidths=[3.8*cm,2*cm,1.2*cm,1.2*cm,1.5*cm,1.5*cm,1.5*cm,2*cm,1.8*cm])
    res_style = [
        ('BACKGROUND',    (0,0), (-1,0),  HexColor('#002060')),
        ('TEXTCOLOR',     (0,0), (-1,0),  white),
        ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,-1), 6.5),
        ('FONTNAME',      (0,1), (-1,-1), 'Helvetica'),
        ('ALIGN',         (1,0), (-1,-1), 'CENTER'),
        ('GRID',          (0,0), (-1,-1), 0.3, GRIS2),
        ('TOPPADDING',    (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]
    for i, c in enumerate(bg_pdf, 1):
        if i < len(res_data):
            res_style.append(('BACKGROUND', (0,i), (-1,i), c))
    res_tbl.setStyle(TableStyle(res_style))
    story.append(res_tbl)
    story.append(Spacer(1, 0.4*cm))

    # Detalle por cada AOI
    for idx_aoi, aoi_def in enumerate(aois_def_pdf):
        met = _calcular_metricas_aoi(aoi_def['id'], aois_def_pdf, aoi_log, sector_log)
        c_hdr = colores_hdr_pdf[idx_aoi % 3]
        c_bg  = bg_pdf[idx_aoi % 3]
        tipo_txt = '(Dinámica)' if aoi_def.get('tipo') == 'dinamica' else '(Estática)'

        story.append(Paragraph(
            f"<b>{aoi_def['nombre']} {tipo_txt}</b> — "
            f"Segundos {aoi_def['segundos'][0]} al {aoi_def['segundos'][-1]}",
            ParagraphStyle('aoi_h', parent=style_small, textColor=c_hdr,
                          fontName='Helvetica-Bold', fontSize=9)))
        story.append(Spacer(1, 0.1*cm))

        det_data = [
            ['¿Vio el AOI?',      met.get('vio','—'),
             'Seg. disponibles',  f"{met.get('seg_disp',0)}s",
             'Seg. mirando',      f"{met.get('seg_mira',0)}s"],
            ['% tiempo',          f"{met.get('pct',0)}%",
             'Emoción dominante', met.get('emo_dom','—'),
             'Eng. promedio',     f"{met.get('eng_prom',0)}%"],
            ['1ª fijación al AOI', f"{met.get('primera_fij','—')}s" if met.get('primera_fij') is not None else '—',
             'Última fijación',   f"{met.get('ultima_fij','—')}s" if met.get('ultima_fij') is not None else '—',
             'Inicio AOI',        f"{met.get('seg_inicio',0)}s"],
        ]
        det_tbl = Table(det_data, colWidths=[3.5*cm,2.5*cm,3.5*cm,2.5*cm,3*cm,2.5*cm])
        det_sty = [
            ('FONTSIZE',      (0,0), (-1,-1), 7),
            ('FONTNAME',      (0,0), (0,-1),  'Helvetica-Bold'),
            ('FONTNAME',      (2,0), (2,-1),  'Helvetica-Bold'),
            ('FONTNAME',      (4,0), (4,-1),  'Helvetica-Bold'),
            ('FONTNAME',      (1,0), (1,-1),  'Helvetica'),
            ('FONTNAME',      (3,0), (3,-1),  'Helvetica'),
            ('FONTNAME',      (5,0), (5,-1),  'Helvetica'),
            ('ALIGN',         (1,0), (1,-1),  'CENTER'),
            ('ALIGN',         (3,0), (3,-1),  'CENTER'),
            ('ALIGN',         (5,0), (5,-1),  'CENTER'),
            ('ROWBACKGROUNDS',(0,0), (-1,-1), [c_bg, white, c_bg]),
            ('GRID',          (0,0), (-1,-1), 0.3, GRIS2),
            ('TOPPADDING',    (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('LEFTPADDING',   (0,0), (-1,-1), 4),
        ]
        det_tbl.setStyle(TableStyle(det_sty))
        story.append(det_tbl)

        if not met.get('entradas'):
            story.append(Spacer(1, 0.1*cm))
            story.append(Paragraph(
                '⚠ El participante no miró esta AOI durante los segundos disponibles.',
                ParagraphStyle('aoi_warn', parent=style_small,
                              textColor=HexColor('#C00000'), fontSize=7)))
        story.append(Spacer(1, 0.3*cm))

    # ── Línea de tiempo emocional (Gantt) ────────────────────
    if sector_log:
        story.append(PageBreak())
        story.append(Paragraph('Línea de Tiempo Emocional — Emociones vs Tiempo', style_sec))
        story.append(Spacer(1, 0.1*cm))
        story.append(Paragraph('Cada barra representa el período de tiempo en que el participante manifestó esa emoción.', style_small))
        story.append(Spacer(1, 0.3*cm))

        # Colores por emoción
        color_emo_pdf = {
            'neutral':  HexColor('#2A78D6'),
            'happy':    HexColor('#1BAF7A'),
            'surprise': HexColor('#EDA100'),
            'sad':      HexColor('#E34948'),
            'fear':     HexColor('#4A3AA7'),
            'angry':    HexColor('#888780'),
            'disgust':  HexColor('#008300'),
        }
        bg_emo_pdf = {
            'neutral':  HexColor('#E6F1FB'),
            'happy':    HexColor('#E1F5EE'),
            'surprise': HexColor('#FAEEDA'),
            'sad':      HexColor('#FCEBEB'),
            'fear':     HexColor('#EEEDFE'),
            'angry':    HexColor('#F1EFE8'),
            'disgust':  HexColor('#EAF3DE'),
        }

        total_segs_pdf = max(e.get('tiempo_seg', 0) for e in sector_log) + 1
        total_segs_pdf = max(total_segs_pdf, 1)

        # Obtener emociones presentes
        emociones_orden_pdf = ['neutral','happy','surprise','sad','fear','angry','disgust']
        emociones_presentes_pdf = [emo for emo in emociones_orden_pdf
                                   if any(e.get('emocion') == emo for e in sector_log)]

        # Construir tabla Gantt
        # Columna 0 = etiqueta emoción, columnas 1..N = segundos
        PAGE_W = 17*cm
        lbl_w  = 2.2*cm
        bar_w  = (PAGE_W - lbl_w) / total_segs_pdf

        gantt_data = []
        gantt_style = []

        # Fila de encabezado con marcas de tiempo cada 5 segundos
        header_row = ['']
        for s in range(total_segs_pdf):
            header_row.append(str(s) + 's' if s % 5 == 0 else '')
        gantt_data.append(header_row)
        gantt_style.append(('FONTSIZE',    (0,0), (-1,0), 6))
        gantt_style.append(('TEXTCOLOR',   (0,0), (-1,0), HexColor('#898781')))
        gantt_style.append(('BOTTOMPADDING',(0,0),(-1,0), 2))
        gantt_style.append(('TOPPADDING',  (0,0), (-1,0), 2))

        # Una fila por emoción
        for row_idx, emo in enumerate(emociones_presentes_pdf, 1):
            row = [emo]
            for s in range(total_segs_pdf):
                row.append('')
            gantt_data.append(row)

            # Estilo de la fila
            gantt_style.append(('FONTSIZE',    (0,row_idx), (0,row_idx), 8))
            gantt_style.append(('FONTNAME',    (0,row_idx), (0,row_idx), 'Helvetica-Bold'))
            gantt_style.append(('TEXTCOLOR',   (0,row_idx), (0,row_idx), color_emo_pdf.get(emo, HexColor('#000000'))))
            gantt_style.append(('ALIGN',       (0,row_idx), (0,row_idx), 'RIGHT'))
            gantt_style.append(('ROWBACKGROUNDS',(1,row_idx),(-1,row_idx),[HexColor('#F8F8F8')]))
            gantt_style.append(('TOPPADDING',  (0,row_idx), (-1,row_idx), 1))
            gantt_style.append(('BOTTOMPADDING',(0,row_idx),(-1,row_idx), 1))

            # Pintar celdas donde esta emoción está activa
            for entry in sector_log:
                seg_t = entry.get('tiempo_seg', 0)
                emo_t = entry.get('emocion', 'neutral')
                if emo_t == emo and seg_t < total_segs_pdf:
                    col_i = seg_t + 1
                    gantt_style.append(('BACKGROUND', (col_i,row_idx), (col_i,row_idx),
                                        color_emo_pdf.get(emo, HexColor('#888888'))))

        col_widths = [lbl_w] + [bar_w] * total_segs_pdf
        gantt_tbl = Table(gantt_data, colWidths=col_widths)
        gantt_tbl.setStyle(TableStyle(gantt_style + [
            ('GRID',        (0,0), (-1,-1), 0, white),
            ('VALIGN',      (0,0), (-1,-1), 'MIDDLE'),
        ]))
        story.append(gantt_tbl)
        story.append(Spacer(1, 0.3*cm))

        # Leyenda de colores
        leyenda_data = [['Emoción', 'Color', 'Segundos', '% del total']]
        for emo in emociones_presentes_pdf:
            cnt = sum(1 for e in sector_log if e.get('emocion') == emo)
            pct = round(cnt / total_segs_pdf * 100, 1)
            leyenda_data.append([emo, '', f'{cnt}s', f'{pct}%'])

        ley_style = [
            ('BACKGROUND',   (0,0), (-1,0),  AZUL),
            ('TEXTCOLOR',    (0,0), (-1,0),  white),
            ('FONTNAME',     (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',     (0,0), (-1,-1), 8),
            ('FONTNAME',     (0,1), (-1,-1), 'Helvetica'),
            ('ALIGN',        (1,0), (-1,-1), 'CENTER'),
            ('GRID',         (0,0), (-1,-1), 0.3, GRIS2),
            ('TOPPADDING',   (0,0), (-1,-1), 3),
            ('BOTTOMPADDING',(0,0), (-1,-1), 3),
        ]
        for i, emo in enumerate(emociones_presentes_pdf, 1):
            ley_style.append(('BACKGROUND', (1,i), (1,i), color_emo_pdf.get(emo, HexColor('#888888'))))
            ley_style.append(('TEXTCOLOR',  (0,i), (0,i), color_emo_pdf.get(emo, HexColor('#000000'))))
            ley_style.append(('FONTNAME',   (0,i), (0,i), 'Helvetica-Bold'))

        ley_tbl = Table(leyenda_data, colWidths=[4*cm, 1*cm, 3*cm, 3*cm])
        ley_tbl.setStyle(TableStyle(ley_style))
        story.append(ley_tbl)
        story.append(Spacer(1, 0.3*cm))

        # Tabla detalle segundo a segundo
        story.append(Paragraph('Detalle segundo a segundo', style_small))
        story.append(Spacer(1, 0.1*cm))

        # Cargar anotaciones
        import glob as _glob2, json as _json2
        anotaciones_pdf = {}
        json_files_pdf = _glob2.glob('videos/*.json')
        if json_files_pdf:
            try:
                with open(json_files_pdf[0], encoding='utf-8') as jf2:
                    data_json2 = _json2.load(jf2)
                    for item in data_json2.get('anotaciones', []):
                        anotaciones_pdf[item['segundo']] = item.get('escena', '—')
            except Exception as e:
                print(f"Error cargando anotaciones PDF: {e}")

        tl_data = [['Seg.', 'Escena del Comercial', 'Emoción', 'Categoría', 'Eng. est.']]
        for entry in sector_log:
            seg = entry.get('tiempo_seg', 0)
            emo = entry.get('emocion', 'neutral')
            cat = 'Positiva' if emo in POSITIVAS else ('Negativa' if emo in NEGATIVAS else 'Neutral')
            eng = _eng_estimado(emo)
            escena = anotaciones_pdf.get(int(seg), '—')
            tl_data.append([f"{seg}s", escena, emo, cat, f"{eng}%"])

        tl_style = [
            ('BACKGROUND',    (0,0), (-1,0),  AZUL),
            ('TEXTCOLOR',     (0,0), (-1,0),  white),
            ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0,0), (-1,-1), 7),
            ('FONTNAME',      (0,1), (-1,-1), 'Helvetica'),
            ('ALIGN',         (0,0), (0,-1),  'CENTER'),
            ('ALIGN',         (2,0), (-1,-1), 'CENTER'),
            ('GRID',          (0,0), (-1,-1), 0.3, GRIS2),
            ('TOPPADDING',    (0,0), (-1,-1), 2),
            ('BOTTOMPADDING', (0,0), (-1,-1), 2),
            ('LEFTPADDING',   (0,0), (-1,-1), 4),
        ]
        for i, entry in enumerate(sector_log, 1):
            emo = entry.get('emocion', 'neutral')
            bg_c = bg_emo_pdf.get(emo, HexColor('#F2F2F2'))
            tl_style.append(('BACKGROUND', (0,i), (-1,i), bg_c))

        tl_tbl = Table(tl_data, colWidths=[1.5*cm, 7*cm, 3*cm, 3*cm, 2.5*cm])
        tl_tbl.setStyle(TableStyle(tl_style))
        story.append(tl_tbl)
        story.append(Spacer(1, 0.2*cm))

    # ── Pie de página ────────────────────────────────────────
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width='100%', thickness=1, color=AZUL2))
    story.append(Paragraph(
        f'© 2026 Universidad ECOTEC — Ingeniería en Sistemas Inteligentes | '
        f'Campus Samborondón | Generado: {datetime.now().strftime("%d/%m/%Y %H:%M")}',
        style_small))

    doc.build(story)

    for tf in tmp_files:
        try: os.unlink(tf)
        except: pass

    return filepath


# ─────────────────────────────────────────────────────────────────────────────
#  EXCEL CONSOLIDADO (acumulativo de los 30 participantes)
# ─────────────────────────────────────────────────────────────────────────────

def save_consolidated_excel(pid, participant_data, summary, sector_log,
                            filepath='data/reporte_consolidado.xlsx', aoi_log=None):
    """Agrega una fila al Excel consolidado. Crea el archivo si no existe."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    os.makedirs('data', exist_ok=True)

    thin = Side(style='thin', color='B8C0CC')
    brd  = Border(left=thin, right=thin, top=thin, bottom=thin)

    def hcell(ws, row, col, val, bg='002060'):
        c = ws.cell(row=row, column=col, value=val)
        c.font      = Font(bold=True, color='FFFFFF', name='Arial', size=9)
        c.fill      = PatternFill('solid', start_color=bg)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border    = brd
        return c

    def dcell(ws, row, col, val, fmt=None, bold=False, bg=None, align='center'):
        c = ws.cell(row=row, column=col, value=val)
        c.font      = Font(name='Arial', size=9, bold=bold)
        c.alignment = Alignment(horizontal=align, vertical='center')
        c.border    = brd
        if bg:  c.fill = PatternFill('solid', start_color=bg)
        if fmt: c.number_format = fmt
        return c

    wb = openpyxl.load_workbook(filepath) if os.path.exists(filepath) else openpyxl.Workbook()
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']

    fecha = datetime.now().strftime('%d/%m/%Y %H:%M')

    # ── Hoja 1: Resumen ───────────────────────────────────────
    if 'Resumen' not in wb.sheetnames:
        ws1 = wb.create_sheet('Resumen')
        ws1.merge_cells('A1:P1')
        t = ws1['A1']
        t.value     = 'Reporte Consolidado — Engagement Publicitario ECOTEC 2026'
        t.font      = Font(bold=True, color='FFFFFF', name='Arial', size=12)
        t.fill      = PatternFill('solid', start_color='002060')
        t.alignment = Alignment(horizontal='center', vertical='center')
        ws1.row_dimensions[1].height = 28
        cabeceras = [
            'Foto','ID','Nombre','Código','Carrera','Semestre','Gafas','Fecha',
            'Eng. Prom. (%)','Eng. Máx. (%)','T. Mirando (%)','1ª Fijación al AOI (s)',
            'Distracciones','Valencia (%)','Emoción Dom.','Sector Top','Vio Logo AOI'
        ]
        for j, h in enumerate(cabeceras, 1):
            hcell(ws1, 2, j, h)
        ws1.row_dimensions[2].height = 32
        ws1.freeze_panes = 'B3'
        anchos = [12,10,28,14,24,12,10,18,16,14,16,14,14,12,16,12]
        for j, w in enumerate(anchos, 1):
            ws1.column_dimensions[get_column_letter(j)].width = w
    else:
        ws1 = wb['Resumen']

    existing = [ws1.cell(row=r,column=2).value for r in range(3, ws1.max_row+1)]
    if pid not in existing:
        nr = ws1.max_row + 1
        bg = 'D9E1F2' if nr % 2 == 0 else 'F2F2F2'
        ws1.row_dimensions[nr].height = 55

        # Foto thumbnail en columna A
        photo_path = participant_data.get('photo_path', '')
        if photo_path and os.path.exists(photo_path):
            try:
                import tempfile, cv2 as _cv2
                img_cv = _cv2.imread(photo_path)
                if img_cv is not None:
                    thumb = _cv2.resize(img_cv, (60, 55))
                    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
                    tmp.close()
                    _cv2.imwrite(tmp.name, thumb)
                    xl_img = XLImage(tmp.name)
                    xl_img.width = 60; xl_img.height = 55
                    ws1.add_image(xl_img, f'A{nr}')
                    import threading
                    def _del(p=tmp.name):
                        import time, os as _os
                        time.sleep(2)
                        try: _os.unlink(p)
                        except: pass
                    threading.Thread(target=_del, daemon=True).start()
            except Exception as e:
                print(f"Foto en consolidado: {e}")
                dcell(ws1, nr, 1, '📷', align='center', bg=bg)
        else:
            dcell(ws1, nr, 1, '—', align='center', bg=bg)

        dcell(ws1, nr,  2, pid, bold=True, bg=bg)
        dcell(ws1, nr,  3, participant_data.get('nombre','—'), bg=bg, align='left')
        dcell(ws1, nr,  4, participant_data.get('codigo','—'), bg=bg)
        dcell(ws1, nr,  5, participant_data.get('carrera','—'), bg=bg, align='left')
        dcell(ws1, nr,  6, participant_data.get('semestre','—'), bg=bg)
        dcell(ws1, nr,  7, 'Sí' if participant_data.get('gafas') else 'No', bg=bg)
        dcell(ws1, nr,  8, fecha, bg=bg)
        if aoi_log is None:
            aoi_log = []
        # Primera fijación real: el segundo más bajo en que miró CUALQUIERA de las 3 AOIs.
        # (antes dependía de summary['primera_fijacion'], que nunca se poblaba y quedaba en 0)
        primera_fijacion_real = min([e['segundo'] for e in aoi_log], default=0)

        dcell(ws1, nr,  9, round(summary.get('engagement_promedio',0)*100,1), bg=bg, fmt='0.0"%"')
        dcell(ws1, nr, 10, round(summary.get('engagement_maximo',0)*100,1),  bg=bg, fmt='0.0"%"')
        dcell(ws1, nr, 11, round(summary.get('tiempo_mirando',0),1), bg=bg, fmt='0.0"%"')
        dcell(ws1, nr, 12, primera_fijacion_real, bg=bg, fmt='0"s"')
        dcell(ws1, nr, 13, summary.get('num_distracciones', 0), bg=bg)
        dcell(ws1, nr, 14, round(summary.get('valencia_emocional',0),1), bg=bg, fmt='+0.0"%"')
        dcell(ws1, nr, 15, summary.get('emocion_dominante','—'), bg=bg)
        dcell(ws1, nr, 16, _top_sector(sector_log), bg=bg)
        # Vio Logo AOI: antes comparaba contra el id viejo 'aoi_logo_cocacola' (de cuando
        # solo existía 1 AOI), por lo que casi siempre daba 'No' aunque sí hubiera visto
        # el AOI 2 o el AOI 3. Ahora basta con que haya CUALQUIER entrada en aoi_log.
        vio_logo = 'Sí ✅' if aoi_log else 'No ❌'
        dcell(ws1, nr, 17, vio_logo, align='center', bg=bg)

    # ── Hoja 2: Sectores ──────────────────────────────────────
    if 'Sectores' not in wb.sheetnames:
        ws2 = wb.create_sheet('Sectores')
        ws2.merge_cells('A1:H1')
        t2 = ws2['A1']
        t2.value     = 'Distribución de Atención por Sector (grid 4×4)'
        t2.font      = Font(bold=True, color='FFFFFF', name='Arial', size=12)
        t2.fill      = PatternFill('solid', start_color='00A651')
        t2.alignment = Alignment(horizontal='center', vertical='center')
        ws2.row_dimensions[1].height = 28
        for j, h in enumerate(['ID','Nombre','Carrera','Sector','Fila','Columna','Segundos','% Total'], 1):
            hcell(ws2, 2, j, h, bg='00A651')
        ws2.row_dimensions[2].height = 28
        ws2.freeze_panes = 'A3'
        for j, w in enumerate([10,28,24,10,10,12,14,12], 1):
            ws2.column_dimensions[get_column_letter(j)].width = w
    else:
        ws2 = wb['Sectores']

    if sector_log and pid not in [ws2.cell(row=r,column=1).value for r in range(3, ws2.max_row+1)]:
        df_s = pd.DataFrame(sector_log)
        sc   = df_s['sector'].value_counts().sort_index()
        tot  = len(df_s)
        for sec, cnt in sc.items():
            pct  = cnt/tot*100 if tot > 0 else 0
            fila = ((int(sec)-1)//4)+1
            col  = ((int(sec)-1)%4)+1
            nr   = ws2.max_row+1
            bg   = 'E2EFDA' if nr % 2 == 0 else 'F2F2F2'
            dcell(ws2, nr, 1, pid,                                    bold=True, bg=bg)
            dcell(ws2, nr, 2, participant_data.get('nombre','—'),     bg=bg, align='left')
            dcell(ws2, nr, 3, participant_data.get('carrera','—'),    bg=bg, align='left')
            dcell(ws2, nr, 4, int(sec), align='center', bg=bg)
            dcell(ws2, nr, 5, fila,     align='center', bg=bg)
            dcell(ws2, nr, 6, col,      align='center', bg=bg)
            dcell(ws2, nr, 7, round(cnt/27, 1), align='right', bg=bg, fmt='0.0')
            dcell(ws2, nr, 8, round(pct, 1),    align='right', bg=bg, fmt='0.0"%"')

    # ── Hoja 3: Emociones ─────────────────────────────────────
    if 'Emociones' not in wb.sheetnames:
        ws3 = wb.create_sheet('Emociones')
        ws3.merge_cells('A1:G1')
        t3 = ws3['A1']
        t3.value     = 'Distribución de Emociones por Participante'
        t3.font      = Font(bold=True, color='FFFFFF', name='Arial', size=12)
        t3.fill      = PatternFill('solid', start_color='7030A0')
        t3.alignment = Alignment(horizontal='center', vertical='center')
        ws3.row_dimensions[1].height = 28
        for j, h in enumerate(['ID','Nombre','Carrera','Emoción','Segundos','% Total','Categoría'], 1):
            hcell(ws3, 2, j, h, bg='7030A0')
        ws3.row_dimensions[2].height = 28
        ws3.freeze_panes = 'A3'
        for j, w in enumerate([10,28,24,16,12,12,14], 1):
            ws3.column_dimensions[get_column_letter(j)].width = w
    else:
        ws3 = wb['Emociones']

    if sector_log and pid not in [ws3.cell(row=r,column=1).value for r in range(3, ws3.max_row+1)]:
        df_e = pd.DataFrame(sector_log)
        ec   = df_e['emocion'].value_counts()
        tot  = len(df_e)
        for emo, cnt in ec.items():
            pct = cnt/tot*100 if tot > 0 else 0
            cat = 'Positiva' if emo in POSITIVAS else ('Negativa' if emo in NEGATIVAS else 'Neutral')
            nr  = ws3.max_row+1
            bg  = 'F3E5F5' if nr % 2 == 0 else 'F2F2F2'
            dcell(ws3, nr, 1, pid,                                   bold=True, bg=bg)
            dcell(ws3, nr, 2, participant_data.get('nombre','—'),    bg=bg, align='left')
            dcell(ws3, nr, 3, participant_data.get('carrera','—'),   bg=bg, align='left')
            dcell(ws3, nr, 4, emo, bg=bg)
            dcell(ws3, nr, 5, round(cnt/27,1), align='right', bg=bg, fmt='0.0')
            dcell(ws3, nr, 6, round(pct,1),    align='right', bg=bg, fmt='0.0"%"')
            dcell(ws3, nr, 7, cat, align='center', bg=bg)

    # ── Hoja 4: Instrucciones ─────────────────────────────────
    if 'Instrucciones' not in wb.sheetnames:
        ws4 = wb.create_sheet('Instrucciones')
        ws4.merge_cells('A1:C1')
        t4 = ws4['A1']
        t4.value     = 'Cómo usar este archivo'
        t4.font      = Font(bold=True, color='FFFFFF', name='Arial', size=13)
        t4.fill      = PatternFill('solid', start_color='002060')
        t4.alignment = Alignment(horizontal='center', vertical='center')
        ws4.row_dimensions[1].height = 30
        instrucciones = [
            ('Hoja "Resumen"',    'Una fila por participante. Incluye todos los datos y métricas principales.'),
            ('Hoja "Sectores"',   'Una fila por participante × sector. Compara qué zonas del comercial atrajeron más atención.'),
            ('Hoja "Emociones"',  'Una fila por participante × emoción. Compara respuestas emocionales entre carreras.'),
            ('Tablas dinámicas',  'Selecciona cualquier hoja → Insertar → Tabla dinámica para analizar por carrera o semestre.'),
            ('Filtros',           'Usa el botón ▼ en las cabeceras para filtrar por carrera, emoción o sector.'),
            ('Gráficos',          'Selecciona columnas → Insertar → Gráfico para crear visualizaciones comparativas.'),
            ('Actualización',     'Cada nueva sesión agrega filas automáticamente sin borrar datos anteriores.'),
        ]
        for i, (tit, desc) in enumerate(instrucciones, 3):
            c1 = ws4.cell(row=i, column=1, value=tit)
            c1.font      = Font(bold=True, name='Arial', size=10, color='002060')
            c1.alignment = Alignment(vertical='center')
            c2 = ws4.cell(row=i, column=2, value=desc)
            c2.font      = Font(name='Arial', size=10)
            c2.alignment = Alignment(wrap_text=True, vertical='center')
            ws4.row_dimensions[i].height = 28
        ws4.column_dimensions['A'].width = 24
        ws4.column_dimensions['B'].width = 72

    # Reordenar hojas
    for idx, name in enumerate(['Resumen','Sectores','Emociones','Instrucciones']):
        if name in wb.sheetnames:
            wb.move_sheet(name, offset=wb.sheetnames.index(name)-idx)

    wb.save(filepath)
    return filepath