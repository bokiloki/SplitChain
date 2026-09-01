"""TLS 1.3 mutual-authentication policy for SplitChain reference networking."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path

from .model import ProtocolError


@dataclass(frozen=True)
class TLSMaterial:
    certificate: Path
    private_key: Path
    certificate_authority: Path

    @classmethod
    def from_values(
        cls,
        certificate: str | Path | None,
        private_key: str | Path | None,
        certificate_authority: str | Path | None,
    ) -> TLSMaterial | None:
        values = (certificate, private_key, certificate_authority)
        if not any(values):
            return None
        if not all(values):
            raise ProtocolError("TLS requires certificate, private key and certificate authority")
        material = cls(*(Path(value) for value in values if value is not None))
        missing = [str(path) for path in material.paths() if not path.is_file()]
        if missing:
            raise ProtocolError(f"TLS material does not exist: {', '.join(missing)}")
        return material

    def paths(self) -> tuple[Path, Path, Path]:
        return self.certificate, self.private_key, self.certificate_authority

    def server_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self._configure(context)
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def client_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH,
            cafile=str(self.certificate_authority),
        )
        self._configure(context, load_authority=False)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def _configure(self, context: ssl.SSLContext, *, load_authority: bool = True) -> None:
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        if load_authority:
            context.load_verify_locations(cafile=str(self.certificate_authority))
        context.load_cert_chain(str(self.certificate), str(self.private_key))
