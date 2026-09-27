from enum import Enum


class AnalystType(str, Enum):
    MARKET = "market"
    # Wire value stays "social" for saved-config and string-keyed-caller
    # back-compat; the user-facing label is "Sentiment Analyst".
    SOCIAL = "social"
    SENTIMENT = "social"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"
    FOREX_TECHNICAL = "forex_technical"
    FOREX_MACRO = "forex_macro"
    FOREX_NEWS = "forex_news"


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"
    FOREX = "forex"
