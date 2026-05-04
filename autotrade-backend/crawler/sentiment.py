# FinBERT-based financial sentiment analysis using HuggingFace Transformers.
# The model is lazy-loaded on first use to avoid slowing startup.

from dataclasses import dataclass
from functools import lru_cache

from utils.logger import logger

FINBERT_MODEL = "ProsusAI/finbert"


@dataclass
class SentimentResult:
    label: str      # "positive" | "negative" | "neutral"
    score: float    # probability of the predicted label (0.0–1.0)
    normalised: float  # -1.0 (very negative) to +1.0 (very positive)


@lru_cache(maxsize=1)
def _load_pipeline():
    """Load FinBERT once and cache it for the process lifetime."""
    try:
        from transformers import pipeline
        logger.info(f"Loading FinBERT model '{FINBERT_MODEL}' — first call may take a moment")
        return pipeline(
            "text-classification",
            model=FINBERT_MODEL,
            tokenizer=FINBERT_MODEL,
            truncation=True,
            max_length=512,
        )
    except Exception as exc:
        logger.error(f"Failed to load FinBERT: {exc}. Sentiment will default to neutral.")
        return None


class SentimentAnalyser:

    def analyse(self, text: str) -> SentimentResult:
        """
        Run FinBERT on a single text string.
        Returns a neutral score if the model is unavailable.
        """
        pipe = _load_pipeline()
        if pipe is None or not text.strip():
            return SentimentResult(label="neutral", score=0.5, normalised=0.0)

        try:
            result = pipe(text[:512])[0]   # FinBERT returns [{label, score}]
            label = result["label"].lower()
            prob  = float(result["score"])

            if label == "positive":
                normalised = prob
            elif label == "negative":
                normalised = -prob
            else:
                normalised = 0.0

            return SentimentResult(label=label, score=prob, normalised=normalised)

        except Exception as exc:
            logger.error(f"Sentiment analysis failed: {exc}")
            return SentimentResult(label="neutral", score=0.5, normalised=0.0)

    def analyse_batch(self, texts: list[str]) -> list[SentimentResult]:
        """Analyse a list of texts and return one SentimentResult per entry."""
        return [self.analyse(t) for t in texts]

    def aggregate(self, results: list[SentimentResult]) -> float:
        """
        Average the normalised scores from multiple headlines.
        Returns a single float in [-1.0, +1.0].  0.0 if list is empty.
        """
        if not results:
            return 0.0
        return sum(r.normalised for r in results) / len(results)
