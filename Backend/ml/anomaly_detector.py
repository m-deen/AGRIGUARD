# ml/anomaly_detector.py
"""Hybrid theft anomaly detector: rule-based checks + Isolation Forest for edge cases."""
import os
import random
import joblib
import numpy as np
from sklearn.ensemble import IsolationForest


class AnomalyDetector:
    """Detects livestock theft indicators from GPS movement patterns."""

    SPEED_THRESHOLD         = 8     # km/h — above normal grazing / walking
    NIGHT_START             = 18    # 6 PM
    NIGHT_END               = 4     # 4 AM
    DEFAULT_GEOFENCE_RADIUS = 2000  # metres

    def __init__(self):
        self.model = None
        self.model_path = os.path.join(
            os.path.dirname(__file__), 'models', 'anomaly_model.pkl'
        )

    def train(self):
        """Train Isolation Forest on synthetic normal/anomalous movement data."""
        print("Training anomaly detection model...")
        X_train = []

        # ── Normal grazing patterns (70%) ────────────────────────────────────
        # Daytime (4AM–5PM), slow speed, well inside fence
        for _ in range(700):
            X_train.append([
                random.uniform(0.2, 4.5),   # speed km/h  — slow grazing
                random.randint(4, 17),       # hour        — daytime only
                random.uniform(0, 800),      # dist metres — inside fence
            ])

        # ── Known theft / anomaly indicators (30%) ───────────────────────────
        for _ in range(300):
            kind = random.random()

            if kind < 0.33:
                # High speed anomaly — fast movement at any hour
                X_train.append([
                    random.uniform(9, 45),        # speed above 8 km/h threshold
                    random.randint(0, 23),        # any hour
                    random.uniform(0, 1800),      # any position inside/near fence
                ])

            elif kind < 0.66:
                # Night movement anomaly — 18:00–03:59
                hour = random.choice(
                    list(range(0, 4)) + list(range(18, 24))
                )
                X_train.append([
                    random.uniform(1, 7.5),       # under speed threshold; night alone flags it
                    hour,
                    random.uniform(0, 1500),      # inside fence
                ])

            else:
                # Geofence breach — clearly outside boundary
                X_train.append([
                    random.uniform(1, 12),        # any speed
                    random.randint(0, 23),         # any hour
                    random.uniform(2200, 6000),    # outside 2000m fence
                ])

        self.model = IsolationForest(
            contamination=0.15,   # ~15% of data is anomalous
            random_state=42,
            n_estimators=100
        )
        self.model.fit(X_train)

        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        joblib.dump(self.model, self.model_path)
        print(f"Model saved to {self.model_path}")
        return True

    def _load_or_train(self):
        if self.model is not None:
            return
        if os.path.exists(self.model_path):
            try:
                self.model = joblib.load(self.model_path)
                return
            except Exception:
                pass
        self.train()

    def predict(self, speed, hour, distance_from_center, geofence_radius=None,
                outside_zone=False):
        """
        Check if a GPS reading indicates potential livestock theft.

        Priority order (most objective first):
          1. Geofence Breach — location based, most definitive
          2. High Speed      — physics based, undeniable
          3. Night Movement  — night AND animal outside its assigned zone
          4. ML model        — catches subtle combined patterns

        Args:
            speed:                Movement speed in km/h
            hour:                 Hour of day (0-23)
            distance_from_center: Distance from geofence centre in metres
            geofence_radius:      Allowed radius in metres (default 2000m)
            outside_zone:         True if animal is outside its assigned zone

        Returns:
            dict — is_anomaly (bool), anomaly_type (str|None), score (float)
        """
        radius = geofence_radius or self.DEFAULT_GEOFENCE_RADIUS

        # ── P1: Geofence Breach (location — most objective) ──────────────────
        if distance_from_center > radius * 1.1:
            return {
                'is_anomaly':   True,
                'anomaly_type': 'Geofence Breach',
                'score':        1.0
            }

        # ── P2: High Speed (physics — undeniable) ────────────────────────────
        if speed > self.SPEED_THRESHOLD:
            return {
                'is_anomaly':   True,
                'anomaly_type': 'High Speed',
                'score':        1.0
            }

        # ── P3: Night Movement — only if out of zone during night ────────────
        is_night = hour >= self.NIGHT_START or hour < self.NIGHT_END
        if is_night and outside_zone:
            return {
                'is_anomaly':   True,
                'anomaly_type': 'Night Movement',
                'score':        1.0
            }

        # ── P4: ML model — subtle / combined pattern detection ────────────────
        self._load_or_train()
        features   = np.array([[speed, hour, distance_from_center]])
        prediction = self.model.predict(features)
        score      = float(self.model.score_samples(features)[0])

        if prediction[0] == -1:
            return {
                'is_anomaly':   True,
                'anomaly_type': 'Erratic Movement',
                'score':        score
            }

        return {
            'is_anomaly':   False,
            'anomaly_type': None,
            'score':        score
        }