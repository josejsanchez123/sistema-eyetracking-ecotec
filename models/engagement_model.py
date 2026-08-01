import pandas as pd
import numpy as np
from datetime import datetime
import os

class EngagementModel:
    def __init__(self):
        self.data = []
        self.participant_id = None
        self.start_time = None

    def start_session(self, participant_id):
        self.participant_id = participant_id
        self.start_time = datetime.now()
        self.data = []
        print(f"Sesión iniciada: Participante {participant_id}")

    def add_frame_data(self, timestamp, emotion, emotion_probs, yaw, pitch, is_looking):
        record = {
            'timestamp':      timestamp,
            'participant_id': self.participant_id,
            'emotion':        emotion,
            'yaw':            round(yaw, 4),
            'pitch':          round(pitch, 4),
            'is_looking':     is_looking,
            'face_detected':  True,   # si llega aquí, el rostro fue detectado
            'engagement_index': self._calculate_engagement(emotion_probs, is_looking)
        }
        self.data.append(record)

    def _calculate_engagement(self, emotion_probs, is_looking):
        if not is_looking:
            return 0.0
        positive_emotions = emotion_probs.get('happy', 0) + \
                            emotion_probs.get('surprise', 0) * 0.5
        engagement = (0.6 * float(is_looking)) + (0.4 * positive_emotions)
        return round(min(engagement, 1.0), 4)

    def save_to_csv(self):
        if not self.data:
            return None
        os.makedirs('data', exist_ok=True)
        df = pd.DataFrame(self.data)
        filename = (f"data/participante_{self.participant_id}_"
                    f"{self.start_time.strftime('%Y%m%d_%H%M%S')}.csv")
        df.to_csv(filename, index=False)
        print(f"Datos guardados en: {filename}")
        return filename

    # ─────────────────────────────────────────────────────────────
    #  MÉTRICAS COMPLETAS
    # ─────────────────────────────────────────────────────────────
    def get_summary(self):
        if not self.data:
            return {}

        df = pd.DataFrame(self.data)
        total = len(df)

        # ── duración y FPS reales, a partir de los timestamps del propio CSV ──
        # (antes se asumía un fijo de 27 fps en varios cálculos, lo cual no
        # coincide con el FPS real del pipeline, sobre todo con cámaras más
        # lentas que la Brio, ni con las pausas de procesamiento pesado de
        # L2CS-Net/HSEmotion).
        ts = pd.to_datetime(df['timestamp'])
        duracion_seg = (ts.iloc[-1] - ts.iloc[0]).total_seconds()
        fps_real = round(total / duracion_seg, 2) if duracion_seg > 0 else 0.0

        # ── emociones ─────────────────────────────────────────────
        POSITIVAS = {'happy', 'surprise'}
        NEGATIVAS  = {'angry', 'sad', 'fear', 'disgust', 'contempt'}

        emocion_dominante = df['emotion'].mode()[0] if total > 0 else '—'

        positivas_pct = df['emotion'].isin(POSITIVAS).mean() * 100
        negativas_pct  = df['emotion'].isin(NEGATIVAS).mean()  * 100
        valencia       = round(positivas_pct - negativas_pct, 2)

        # variabilidad emocional: cambios de emoción por minuto
        cambios = (df['emotion'] != df['emotion'].shift()).sum()
        duracion_min = duracion_seg / 60  # duración real de la sesión, no asumida
        variabilidad = round(cambios / duracion_min, 2) if duracion_min > 0 else 0.0

        # ── engagement ────────────────────────────────────────────
        eng_promedio = round(df['engagement_index'].mean(), 4)
        eng_maximo   = round(df['engagement_index'].max(),  4)
        eng_minimo   = round(df['engagement_index'].min(),  4)
        eng_std      = round(df['engagement_index'].std(),  4)

        # engagement por segmento (4 segmentos de 15s c/u a ~27fps)
        frames_por_seg = max(1, total // 4)
        segmentos = {}
        for i in range(4):
            inicio = i * frames_por_seg
            fin    = inicio + frames_por_seg if i < 3 else total
            seg_df = df.iloc[inicio:fin]
            segmentos[f'segmento_{i+1}'] = round(seg_df['engagement_index'].mean(), 4)

        # ── atención visual ───────────────────────────────────────
        tiempo_mirando = round(df['is_looking'].mean() * 100, 2)

        # primera fijación: primer frame donde is_looking == True
        primera_fij = None
        looking_idx = df[df['is_looking'] == True].index
        if len(looking_idx) > 0:
            primer_idx   = looking_idx[0]
            primera_fij  = round(primer_idx / fps_real, 2) if fps_real > 0 else 0.0

        # distracciones: transiciones True→False en is_looking
        transiciones = df['is_looking'].astype(int).diff()
        num_distracciones = int((transiciones == -1).sum())

        # duración promedio de cada distracción
        dur_distraccion = 0.0
        if num_distracciones > 0 and fps_real > 0:
            total_frames_fuera = int((df['is_looking'] == False).sum())
            dur_distraccion = round(
                (total_frames_fuera / fps_real) / num_distracciones, 2
            )

        # ── sistema ───────────────────────────────────────────────
        fps_promedio = fps_real  # antes hardcodeado en 27.0; ahora calculado
                                  # a partir de los timestamps reales del CSV
        tasa_deteccion = round(
            df['face_detected'].mean() * 100, 2
        ) if 'face_detected' in df.columns else 100.0

        return {
            # identificación
            'participante':        self.participant_id,
            'fecha':               self.start_time.strftime('%d/%m/%Y'),
            'hora':                self.start_time.strftime('%H:%M'),
            'total_frames':        total,

            # engagement
            'engagement_promedio': eng_promedio,
            'engagement_maximo':   eng_maximo,
            'engagement_minimo':   eng_minimo,
            'engagement_std':      eng_std,
            'engagement_segmentos': segmentos,

            # atención visual
            'tiempo_mirando':      tiempo_mirando,
            'primera_fijacion':    primera_fij,
            'num_distracciones':   num_distracciones,
            'dur_distraccion_prom': dur_distraccion,

            # emociones
            'emocion_dominante':   emocion_dominante,
            'valencia_emocional':  valencia,
            'variabilidad_emocional': variabilidad,
            'positivas_pct':       round(positivas_pct, 2),
            'negativas_pct':       round(negativas_pct, 2),

            # sistema
            'fps_promedio':        fps_promedio,
            'tasa_deteccion':      tasa_deteccion,
        }