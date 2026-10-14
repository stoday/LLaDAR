"""Host-owned, length-preserving projections of known private wire literals.

Only the returned ``wire_safe`` may enter an untrusted protocol interpreter.
Original network bytes remain the authority; their separate hash is not a claim
that a projected or decoded envelope retains protocol semantics. This screen
cannot identify private data in unknown compression, encryption or encodings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import base64
import hashlib
import json
import re
import secrets
from urllib.parse import quote_from_bytes

from .resource_limits import resource_limit


_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_ASCII_RANDOM = bytes(_ALPHABET[index % len(_ALPHABET)] for index in range(256))


class ProtocolProjectionError(RuntimeError):
    """Closed public reasons; no original values or candidate strings."""
    def __init__(self, code):
        self.reason = self.code = code if type(code) is str and code in {
            "protocol_projection_input_invalid", "protocol_projection_limit",
            "protocol_projection_unavailable",
        } else "protocol_projection_unavailable"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class BinaryProtocolProjection:
    wire_safe: bytes = field(repr=False)
    original_sha256: str
    projection_sha256: str
    refs_safe: tuple[dict, ...]
    # Host-only screening values. Never include these in model-facing metadata,
    # and never treat an answer containing them as the unchanged original text.
    masked_literals: tuple[str, ...] = field(repr=False)
    version: str = "known-private-byte-projection-v1"


def private_literal_forms(literal: bytes) -> dict[bytes, str]:
    """Host-only common whole-value encodings, not arbitrary encoder chains."""
    result = {literal: "raw"}
    for encoder, kind in ((base64.b64encode, "base64"), (base64.urlsafe_b64encode, "base64url")):
        encoded = encoder(literal)
        result.setdefault(encoded, kind)
        result.setdefault(encoded.rstrip(b"="), kind + "-unpadded")
    result.setdefault(literal.hex().encode(), "hex")
    result.setdefault(literal.hex().upper().encode(), "hex-upper")
    quoted = quote_from_bytes(literal, safe="").encode()
    urls = {quoted, quoted.replace(b"%20", b"+"),
        b"".join(f"%{byte:02X}".encode() for byte in literal)}
    for encoded in tuple(urls):
        urls.add(re.sub(rb"%[0-9A-F]{2}", lambda match: match[0].lower(), encoded))
    for encoded in urls:
        result.setdefault(encoded, "url")
    text = literal.decode("utf-8")
    for ensure_ascii in (True, False):
        encoded = json.dumps(text, ensure_ascii=ensure_ascii)[1:-1].encode()
        for _ in range(2):
            uppercase = re.sub(rb"\\u[0-9a-f]{4}", lambda match: b"\\u" + match[0][2:].upper(), encoded)
            for variant in (encoded, uppercase):
                result.setdefault(variant, "json")
                result.setdefault(variant.replace(b"/", b"\\/"), "json")
            encoded = json.dumps(encoded.decode("utf-8"), ensure_ascii=ensure_ascii)[1:-1].encode()
    return result


def _url_mask(template, mask):
    # Keep each original %HH position, so URL decoders receive the same raw
    # opaque mask as the literal and base64/hex forms, without offset changes.
    result, offset, index = bytearray(), 0, 0
    while offset < len(template):
        if template[offset:offset+1] == b"%":
            result.extend(f"%{mask[index]:02X}".encode())
            offset += 3
        else:
            result.append(mask[index])
            offset += 1
        index += 1
    return bytes(result)


class BinaryProtocolProjector:
    """Stable random masks, kept only in the owning host object's memory.

    Positions, lengths and equality correlation remain visible. All resource
    limits are configurable; no upper ceiling is imposed on configuration.
    """
    def __init__(self, *, max_wire_bytes=None, max_pattern_bytes=None,
                 max_matches=None, max_mask_attempts=64):
        limits = {
            "max_wire_bytes": resource_limit("MAX_CAPTURE_BYTES") if max_wire_bytes is None else max_wire_bytes,
            "max_pattern_bytes": resource_limit("PROTOCOL_INPUT_BYTES") if max_pattern_bytes is None else max_pattern_bytes,
            "max_matches": max(1, resource_limit("PROTOCOL_OUTPUT_BYTES") // 64) if max_matches is None else max_matches,
            "max_mask_attempts": max_mask_attempts,
        }
        if any(type(value) is not int or value <= 0 for value in limits.values()):
            raise ProtocolProjectionError("protocol_projection_input_invalid")
        self._limits, self._masks, self._refs = limits, {}, {}
        self._masked_literals = set()

    @property
    def masked_literals(self) -> tuple[str, ...]:
        """Host-only cumulative snapshot for screening every VM output."""
        return tuple(sorted(self._masked_literals))

    def _mask(self, literal, forbidden):
        if literal not in self._masks:
            for _ in range(self._limits["max_mask_attempts"]):
                mask = secrets.token_bytes(len(literal)).translate(_ASCII_RANDOM)
                if not any(pattern in mask for pattern in forbidden):
                    self._masks[literal] = mask
                    break
            else:
                raise ProtocolProjectionError("protocol_projection_unavailable")
        return self._masks[literal]

    def _replacement(self, pattern, identity, kind, forbidden):
        mask = self._mask(identity, forbidden)
        if kind == "raw":
            result = mask
        elif kind.startswith("base64"):
            encoder = base64.urlsafe_b64encode if kind.startswith("base64url") else base64.b64encode
            result = encoder(mask)
            if kind.endswith("-unpadded"):
                result = result.rstrip(b"=")
        elif kind.startswith("hex"):
            result = mask.hex().encode()
            if kind == "hex-upper":
                result = result.upper()
        elif kind == "url":
            result = _url_mask(pattern, mask)
        else:
            # JSON Unicode escapes and UTF-8 have different byte lengths. This
            # representation gets a stable ASCII mask; refs retain identity.
            result = self._mask(pattern, forbidden)
        return result, self._remember(mask, result)

    def _remember(self, *masks):
        additions = {form.decode("ascii") for mask in masks for form in private_literal_forms(mask)}
        if sum(len(item) for item in self._masked_literals | additions) > self._limits["max_pattern_bytes"]:
            raise ProtocolProjectionError("protocol_projection_limit")
        self._masked_literals.update(additions)
        return additions

    def project(self, raw_bytes: bytes, protected_values) -> BinaryProtocolProjection:
        """Mask known values before decoding; never alter the caller's bytes."""
        if (type(raw_bytes) is not bytes
                or type(protected_values) not in {list, tuple, set, frozenset}
                or any(type(value) is not str for value in protected_values)):
            raise ProtocolProjectionError("protocol_projection_input_invalid")
        if len(raw_bytes) > self._limits["max_wire_bytes"]:
            raise ProtocolProjectionError("protocol_projection_limit")
        try:
            private = {value.encode("utf-8") for value in protected_values if value}
        except UnicodeError:
            raise ProtocolProjectionError("protocol_projection_input_invalid") from None
        if sum(len(literal) for literal in private) > self._limits["max_pattern_bytes"]:
            raise ProtocolProjectionError("protocol_projection_limit")
        patterns = {}
        for literal in sorted(private):
            if literal not in self._refs:
                self._refs[literal] = "private-wire-" + secrets.token_hex(16)
            for pattern, kind in private_literal_forms(literal).items():
                if pattern not in patterns:
                    patterns[pattern] = {"refs": set(), "identity": literal, "kind": kind}
                patterns[pattern]["refs"].add(self._refs[literal])
        if sum(len(pattern) for pattern in patterns) > self._limits["max_pattern_bytes"]:
            raise ProtocolProjectionError("protocol_projection_limit")
        matches = []
        for pattern, metadata in patterns.items():
            offset = raw_bytes.find(pattern)
            while offset >= 0:
                if len(matches) >= self._limits["max_matches"]:
                    raise ProtocolProjectionError("protocol_projection_limit")
                matches.append((offset, offset + len(pattern), metadata["refs"]))
                offset = raw_bytes.find(pattern, offset + 1)
        matches.sort(key=lambda match: (match[0], match[1]))
        groups = []
        for start, end, refs in matches:
            if groups and start < groups[-1][1]:
                groups[-1][1] = max(groups[-1][1], end)
            else:
                groups.append([start, end])
        safe = bytearray(raw_bytes)
        masked = set()
        for start, end in groups:
            pattern = raw_bytes[start:end]
            if pattern in patterns:
                metadata = patterns[pattern]
                mask, screening_values = self._replacement(pattern, metadata["identity"], metadata["kind"], patterns)
            else:
                mask = self._mask(pattern, patterns)
                screening_values = self._remember(mask)
            if len(mask) != end-start:
                raise ProtocolProjectionError("protocol_projection_unavailable")
            safe[start:end] = mask
            masked.update(screening_values)
        wire_safe = bytes(safe)
        # Reject even an accidental reconstruction across mask boundaries.
        if any(pattern in wire_safe for pattern in patterns):
            raise ProtocolProjectionError("protocol_projection_unavailable")
        refs_safe = tuple({"offset": start, "length": end - start, "opaque_ref": ref}
            for start, end, refs in matches for ref in sorted(refs))
        return BinaryProtocolProjection(wire_safe, hashlib.sha256(raw_bytes).hexdigest(),
            hashlib.sha256(wire_safe).hexdigest(), refs_safe, tuple(sorted(masked)))
