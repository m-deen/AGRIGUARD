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
            os.path.dirname(__file__), 'models', 'anomaly_model_v2.pkl'
        )

    def train(self):
        """Train Isolation Forest on synthetic normal/anomalous movement data."""
        print("Training anomaly detection model (4 features, mean heading)...")
        X_train = []

        # heading feature = rolling MEAN turn size (radians), not std-dev:
        # graze/walk ≈ 0.1–0.4 ; erratic zig-zag ≈ 1.8–2.8

        # ── Normal grazing patterns (70%) ────────────────────────────────────
        for _ in range(700):
            X_train.append([
                random.uniform(0.2, 4.5),   # speed km/h
                random.randint(4, 17),       # hour — daytime
                random.uniform(0, 800),      # dist — inside fence
                random.uniform(0.10, 0.40),  # mean turn — smooth drift
            ])

        # ── Known theft / anomaly indicators (30%) ───────────────────────────
        for _ in range(300):
            kind = random.random()

            if kind < 0.25:
                # High speed
                X_train.append([
                    random.uniform(9, 45),
                    random.randint(0, 23),
                    random.uniform(0, 1800),
                    random.uniform(0.10, 0.60),
                ])

            elif kind < 0.5: 
                # Night movement
                hour = random.choice(
                    list(range(0, 4)) + list(range(18, 24))
                )
                X_train.append([
                    random.uniform(1, 7.5),
                    hour,
                    random.uniform(0, 1500),
                    random.uniform(0.10, 0.60),
                ])

            elif kind < 0.75:
                # Geofence breach
                X_train.append([
                    random.uniform(1, 12),
                    random.randint(0, 23),
                    random.uniform(2200, 6000),
                    random.uniform(0.10, 0.60),
                ])

            else:
                # Erratic zig-zag — ordinary speed/position, large mean turn
                X_train.append([
                    random.uniform(0.5, 7.5),
                    random.randint(0, 23),
                    random.uniform(0, 1500),
                    random.uniform(1.80, 2.80),
                ])

        self.model = IsolationForest(
            contamination=0.15,     # 15% of data is anomalous
            random_state=42,        # reproducibility
            n_estimators=100        # number of trees in the forest
        )
        self.model.fit(X_train)     # train the model on synthetic data above

        # Save the trained model to disk for future use
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        joblib.dump(self.model, self.model_path)
        print(f"Model saved to {self.model_path}")
        return True

    def ensure_ready(self):
        """Load saved model, or train once if missing."""
        self._load_or_train()

    def _load_or_train(self):
        if self.model is not None:
            return
        if os.path.exists(self.model_path):
            try:
                self.model = joblib.load(self.model_path)
                print(f"[OK] Loaded anomaly model from {self.model_path}")
                return
            except Exception as e:
                print(f"[WARN] Could not load {self.model_path}: {e}")
        self.train()

    def predict(self, speed, hour, distance_from_center, geofence_radius=None,
                outside_zone=False, heading_variance=0.0):
        """
        Priority order (most objective first):
          1. Geofence Breach
          2. High Speed
          3. Night Movement  — night AND outside assigned zone
          4. Erratic Movement — high mean turn while speed/fence look normal
          5. Isolation Forest — subtler leftover patterns
        """
        radius = geofence_radius or self.DEFAULT_GEOFENCE_RADIUS
        mean_turn = float(heading_variance or 0.0)

        # ── P1: Geofence Breach ──────────────────────────────────────────────
        if distance_from_center > radius * 1.1:
            return {
                'is_anomaly':   True,
                'anomaly_type': 'Geofence Breach',
                'score':        1.0
            }

        # ── P2: High Speed ───────────────────────────────────────────────────
        if speed > self.SPEED_THRESHOLD:
            return {
                'is_anomaly':   True,
                'anomaly_type': 'High Speed',
                'score':        1.0
            }

        # ── P3: Night Movement — night AND out of zone ───────────────────────
        is_night = hour >= self.NIGHT_START or hour < self.NIGHT_END
        if is_night and outside_zone:
            return {
                'is_anomaly':   True,
                'anomaly_type': 'Night Movement',
                'score':        1.0
            }

        # ── P4: Erratic Movement (first-class heading rule) ──────────────────
        # Intentionally before Night so zig-zag inside fence at normal speed
        # is labelled Erratic, not buried under "ML leftover only".
        if mean_turn >= 1.2:
            return {
                'is_anomaly':   True,
                'anomaly_type': 'Erratic Movement',
                'score':        1.0
            }

        # ── P5: Isolation Forest — subtle / combined leftovers ───────────────
        self._load_or_train()
        features = np.array([[
            float(speed),
            float(hour),
            float(distance_from_center),
            mean_turn,
        ]])
        prediction = self.model.predict(features)
        score = float(self.model.score_samples(features)[0])

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


if __name__ == '__main__':
    AnomalyDetector().train()
