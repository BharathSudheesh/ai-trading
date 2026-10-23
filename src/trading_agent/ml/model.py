"""Up/down probability model (Random Forest or XGBoost) and its on-disk registry."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, precision_score, recall_score
from sklearn.metrics import roc_auc_score

from ..config import Settings
from ..data import MarketDataProvider
from .features import FEATURE_COLUMNS, build_dataset

log = logging.getLogger(__name__)

MIN_TRAIN_ROWS = 300


def _make_estimator(algorithm: str, seed: int):
    if algorithm == "xgboost":
        from xgboost import XGBClassifier

        return XGBClassifier(
            n_estimators=250, max_depth=3, learning_rate=0.05, subsample=0.8,
            colsample_bytree=0.8, min_child_weight=10, eval_metric="logloss",
            random_state=seed, n_jobs=2,
        )
    return RandomForestClassifier(
        n_estimators=200, max_depth=6, min_samples_leaf=30, class_weight="balanced_subsample",
        n_jobs=-1, random_state=seed,
    )


class SignalModel:
    """Wraps a fitted classifier plus the metadata needed to audit it."""

    def __init__(self, estimator: Any, metadata: dict[str, Any]):
        self.estimator = estimator
        self.metadata = metadata
        self.feature_columns: list[str] = list(metadata.get("feature_columns", FEATURE_COLUMNS))

    def predict_proba_up(self, features: pd.DataFrame) -> np.ndarray:
        """Probability that the price is higher ``horizon_days`` from now."""
        X = features[self.feature_columns].to_numpy(dtype=float)
        return self.estimator.predict_proba(X)[:, 1]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"estimator": self.estimator, "metadata": self.metadata}, path)

    @classmethod
    def load(cls, path: Path) -> SignalModel:
        # Only load model files that this application created (joblib uses pickle).
        blob = joblib.load(path)
        return cls(blob["estimator"], blob["metadata"])


def _evaluate(data: pd.DataFrame, algorithm: str, horizon: int, seed: int,
              neutral_band: float) -> dict[str, Any] | None:
    dates = np.sort(data.index.unique())
    if len(dates) < 250:
        return None
    cut_i = int(len(dates) * 0.8)
    cut = dates[cut_i]
    train = data[data.index <= dates[max(0, cut_i - horizon)]]  # purge overlapping labels
    test = data[data.index > cut]
    if len(train) < MIN_TRAIN_ROWS or test.empty:
        return None

    est = _make_estimator(algorithm, seed)
    est.fit(train[FEATURE_COLUMNS].to_numpy(), train["target"].to_numpy())
    p = est.predict_proba(test[FEATURE_COLUMNS].to_numpy())[:, 1]
    y = test["target"].to_numpy()
    pred = (p >= 0.5).astype(float)

    base = float(max(y.mean(), 1 - y.mean()))
    outside = np.abs(p - 0.5) >= neutral_band
    signal_acc = float(((p[outside] >= 0.5) == (y[outside] == 1)).mean()) if outside.any() else None
    return {
        "accuracy": float(accuracy_score(y, pred)),
        "baseline_accuracy": base,
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
        "brier": float(brier_score_loss(y, p)),
        "signal_accuracy": signal_acc,
        "signal_coverage": float(outside.mean()),
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "test_start": pd.Timestamp(test.index.min()).date().isoformat(),
        "test_end": pd.Timestamp(test.index.max()).date().isoformat(),
    }


def train_model(
    frames: dict[str, pd.DataFrame],
    *,
    algorithm: str,
    horizon: int,
    seed: int = 42,
    neutral_band: float = 0.03,
    evaluate: bool = True,
) -> SignalModel:
    """Fit on every labelled row; optionally report a chronological hold-out evaluation."""
    data = build_dataset(frames, horizon).dropna(subset=[*FEATURE_COLUMNS, "target"])
    if len(data) < MIN_TRAIN_ROWS:
        raise ValueError(f"Need at least {MIN_TRAIN_ROWS} labelled rows to train, got {len(data)}")

    metrics = _evaluate(data, algorithm, horizon, seed, neutral_band) if evaluate else None

    est = _make_estimator(algorithm, seed)
    est.fit(data[FEATURE_COLUMNS].to_numpy(), data["target"].to_numpy())

    importances = sorted(
        zip(FEATURE_COLUMNS, est.feature_importances_, strict=True), key=lambda kv: -kv[1]
    )[:8]
    data_end = max(pd.Timestamp(f.index.max()) for f in frames.values()).date()
    metadata = {
        "algorithm": algorithm,
        "horizon_days": horizon,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "data_end": data_end.isoformat(),
        "symbols": sorted(frames),
        "n_samples": int(len(data)),
        "feature_columns": FEATURE_COLUMNS,
        "metrics": metrics,
        "top_features": [{"feature": k, "importance": float(v)} for k, v in importances],
    }
    return SignalModel(est, metadata)


class ModelStore:
    """Loads the saved model (training one on first use) and builds point-in-time models.

    A recommendation generated "as of" a past date must not use a model that has seen
    prices after that date, so a separate model is trained on data up to that date.
    """

    def __init__(self, settings: Settings, provider: MarketDataProvider):
        self.settings = settings
        self.provider = provider
        self._model: SignalModel | None = None
        self._pit: dict[tuple[date, tuple[str, ...]], SignalModel] = {}
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self.settings.model_dir / "signal_model.joblib"

    def load_frames(self, symbols: list[str], end: date) -> dict[str, pd.DataFrame]:
        start = end - timedelta(days=int(self.settings.history_years * 365.25))
        return {s: self.provider.get_history(s, start, end) for s in symbols}

    def _train(self, symbols: list[str], end: date) -> SignalModel:
        frames = self.load_frames(symbols, end)
        return train_model(
            frames,
            algorithm=self.settings.ml_algorithm,
            horizon=self.settings.horizon_days,
            seed=self.settings.random_seed,
            neutral_band=self.settings.neutral_band,
        )

    def train(self, symbols: list[str] | None = None, persist: bool = True) -> SignalModel:
        symbols = symbols or self.settings.default_symbols
        with self._lock:
            model = self._train(symbols, date.today())
            if persist:
                self.settings.ensure_dirs()
                model.save(self.path)
            self._model = model
            self._pit.clear()
        return model

    def info(self) -> dict[str, Any] | None:
        model = self._model
        if model is None and self.path.exists():
            model = self._model = SignalModel.load(self.path)
        return model.metadata if model else None

    def get(self, as_of: date | None = None, symbols: list[str] | None = None) -> SignalModel:
        with self._lock:
            if self._model is None and self.path.exists():
                self._model = SignalModel.load(self.path)
        if self._model is None:
            self.train()
        assert self._model is not None

        data_end = date.fromisoformat(self._model.metadata["data_end"])
        if as_of is None or as_of >= data_end:
            return self._model

        key = (as_of, tuple(sorted(symbols or self.settings.default_symbols)))
        with self._lock:
            if key not in self._pit:
                log.info("Training point-in-time model for %s", as_of)
                self._pit[key] = self._train(list(key[1]), as_of)
            return self._pit[key]
