"""Search vocabulary from config/search.json."""
import json
import pathlib

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "config"


def search_config():
    c = json.loads((CONFIG / "search.json").read_text())
    return {
        "synonyms": {k.lower(): [v.lower() for v in vs] for k, vs in c.get("synonyms", {}).items()},
        "make_aliases": {k.lower(): v for k, v in c.get("make_aliases", {}).items()},
        "stop_words": {w.lower() for w in c.get("stop_words", [])},
        "generic_model_words": {w.lower() for w in c.get("generic_model_words", [])},
    }
