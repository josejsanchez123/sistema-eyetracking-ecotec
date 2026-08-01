import cv2
import numpy as np
import time
import math
from datetime import datetime
from models.engagement_model import EngagementModel
from PyQt6.QtCore import QThread, pyqtSignal


class CameraThread(QThread):
    frame_ready = pyqtSignal(object, object)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.running = True

    def run(self):
        while self.running:
            frame, data = self.controller.process_frame()
            if frame is not None:
                self.frame_ready.emit(frame, data)
            time.sleep(0.033)

    def stop(self):
        self.running = False
        self.wait()


class SessionController:
    def __init__(self):
        self.model = EngagementModel()
        self.gaze_pipeline = None
        self.cap = None
        self.running = False
        self.frame_count = 0
        self.current_emotion = 'neutral'
        self.current_emotion_probs = {}
        self.current_yaw = 0.0
        self._fps_last_time = time.time()
        self._fps_frame_acc = 0
        self.current_fps    = 0.0
        self.current_pitch = 0.0
        self.is_looking = False
        self.render = None
        self.fer = None
        self.face_cascade = None
        self.pre_extracted_audio = None

        # Umbrales de mirada — ajustados dinámicamente por DistanceDialog
        self.pitch_offset    = 0.0
        self.yaw_threshold   = 35.0
        self.pitch_threshold = 30.0

        # Suavizado temporal de emociones:
        # Solo se acepta un cambio de emoción si la nueva emoción
        # aparece de forma consistente durante EMOTION_CONFIRM_FRAMES
        # análisis consecutivos. Evita saltos bruscos por gafas/sombras.
        self._emotion_candidate   = 'neutral'
        self._emotion_candidate_count = 0
        self.EMOTION_CONFIRM_FRAMES = 3  # 3 análisis × ~167ms = ~0.5s de confirmación

    def initialize(self):
        import torch
        from pathlib import Path
        from l2cs import Pipeline, render
        from hsemotion_onnx.facial_emotions import HSEmotionRecognizer

        t0 = time.time()
        self.render = render
        self.fer = HSEmotionRecognizer(model_name='enet_b0_8_best_afew')
        print(f"[{time.time()-t0:.1f}s] HSEmotions cargado")

        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        print(f"[{time.time()-t0:.1f}s] Face cascade cargado")

        print(f"[{time.time()-t0:.1f}s] Cargando L2CS-Net...")
        self.gaze_pipeline = Pipeline(
            weights=Path('models/L2CSNet_gaze360.pkl'),
            arch='ResNet50',
            device=torch.device('cuda')
        )
        print(f"[{time.time()-t0:.1f}s] L2CS-Net cargado")

        print(f"[{time.time()-t0:.1f}s] Buscando cámara Brio...")
        self.cap = self._find_brio()
        print(f"[{time.time()-t0:.1f}s] Cámara lista")
        print("Sistema listo.")

    def _find_brio(self):
        cap = cv2.VideoCapture(1, cv2.CAP_DSHOW)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            cap.set(cv2.CAP_PROP_FPS, 30)
            width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            if width >= 1280:
                print(f"Brio detectada en índice 1")
                return cap
            cap.release()

        for i in range(3):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
                width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                if width >= 3840:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                    cap.set(cv2.CAP_PROP_FPS, 30)
                    print(f"Brio detectada en índice {i}")
                    return cap
                cap.release()

        print("Brio no encontrada, usando cámara 0")
        return cv2.VideoCapture(0, cv2.CAP_DSHOW)

    def warmup(self):
        try:
            dummy = np.zeros((400, 640, 3), dtype=np.uint8)
            self.gaze_pipeline.step(dummy)
            print("GPU calentada y lista.")
        except Exception:
            pass

    def get_last_gaze(self):
        return self.current_yaw, self.current_pitch

    def start_session(self, participant_id):
        self.model.start_session(participant_id)
        self.running = True
        self.frame_count = 0
        # Reiniciar suavizado emocional al inicio de cada sesión
        self._emotion_candidate = 'neutral'
        self._emotion_candidate_count = 0

    def stop_session(self):
        self.running = False
        filename = self.model.save_to_csv()
        summary  = self.model.get_summary()
        return filename, summary

    def _get_largest_face(self, faces):
        """Retorna el rostro de mayor área (el participante, el más cercano).
        Ignora rostros pequeños de fondo visibles a través del vidrio."""
        if len(faces) == 0:
            return None
        return max(faces, key=lambda f: f[2] * f[3])

    def _smooth_emotion(self, new_emotion, new_probs):
        """Suavizado temporal: solo acepta un cambio de emoción si la nueva
        emoción se confirma durante EMOTION_CONFIRM_FRAMES análisis consecutivos.
        Elimina saltos bruscos causados por gafas, sombras o frames ruidosos."""
        if new_emotion == self._emotion_candidate:
            self._emotion_candidate_count += 1
        else:
            self._emotion_candidate = new_emotion
            self._emotion_candidate_count = 1

        if self._emotion_candidate_count >= self.EMOTION_CONFIRM_FRAMES:
            self.current_emotion       = new_emotion
            self.current_emotion_probs = new_probs

        return self.current_emotion, self.current_emotion_probs

    def _detect_emotion(self, frame):
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(60, 60)  # ignorar rostros pequeños/lejanos
            )
            if len(faces) == 0:
                return self.current_emotion, self.current_emotion_probs

            face = self._get_largest_face(faces)
            if face is None:
                return self.current_emotion, self.current_emotion_probs

            x, y, w, h = face

            # Ampliar ligeramente el recorte facial para dar más contexto
            # al modelo — mejora la precisión especialmente con gafas
            pad = int(min(w, h) * 0.1)
            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(frame.shape[1], x + w + pad)
            y2 = min(frame.shape[0], y + h + pad)
            face_img = frame[y1:y2, x1:x2]

            # Redimensionar a 224x224 para dar más detalle a HSEmotion
            face_resized = cv2.resize(face_img, (224, 224))
            face_rgb = cv2.cvtColor(face_resized, cv2.COLOR_BGR2RGB)

            emotion, scores = self.fer.predict_emotions(face_rgb, logits=False)

            emotion_map = {
                'Anger': 'angry', 'Contempt': 'disgust', 'Disgust': 'disgust',
                'Fear': 'fear', 'Happiness': 'happy', 'Neutral': 'neutral',
                'Sadness': 'sad', 'Surprise': 'surprise'
            }
            emotion_lower = emotion_map.get(emotion, 'neutral')

            probs = {}
            for i, score in enumerate(scores):
                label = self.fer.idx_to_class[i]
                probs[emotion_map.get(label, label.lower())] = float(score)

            # Filtro de desempate angry/neutral:
            # Las gafas sobreestiman angry sistemáticamente.
            # Si angry y neutral están dentro de 10pp, preferir neutral.
            if emotion_lower == 'angry':
                angry_prob   = probs.get('angry', 0)
                neutral_prob = probs.get('neutral', 0)
                if (angry_prob - neutral_prob) < 0.10:
                    emotion_lower = 'neutral'

            # Aplicar suavizado temporal antes de retornar
            return self._smooth_emotion(emotion_lower, probs)

        except Exception:
            return self.current_emotion, self.current_emotion_probs

    def _is_looking_at_screen(self, yaw, pitch):
        """Determina si el participante mira la pantalla considerando
        la geometría real del montaje con compensación de pitch_offset.
        yaw y pitch deben venir ya en GRADOS (ver process_frame)."""
        corrected_pitch = pitch - self.pitch_offset
        return (abs(yaw) < self.yaw_threshold and
                abs(corrected_pitch) < self.pitch_threshold)

    def process_frame(self):
        if self.cap is None:
            return None, None
        ret, frame = self.cap.read()
        if not ret:
            return None, None

        frame_small = cv2.resize(frame, (640, 400))
        frame_small = cv2.flip(frame_small, 1)

        try:
            results = self.gaze_pipeline.step(frame_small)
            # FIX: L2CS-Net devuelve yaw/pitch en RADIANES, pero
            # yaw_threshold/pitch_threshold están definidos en GRADOS.
            # Sin esta conversión, abs(yaw) < 35 es casi siempre True
            # (un radián máximo ronda ±3.14), por lo que is_looking
            # quedaba prácticamente siempre en True sin importar el
            # movimiento real de cabeza del participante.
            self.current_yaw   = math.degrees(float(results.yaw[0]))
            self.current_pitch = math.degrees(float(results.pitch[0]))
            self.is_looking    = self._is_looking_at_screen(
                self.current_yaw, self.current_pitch
            )
            frame_small = self.render(frame_small, results)
        except Exception:
            self.is_looking = False

        # Análisis emocional cada 5 frames (~167ms) para suavizado más responsivo
        if self.frame_count % 5 == 0:
            self.current_emotion, self.current_emotion_probs = \
                self._detect_emotion(frame_small)

        if self.running:
            self.model.add_frame_data(
                timestamp=datetime.now().isoformat(),
                emotion=self.current_emotion,
                emotion_probs=self.current_emotion_probs,
                yaw=self.current_yaw,
                pitch=self.current_pitch,
                is_looking=self.is_looking
            )

        self.frame_count += 1
        self._fps_frame_acc += 1
        now = time.time()
        elapsed = now - self._fps_last_time
        if elapsed >= 1.0:
            self.current_fps    = self._fps_frame_acc / elapsed
            self._fps_frame_acc = 0
            self._fps_last_time = now

        return frame_small, {
            'emotion':    self.current_emotion,
            'yaw':        self.current_yaw,
            'pitch':      self.current_pitch,
            'is_looking': self.is_looking,
            'fps':        self.current_fps,
            'engagement': self.model.data[-1]['engagement_index'] if self.model.data else 0.0
        }

    def release(self):
        if self.cap:
            self.cap.release()