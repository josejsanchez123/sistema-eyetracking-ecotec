import json
import os

class AnnotationEngine:
    def __init__(self, annotation_path='videos/oldspice_anotacion.json'):
        self.anotacion = None
        if os.path.exists(annotation_path):
            with open(annotation_path, 'r', encoding='utf-8') as f:
                self.anotacion = json.load(f)
            print("Anotación del comercial cargada.")
        else:
            print("No se encontró archivo de anotación.")

    def get_elemento(self, segundo):
        if not self.anotacion:
            return None
        segundos = self.anotacion.get('segundos', {})
        return segundos.get(str(segundo), None)

    def generar_narrativa(self, sector_log):
        if not self.anotacion or not sector_log:
            return []

        narrativa = []
        for entry in sector_log:
            segundo = entry['tiempo_seg']
            sector = entry['sector']
            emocion = entry['emocion']
            elemento = self.get_elemento(segundo)

            if elemento:
                narrativa.append({
                    'segundo': segundo,
                    'sector': sector,
                    'emocion': emocion,
                    'escena': elemento['escena'],
                    'elemento_principal': elemento['elemento_principal'],
                    'descripcion': elemento['descripcion'],
                    'frase': self._generar_frase(segundo, sector, emocion, elemento)
                })

        return narrativa

    def _generar_frase(self, segundo, sector, emocion, elemento):
        elemento_nombre = {
            'actor': 'el actor principal',
            'producto': 'el producto Old Spice',
            'logo': 'el logo y texto de campaña',
            'ninguno': 'la pantalla'
        }.get(elemento['elemento_principal'], elemento['elemento_principal'])

        emocion_texto = {
            'happy': 'felicidad',
            'sad': 'tristeza',
            'angry': 'enojo',
            'fear': 'miedo',
            'surprise': 'sorpresa',
            'disgust': 'disgusto',
            'neutral': 'expresión neutral'
        }.get(emocion, emocion)

        escena_texto = {
            'baño': 'escena del baño',
            'barco': 'escena del barco',
            'playa': 'escena de la playa',
            'playa_caballo': 'escena final con caballo',
            'intro': 'introducción',
            'fin': 'cierre del comercial'
        }.get(elemento['escena'], elemento['escena'])

        return (f"En el segundo {segundo} ({escena_texto}), "
                f"el participante miraba {elemento_nombre} "
                f"(sector {sector}) y mostró {emocion_texto}.")

    def resumen_por_elemento(self, sector_log):
        if not self.anotacion or not sector_log:
            return {}

        resumen = {}
        for entry in sector_log:
            segundo = entry['tiempo_seg']
            emocion = entry['emocion']
            elemento_data = self.get_elemento(segundo)
            if not elemento_data:
                continue

            elem = elemento_data['elemento_principal']
            if elem not in resumen:
                resumen[elem] = {
                    'segundos_mirados': 0,
                    'emociones': {},
                    'escenas': set()
                }

            resumen[elem]['segundos_mirados'] += 1
            resumen[elem]['emociones'][emocion] = resumen[elem]['emociones'].get(emocion, 0) + 1
            resumen[elem]['escenas'].add(elemento_data['escena'])

        # Convertir sets a listas para serialización
        for elem in resumen:
            resumen[elem]['escenas'] = list(resumen[elem]['escenas'])
            emociones = resumen[elem]['emociones']
            if emociones:
                resumen[elem]['emocion_dominante'] = max(emociones, key=emociones.get)

        return resumen