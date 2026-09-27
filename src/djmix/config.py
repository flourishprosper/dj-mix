"""Saved defaults: ~/.config/dj-mix/config.json

  {"brand": {"credits": [...], "watermark_text": ..., ...},
   "ui": {...last interface settings...}}

CLI flags override these; the interface saves what you last used.
"""
import json, os
from dataclasses import asdict, fields

from .brand import Brand

PATH = os.path.expanduser("~/.config/dj-mix/config.json")


def load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(cfg):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def brand_defaults():
    saved = load().get("brand", {})
    names = {f.name for f in fields(Brand)}
    return Brand(**{k: v for k, v in saved.items() if k in names})


def save_brand(brand):
    cfg = load()
    cfg["brand"] = asdict(brand)
    save(cfg)
