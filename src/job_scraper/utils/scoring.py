from __future__ import annotations

import html
import re
from typing import Iterable, Tuple


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

AI_ML_PHRASES = {
    "machine learning": 0.35,
    "ml engineer": 0.25,
    "artificial intelligence": 0.3,
    "ai engineer": 0.25,
    "llm": 0.25,
    "large language model": 0.3,
    "data science": 0.2,
    "deep learning": 0.25,
    "computer vision": 0.2,
    "nlp": 0.2,
    "inference": 0.15,
    "model serving": 0.2,
    "retrieval": 0.1,
    "training pipeline": 0.15,
}

STARTUP_PHRASES = {
    "series a": 0.25,
    "series b": 0.2,
    "seed stage": 0.3,
    "fast-paced": 0.1,
    "startup": 0.2,
    "founding": 0.3,
    "0 to 1": 0.2,
    "venture-backed": 0.2,
}


def strip_html(raw_text: str) -> str:
    if not raw_text:
        return ""
    text = TAG_RE.sub(" ", raw_text)
    text = html.unescape(text)
    return SPACE_RE.sub(" ", text).strip()


def normalize_text(*parts: str) -> str:
    return " ".join(part.strip().lower() for part in parts if part).strip()


def _score_phrases(text: str, phrases: Iterable[Tuple[str, float]]) -> float:
    score = 0.0
    for phrase, weight in phrases:
        if phrase in text:
            score += weight
    return min(score, 1.0)


def score_ai_ml_relevance(title: str, description_text: str) -> Tuple[float, bool]:
    text = normalize_text(title, description_text)
    score = _score_phrases(text, AI_ML_PHRASES.items())
    title_text = title.lower()
    if any(keyword in title_text for keyword in ("machine learning", "ml", "ai", "data")):
        score = min(score + 0.15, 1.0)
    return score, score >= 0.35


def score_startup_fit(company_name: str, description_text: str, startup_bias: float) -> float:
    text = normalize_text(company_name, description_text)
    score = _score_phrases(text, STARTUP_PHRASES.items())
    score = min(score + max(min(startup_bias, 1.0), 0.0) * 0.5, 1.0)
    return score


def classify_remote_type(location_raw: str, description_text: str) -> str:
    text = normalize_text(location_raw, description_text)
    if "hybrid" in text:
        return "hybrid"
    if "remote" in text or "work from home" in text or "distributed" in text:
        return "remote"
    if location_raw:
        return "onsite"
    return "unknown"


def split_location(location_raw: str) -> Tuple[str, str]:
    if not location_raw:
        return "", ""
    parts = [part.strip() for part in location_raw.split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return parts[0], ""


def compute_overall_score(ai_ml_score: float, startup_score: float, remote_type: str) -> float:
    remote_bonus = 0.1 if remote_type == "remote" else 0.05 if remote_type == "hybrid" else 0.0
    return min(ai_ml_score * 0.65 + startup_score * 0.25 + remote_bonus, 1.0)
