"""本地词向量及可预装的多语言 E5 编码器。只接收本机绝对路径。"""
from __future__ import annotations

import importlib
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from .retrieval import _terms


def normalize(values: list[float]) -> list[float]:
    if not values or len(values) > 8192 or not all(math.isfinite(v) for v in values):
        raise ValueError("向量格式无效")
    norm = math.sqrt(math.fsum(v * v for v in values))
    if not math.isfinite(norm):
        raise ValueError("向量范数无效")
    return [v / norm for v in values] if norm else values


def model_files(path: Path) -> tuple[Path, ...]:
    if path.suffix.lower() == ".vec" and path.is_file():
        return (path,)
    return (path / "tokenizer.json", path / "model.onnx")


@lru_cache(maxsize=1)
def _load(model: str, fingerprint: str) -> Any:
    path = Path(model)
    files = model_files(path)
    if len(files) == 1:
        words: dict[str, list[float]] = {}
        with path.open(encoding="utf-8") as stream:
            count, size = map(int, stream.readline().split())
            if not 0 < size <= 8192 or count < 1:
                raise ValueError("词向量文件声明无效")
            for line in stream:
                fields = line.split()
                if len(fields) != size + 1:
                    raise ValueError("词向量行长度不匹配")
                words[fields[0].casefold()] = normalize(list(map(float, fields[1:])))
        if len(words) != count:
            raise ValueError("词向量文件不完整")
        return words
    runtime = importlib.import_module("onnxruntime")
    tokenizer = importlib.import_module("tokenizers").Tokenizer.from_file(str(files[0]))
    tokenizer.enable_truncation(max_length=512)
    session = runtime.InferenceSession(str(files[1]), providers=["CPUExecutionProvider"])
    return tokenizer, session


def encode_local(model: str, fingerprint: str, texts: list[str], *, query: bool) -> list[list[float]]:
    encoder = _load(model, fingerprint)
    if isinstance(encoder, dict):
        result = []
        size = len(next(iter(encoder.values())))
        for text in texts:
            matched = [encoder[t] for t in sorted(_terms(text)) if t in encoder]
            result.append(normalize([math.fsum(column) for column in zip(*matched)]) if matched else [0.] * size)
        return result
    np = importlib.import_module("numpy")
    tokenizer, session = encoder
    result = []
    for text in texts:
        encoding = tokenizer.encode(("query: " if query else "passage: ") + text)
        inputs = {"input_ids": encoding.ids, "attention_mask": encoding.attention_mask,
                  "token_type_ids": encoding.type_ids}
        feed = {node.name: np.asarray([inputs[node.name]], dtype=np.int64) for node in session.get_inputs()}
        tokens = session.run(None, feed)[0]
        mask = np.asarray(encoding.attention_mask, dtype=np.float32)
        if tokens.ndim != 3 or tokens.shape[1] != len(mask):
            raise ValueError("本地模型不是 E5 词元编码器")
        pooled = (tokens[0] * mask[:, None]).sum(axis=0) / max(1, mask.sum())
        result.append(normalize(pooled.tolist()))
    return result
