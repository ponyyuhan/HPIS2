"""Wire format, authenticated context encryption, and the two search indexes."""

import hmac
import os
import struct
import unicodedata

import msgpack
import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

F32 = np.dtype("<f4")
_MIN_NORMAL_NORM = np.sqrt(np.finfo(np.float64).tiny)
MAX_DIM = 4096
MAX_ID = 2**63 - 1


def pack(value):
    return msgpack.packb(value, use_bin_type=True)


def unpack(blob):
    return msgpack.unpackb(blob, raw=False)


def record_id(value):
    if type(value) is not int or not 0 <= value <= MAX_ID:
        raise ValueError("record id must be a nonnegative signed 64-bit integer")
    return value


def encrypt_item(cipher, purpose, rid, value):
    nonce = os.urandom(12)
    return nonce + cipher.encrypt(nonce, value, purpose + struct.pack(">Q", rid))


def decrypt_item(cipher, purpose, rid, value):
    return cipher.decrypt(value[:12], value[12:], purpose + struct.pack(">Q", rid))


def normalized_text(value):
    if not isinstance(value, str):
        raise ValueError("index values must be strings")
    return unicodedata.normalize("NFKC", value).casefold()


def field_label(field, value):
    return pack(["field", normalized_text(field), normalized_text(value)])


def word_label(word):
    return pack(["word", word])


def spelling_labels(word, *, building):
    # Removing the same position identifies a substitution. A deletion lookup
    # in the opposite-length index identifies insertion/deletion exactly.
    yield word_label(word)
    if not building:
        yield pack(["deleted", word])
    for i in range(len(word)):
        shortened = word[:i] + word[i + 1 :]
        yield pack(["hole", i, shortened])
        yield pack(["deleted", shortened]) if building else word_label(shortened)


class Labels:
    def __init__(self, key=None):
        self.key = key

    def encode(self, label):
        return hmac.digest(self.key, label, "sha256") if self.key is not None else label

    def index(self, fields, words):
        labels = {field_label(f, v) for f, v in fields.items()}
        for word in words:
            labels.update(spelling_labels(normalized_text(word), building=True))
        return [self.encode(label) for label in labels]

    def predicate(self, kind, *args):
        if kind == "field":
            labels = [field_label(*args)]
        elif kind == "word":
            labels = [word_label(normalized_text(args[0]))]
        elif kind == "fuzzy":
            labels = set(spelling_labels(normalized_text(args[0]), building=False))
        else:
            raise ValueError("unknown predicate")
        return [self.encode(label) for label in labels]


def unit_vectors(values, dim):
    vectors = np.asarray(values, dtype=np.float64)
    if vectors.ndim not in (1, 2) or vectors.shape[-1] != dim:
        raise ValueError("wrong vector dimension")
    lengths = np.linalg.norm(vectors, axis=-1, keepdims=True)
    if not np.all(np.isfinite(lengths) & (lengths >= _MIN_NORMAL_NORM)):
        # Squaring very small coordinates can have large *relative* error even
        # when the unscaled norm is nonzero (subnormal squared lengths).
        scale = np.max(np.abs(vectors), axis=-1, keepdims=True)
        if not np.all(np.isfinite(scale) & (scale > 0)):
            raise ValueError("expected finite, nonzero vectors")
        vectors = vectors / scale
        lengths = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return np.ascontiguousarray(vectors / lengths, dtype=F32)


def uniforms(shape):
    # Open-interval uniforms from OS random bits. Avoid a predictable numerical RNG.
    count = int(np.prod(shape))
    bits = np.frombuffer(os.urandom(8 * count), dtype=np.uint64) >> np.uint64(12)
    return ((bits.astype(np.float64) + 0.5) * 2.0**-52).reshape(shape)


def gaussian(shape):
    pairs = (int(np.prod(shape)) + 1) // 2
    u, v = uniforms((2, pairs))
    radius = np.sqrt(-2.0 * np.log(u))
    angle = 2 * np.pi * v
    return (
        np.stack((radius * np.cos(angle), radius * np.sin(angle)), axis=-1)
        .ravel()[: int(np.prod(shape))]
        .reshape(shape)
    )


def rotation(dim):
    q, r = np.linalg.qr(gaussian((dim, dim)))
    return np.ascontiguousarray(q * np.where(np.diag(r) < 0, -1, 1), dtype=F32)


class VectorCodec:
    """Rotate-and-Perturb on normalized vectors. The cloud only needs the sketch."""

    def __init__(self, dim, beta, matrix=None):
        self.dim, self.beta, self.matrix = dim, beta, matrix

        self._noise = np.empty((0, dim), dtype=F32)
        self._pid = os.getpid()

    def noise(self, shape):
        noise = gaussian(shape)
        length = np.linalg.norm(noise, axis=-1, keepdims=True)
        radius = self.beta / 4 * uniforms(length.shape) ** (1 / self.dim)
        return np.ascontiguousarray(noise * (radius / length), dtype=F32)

    def sketch(self, unit):
        if self.matrix is None:
            return unit
        if unit.ndim == 2:
            return np.ascontiguousarray(
                unit @ self.matrix + self.noise(unit.shape), dtype=F32
            )
        # Sample independent query noise in batches. Refill is charged to the
        # calling query. Never reuse a sample, including across a process fork.
        pid = os.getpid()
        if not len(self._noise) or pid != self._pid:
            self._noise = self.noise((32, self.dim))
            self._pid = pid
        noise, self._noise = self._noise[-1], self._noise[:-1]
        return np.ascontiguousarray(unit @ self.matrix + noise, dtype=F32)


def nearest(ids, distances, k):
    """Partial selection with stable id ordering, including ties at the cutoff."""
    count = min(k, len(ids))
    if count == 0:
        return np.empty(0, dtype=np.intp)
    threshold = np.partition(distances, count - 1)[count - 1]
    candidates = np.flatnonzero(distances <= threshold)
    return candidates[np.lexsort((ids[candidates], distances[candidates]))[:count]]


def context_key(key):
    return AESGCM(key) if key is not None else None
