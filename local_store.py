"""Small atomic JSON stores scoped to the selected database; no credentials."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def scoped_path(database, folder, identity):
    return Path(database).resolve().with_suffix("." + folder) / (fingerprint(identity) + ".json")


def read_json(path, maximum=2_000_000):
    path = Path(path)
    if not path.exists():
        return None
    if path.stat().st_size > maximum:
        raise ValueError("保存的文件过大，原文件已保留。")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("保存的文件无法读取，原文件已保留。") from error


def write_json(path, document):
    path = Path(path)
    encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > 2_000_000:
        raise ValueError("保存内容过大，请缩短后再试。")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
