import cv2
import numpy as np
import pygame
import os
import time
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import QTimer, Qt, pyqtSignal, QThread
from PyQt6.QtGui import QImage, QPixmap

ROWS = 4
COLS = 4


class VideoThread(QThread):
    frame_ready = pyqtSignal(np.ndarray, float)
    finished = pyqtSignal()

    def __init__(self, video_path):
        super().__init__()
        self.video_path = video_path
        self.running = True

    def run(self):
        cap = cv2.VideoCapture(self.video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 24.0

        start_time = time.perf_counter()
        frame_num  = 0

        while self.running:
            ret, frame = cap.read()
            if not ret:
                self.finished.emit()
                break

            frame_num   += 1
            current_time = frame_num / fps
            expected_time = start_time + current_time
            now = time.perf_counter()
            sleep_time = expected_time - now
            if sleep_time > 0:
                time.sleep(sleep_time)

            self.frame_ready.emit(frame, current_time)

        cap.release()

    def stop(self):
        self.running = False
        self.wait()


class VideoPlayer(QWidget):
    video_finished   = pyqtSignal()
    timestamp_updated = pyqtSignal(float, int, str)
    video_thread_ready = pyqtSignal(object)

    def __init__(self, video_path, audio_path=None, parent=None):
        super().__init__(parent)
        self.video_path    = video_path
        self.audio_path    = audio_path
        self.current_time  = 0.0
        self.fps           = 24.0

        # -1 = sin datos todavía (no se loguea hasta tener gaze real)
        self.current_sector  = -1
        self.current_emotion = 'neutral'
        self.gaze_x = 0
        self.gaze_y = 0
        self.sector_log      = []
        self.last_logged_second = -1
        self._gaze_initialized  = False  # True cuando llega el primer update_gaze()

        # ── Áreas de Interés (AOI) — cargadas desde JSON ────────────────────
        self.AOIS    = []
        self.aoi_map = {}  # {segundo: [(aoi_id, aoi_nombre, [sectores])]}
        self._cargar_aois()
        self.aoi_log = []  # {segundo, aoi_id, aoi_nombre, emocion, sector}
        self.video_thread    = None
        self._render_count   = 0
        self.calibration_matrix = None

        # Dimensiones reales del video
        self._video_w = 0
        self._video_h = 0

        # Dimensiones de la pantalla de renderizado real (pantalla completa o panel)
        # Se actualiza desde main.py con set_render_size() antes de play()
        self._render_w = 0
        self._render_h = 0

        self._build_ui()
        self._init_audio()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: black;")
        layout.addWidget(self.video_label)

    def _init_audio(self):
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            if self.audio_path and os.path.exists(self.audio_path):
                pygame.mixer.music.load(self.audio_path)
                print("Audio cargado correctamente.")
        except Exception as e:
            print(f"Audio no disponible: {e}")

    def load(self):
        cap = cv2.VideoCapture(self.video_path)
        self.fps = cap.get(cv2.CAP_PROP_FPS)
        if self.fps <= 0:
            self.fps = 24.0
        self._video_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._video_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return True

    def set_render_size(self, w, h):
        """Informa al VideoPlayer las dimensiones reales de la superficie
        donde se va a renderizar el video (pantalla completa o panel interno).
        Debe llamarse ANTES de play() para que update_gaze() calcule
        los sectores correctamente desde el primer frame."""
        self._render_w = w
        self._render_h = h
        print(f"VideoPlayer render size: {w}x{h}")

    def play(self):
        try:
            pygame.mixer.music.play()
            print("Audio iniciado.")
        except Exception as e:
            print(f"Error audio: {e}")

        self.video_thread = VideoThread(self.video_path)
        self.video_thread.frame_ready.connect(self.on_frame)
        self.video_thread.finished.connect(self._on_finished)
        self.video_thread.start()
        self.video_thread_ready.emit(self.video_thread)

    def _on_finished(self):
        self.video_finished.emit()

    def stop(self):
        if self.video_thread:
            self.video_thread.stop()
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

    def set_emotion(self, emotion):
        self.current_emotion = emotion

    def _cargar_aois(self):
        """Carga las AOIs desde el JSON de anotaciones del comercial."""
        import glob, json
        self.AOIS    = []
        self.aoi_map = {}
        json_files = glob.glob('videos/*.json')
        if not json_files:
            return
        try:
            with open(json_files[0], encoding='utf-8') as f:
                data = json.load(f)
            for aoi in data.get('aois', []):
                self.AOIS.append(aoi)
                for seg_str, sectores in aoi.get('segundos_cuadrantes', {}).items():
                    seg = int(seg_str)
                    if seg not in self.aoi_map:
                        self.aoi_map[seg] = []
                    self.aoi_map[seg].append({
                        'aoi_id':     aoi['id'],
                        'aoi_nombre': aoi['nombre'],
                        'sectores':   sectores,
                    })
            print(f"[AOI] {len(self.AOIS)} AOIs cargadas desde {json_files[0]}")
        except Exception as e:
            print(f"[AOI] Error cargando AOIs: {e}")

    def set_calibration(self, matrix):
        self.calibration_matrix = matrix
        print("Calibración aplicada al video player.")

    def _get_effective_render_size(self):
        """Devuelve el tamaño real de renderizado:
        1. Si se configuró explícitamente con set_render_size() → usa ese
        2. Si no, intenta el video_label (puede ser 0 si no está visible)
        3. Fallback a 640x400"""
        if self._render_w > 0 and self._render_h > 0:
            return self._render_w, self._render_h
        lw = self.video_label.width()
        lh = self.video_label.height()
        if lw > 0 and lh > 0:
            return lw, lh
        return 640, 400

    def _get_image_rect(self, rw, rh):
        """Calcula la zona real de imagen dentro del área de renderizado (rw x rh),
        excluyendo barras negras de letterbox/pillarbox."""
        if self._video_w <= 0 or self._video_h <= 0:
            return 0, 0, rw, rh

        video_aspect = self._video_w / self._video_h
        render_aspect = rw / rh if rh > 0 else 1.0

        if video_aspect > render_aspect:
            img_w = rw
            img_h = int(rw / video_aspect)
            img_x = 0
            img_y = (rh - img_h) // 2
        else:
            img_h = rh
            img_w = int(rh * video_aspect)
            img_x = (rw - img_w) // 2
            img_y = 0

        return img_x, img_y, img_w, img_h

    def update_gaze(self, yaw, pitch):
        """Mapea yaw/pitch a sector usando las dimensiones reales de renderizado
        y excluyendo las barras negras del letterbox."""
        self._gaze_initialized = True

        rw, rh = self._get_effective_render_size()
        img_x, img_y, img_w, img_h = self._get_image_rect(rw, rh)

        if self.calibration_matrix:
            m = self.calibration_matrix
            src = np.array([yaw, pitch, 1.0], dtype=np.float32)
            cx_raw = float(np.dot(m['x'], src))
            cy_raw = float(np.dot(m['y'], src))

            # La matriz fue calibrada en coordenadas de pantalla completa
            # Reescalar al tamaño de renderizado actual
            cx_label = cx_raw * rw / m['screen_w']
            cy_label = cy_raw * rh / m['screen_h']

            # Coordenadas dentro de la zona de imagen (sin letterbox)
            cx = cx_label - img_x
            cy = cy_label - img_y
        else:
            cx = img_w / 2 + yaw   * (img_w / 60)
            cy = img_h / 2 + pitch * (img_h / 40)
            cx_label = cx + img_x
            cy_label = cy + img_y

        # Coordenadas absolutas para el heatmap
        self.gaze_x = int(np.clip(cx_label, 0, rw - 1))
        self.gaze_y = int(np.clip(cy_label, 0, rh - 1))

        # Sector solo dentro de la zona real de imagen
        cx_c = np.clip(cx, 0, img_w - 1) if img_w > 0 else 0
        cy_c = np.clip(cy, 0, img_h - 1) if img_h > 0 else 0

        if img_w > 0 and img_h > 0:
            col = min(int(cx_c / img_w * COLS), COLS - 1)
            row = min(int(cy_c / img_h * ROWS), ROWS - 1)
            self.current_sector = row * COLS + col + 1
        else:
            self.current_sector = 1

    def on_frame(self, frame, current_time):
        self.current_time = current_time

        if self._video_h == 0 and frame is not None:
            self._video_h, self._video_w = frame.shape[:2]

        current_second = int(current_time)
        if current_second != self.last_logged_second:
            self.last_logged_second = current_second
            # Solo loguear si ya tenemos datos de gaze reales
            if self._gaze_initialized and self.current_sector >= 1:
                self.sector_log.append({
                    'tiempo_seg': current_second,
                    'sector':     self.current_sector,
                    'emocion':    self.current_emotion
                })
                self.timestamp_updated.emit(
                    current_time,
                    self.current_sector,
                    self.current_emotion
                )

                # ── Detección de AOI dinámica ───────────────────────────────
                if current_second in self.aoi_map:
                    for aoi_def in self.aoi_map[current_second]:
                        if self.current_sector in aoi_def['sectores']:
                            self.aoi_log.append({
                                'segundo':    current_second,
                                'aoi_id':     aoi_def['aoi_id'],
                                'aoi_nombre': aoi_def['aoi_nombre'],
                                'sector':     self.current_sector,
                                'emocion':    self.current_emotion,
                            })
                            print(f"[AOI] {aoi_def['aoi_nombre']} — "
                                  f"seg {current_second}s sector {self.current_sector} "
                                  f"emoción {self.current_emotion}")

        self._render_count += 1
        if self._render_count % 2 != 0:
            return

        win_w = self.video_label.width()  if self.video_label.width()  > 0 else 640
        win_h = self.video_label.height() if self.video_label.height() > 0 else 400

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qt_image = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        scaled = qt_image.scaled(
            win_w, win_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation
        )
        self.video_label.setPixmap(QPixmap.fromImage(scaled))

    def get_current_time(self):
        return self.current_time
