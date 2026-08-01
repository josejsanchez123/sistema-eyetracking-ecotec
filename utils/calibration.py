import numpy as np
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QColor, QFont, QPen


class CalibrationWidget(QWidget):
    calibration_finished = pyqtSignal(object)

    POINTS_RELATIVE = [
        (0.1, 0.1), (0.5, 0.1), (0.9, 0.1),
        (0.1, 0.5), (0.5, 0.5), (0.9, 0.5),
        (0.1, 0.9), (0.5, 0.9), (0.9, 0.9),
    ]

    # Puntos extremos (filas 1 y 3) necesitan más tiempo
    COLLECT_MS_CORNER = 3500   # esquinas y bordes extremos
    COLLECT_MS_CENTER = 2000   # fila central
    CORNER_INDICES    = {0, 1, 2, 6, 7, 8}  # filas 1 y 3
    CONFIRM_MS        = 600

    def __init__(self, controller, parent_widget=None, screen_index=0, parent=None):
        if parent_widget is not None:
            super().__init__(parent_widget)
            self.setGeometry(parent_widget.rect())
            self.setStyleSheet("background-color: black;")
            self.show()
            self.raise_()
            self._fullscreen_mode = False
        else:
            super().__init__(parent, Qt.WindowType.Window)
            self.setWindowFlags(
                Qt.WindowType.Window |
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint
            )
            self.setStyleSheet("background-color: black;")
            screens = QApplication.screens()
            screen = screens[screen_index] if screen_index < len(screens) else screens[0]
            self.setGeometry(screen.geometry())
            self.showFullScreen()
            self.raise_()
            self.activateWindow()
            self._fullscreen_mode = True

        self.controller       = controller
        self.current_point    = 0
        self.gaze_samples     = []
        self.calibration_data = []
        self.confirmed_points = []
        self.show_instruction = True
        self.point_confirmed  = False

        # Estado de verificación post-calibración
        self._verifying       = False
        self._verify_samples  = []
        self._verify_result   = None  # None / 'ok' / 'warn'

        self.timer = QTimer()
        self.timer.timeout.connect(self.collect_gaze)

        self.instruction_timer = QTimer()
        self.instruction_timer.setSingleShot(True)
        self.instruction_timer.timeout.connect(self.start_calibration)
        self.instruction_timer.start(2500)

    # ── colección de muestras ──────────────────────────────────────

    def _collect_ms(self):
        """Devuelve el tiempo de recolección según si el punto es esquina o central."""
        return (self.COLLECT_MS_CORNER
                if self.current_point in self.CORNER_INDICES
                else self.COLLECT_MS_CENTER)

    def start_calibration(self):
        self.show_instruction = False
        self.current_point    = 0
        self.calibration_data = []
        self.confirmed_points = []
        self.show_point()

    def show_point(self):
        self.gaze_samples    = []
        self.point_confirmed = False
        self.update()
        self.timer.start(100)
        QTimer.singleShot(self._collect_ms(), self.finish_point)

    def collect_gaze(self):
        yaw, pitch = self.controller.get_last_gaze()
        self.gaze_samples.append((yaw, pitch))

    def finish_point(self):
        self.timer.stop()
        if self.gaze_samples:
            yaws    = [g[0] for g in self.gaze_samples]
            pitches = [g[1] for g in self.gaze_samples]
            avg_yaw   = np.median(yaws)
            avg_pitch = np.median(pitches)
            w = self.width()
            h = self.height()
            px, py = self.POINTS_RELATIVE[self.current_point]
            self.calibration_data.append({
                'screen_x': px * w,
                'screen_y': py * h,
                'yaw':      avg_yaw,
                'pitch':    avg_pitch
            })

        self.confirmed_points.append(self.current_point)
        self.point_confirmed = True
        self.update()

        self.current_point += 1
        if self.current_point >= len(self.POINTS_RELATIVE):
            QTimer.singleShot(self.CONFIRM_MS, self._start_verification)
        else:
            QTimer.singleShot(self.CONFIRM_MS, self.show_point)

    # ── verificación post-calibración ──────────────────────────────

    def _start_verification(self):
        """Muestra el punto central y verifica que la mirada caiga en sectores centrales."""
        self._verifying      = True
        self._verify_samples = []
        self._verify_result  = None
        self.update()
        self.timer.timeout.disconnect()
        self.timer.timeout.connect(self._collect_verify)
        self.timer.start(100)
        QTimer.singleShot(2000, self._finish_verification)

    def _collect_verify(self):
        yaw, pitch = self.controller.get_last_gaze()
        self._verify_samples.append((yaw, pitch))

    def _finish_verification(self):
        self.timer.stop()
        matrix = self._compute_calibration()

        if matrix and self._verify_samples:
            yaws    = [g[0] for g in self._verify_samples]
            pitches = [g[1] for g in self._verify_samples]
            avg_yaw   = np.median(yaws)
            avg_pitch = np.median(pitches)

            src = np.array([avg_yaw, avg_pitch, 1.0], dtype=np.float32)
            cx  = float(np.dot(matrix['x'], src))
            cy  = float(np.dot(matrix['y'], src))
            w   = matrix['screen_w']
            h   = matrix['screen_h']

            col = min(int(cx / w * 4), 3)
            row = min(int(cy / h * 4), 3)
            sector = row * 4 + col + 1
            # Sectores centrales esperados: 5,6,7,8,9,10,11,12 (filas 2 y 3)
            central_sectors = {5, 6, 7, 8, 9, 10, 11, 12}
            self._verify_result = 'ok' if sector in central_sectors else 'warn'
            self._verify_sector = sector
        else:
            self._verify_result = 'warn'
            self._verify_sector = -1

        self.update()
        QTimer.singleShot(2500, self._emit_done)

    def _emit_done(self):
        matrix = self._compute_calibration()
        self.hide()
        self.deleteLater()
        self.calibration_finished.emit(matrix)

    # ── calibración ────────────────────────────────────────────────

    def _compute_calibration(self):
        if len(self.calibration_data) < 4:
            return None
        try:
            src = np.array(
                [[d['yaw'], d['pitch']] for d in self.calibration_data],
                dtype=np.float32
            )
            dst = np.array(
                [[d['screen_x'], d['screen_y']] for d in self.calibration_data],
                dtype=np.float32
            )
            ones    = np.ones((len(src), 1), dtype=np.float32)
            src_aug = np.hstack([src, ones])
            result_x, _, _, _ = np.linalg.lstsq(src_aug, dst[:, 0], rcond=None)
            result_y, _, _, _ = np.linalg.lstsq(src_aug, dst[:, 1], rcond=None)
            return {
                'x':        result_x,
                'y':        result_y,
                'screen_w': self.width(),
                'screen_h': self.height()
            }
        except Exception as e:
            print(f"Error en calibración: {e}")
            return None

    # ── dibujo ─────────────────────────────────────────────────────

    def _draw_point(self, painter, cx, cy, state):
        if state == 'active_green':
            ring_color  = QColor(0, 255, 100)
            inner_color = QColor(0, 210, 80)
            cross_color = QColor(0, 0, 0)
            ring_r, inner_r = 25, 12
        elif state == 'active_red':
            ring_color  = QColor(255, 80, 80)
            inner_color = QColor(220, 40, 40)
            cross_color = QColor(0, 0, 0)
            ring_r, inner_r = 25, 12
        elif state == 'verify':
            ring_color  = QColor(255, 220, 0)
            inner_color = QColor(220, 180, 0)
            cross_color = QColor(0, 0, 0)
            ring_r, inner_r = 28, 14
        else:  # done
            ring_color  = QColor(80, 80, 80)
            inner_color = QColor(60, 60, 60)
            cross_color = QColor(120, 120, 120)
            ring_r, inner_r = 12, 5

        painter.setPen(QPen(ring_color, 2))
        painter.setBrush(QColor(0, 0, 0, 0))
        painter.drawEllipse(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(inner_color)
        painter.drawEllipse(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2)
        painter.setPen(QPen(cross_color, 2))
        painter.drawLine(cx - 6, cy, cx + 6, cy)
        painter.drawLine(cx, cy - 6, cx, cy + 6)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        w = self.width()
        h = self.height()

        # ── pantalla de instrucción ──
        if self.show_instruction:
            painter.setPen(QColor(255, 255, 255))
            fs = 18 if self._fullscreen_mode else 13
            painter.setFont(QFont("Arial", fs, QFont.Weight.Bold))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                "CALIBRACIÓN\n\n"
                "Mire cada punto rojo naturalmente\n"
                "(mueva cabeza y ojos hacia él)\n"
                "y espere hasta que cambie a verde.\n\n"
                "Los puntos de esquinas duran un poco más."
            )
            return

        # ── pantalla de verificación ──
        if self._verifying:
            cx, cy = w // 2, h // 2
            if self._verify_result is None:
                # Aún recolectando — mostrar punto amarillo central
                self._draw_point(painter, cx, cy, 'verify')
                painter.setPen(QColor(255, 220, 0))
                painter.setFont(QFont("Arial", 13, QFont.Weight.Bold))
                painter.drawText(
                    self.rect(), Qt.AlignmentFlag.AlignCenter,
                    "\n\n\n\n\n\nVerificación — mire el centro"
                )
            elif self._verify_result == 'ok':
                painter.setPen(QColor(0, 220, 80))
                painter.setFont(QFont("Arial", 18, QFont.Weight.Bold))
                painter.drawText(
                    self.rect(), Qt.AlignmentFlag.AlignCenter,
                    "✓ Calibración correcta\n\nSector detectado: central"
                )
            else:
                painter.setPen(QColor(255, 160, 0))
                painter.setFont(QFont("Arial", 16, QFont.Weight.Bold))
                painter.drawText(
                    self.rect(), Qt.AlignmentFlag.AlignCenter,
                    f"⚠ Calibración imprecisa\n\n"
                    f"Sector detectado: {self._verify_sector}\n"
                    f"(se esperaba sector central)\n\n"
                    f"Puedes recalibrar si los resultados\nno son satisfactorios."
                )
            return

        # ── puntos de calibración ──
        for idx in self.confirmed_points:
            if self.point_confirmed and idx == self.current_point - 1:
                continue
            px, py = self.POINTS_RELATIVE[idx]
            self._draw_point(painter, int(px * w), int(py * h), 'done')

        if self.current_point < len(self.POINTS_RELATIVE):
            if self.point_confirmed:
                idx = self.current_point - 1
                if 0 <= idx < len(self.POINTS_RELATIVE):
                    px, py = self.POINTS_RELATIVE[idx]
                    self._draw_point(painter, int(px * w), int(py * h), 'active_green')
            else:
                px, py = self.POINTS_RELATIVE[self.current_point]
                self._draw_point(painter, int(px * w), int(py * h), 'active_red')

        # Contador + indicador de tiempo extra en esquinas
        painter.setPen(QColor(150, 150, 150))
        painter.setFont(QFont("Arial", 11))
        total = len(self.POINTS_RELATIVE)
        done  = len(self.confirmed_points)
        if self.point_confirmed and done > 0:
            label = f"Punto {done} de {total} ✓"
        elif self.current_point < total:
            es_esquina = self.current_point in self.CORNER_INDICES
            extra = " (esquina — mantén la mirada)" if es_esquina else ""
            label = f"Punto {self.current_point + 1} de {total}{extra}"
        else:
            label = "Calibración completada ✓"
        painter.drawText(15, 25, label)