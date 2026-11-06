"""
Şirket ağı SSL ayarı (şirket proxy'si HTTPS trafiğini kendi sertifikasıyla imzalıyor).

Sırayla:
  1) config.json → "ssl_verify": false   → doğrulama tamamen kapalı (son çare)
  2) config.json → "ca_bundle": "...pem" → verilen sertifika dosyası
  3) truststore (Python 3.10+)            → Windows sertifika deposu
  4) Windows + Python 3.9                 → Windows deposundaki sertifikalar windows_ca.pem'e
                                            yazılır ve o kullanılır (şirket kök sertifikası dahil)
Sonuç cfg["_verify"] içine yazılır; tüm requests oturumları bunu kullanır.
"""
from __future__ import annotations

import ssl
import sys
from pathlib import Path


def build_windows_bundle(base: Path) -> Path | None:
    if not hasattr(ssl, "enum_certificates"):
        return None
    try:
        import certifi
        pems = [Path(certifi.where()).read_text(encoding="ascii", errors="ignore")]
    except Exception:
        pems = []
    seen = set()
    for store in ("ROOT", "CA"):
        try:
            certs = ssl.enum_certificates(store)
        except Exception:
            continue
        for der, enc, trust in certs:
            if enc != "x509_asn" or der in seen:
                continue
            # trust: True (her amaç) veya OID kümesi; 1.3.6.1.5.5.7.3.1 = sunucu doğrulama
            if trust is True or (isinstance(trust, (set, frozenset)) and "1.3.6.1.5.5.7.3.1" in trust):
                seen.add(der)
                pems.append(ssl.DER_cert_to_PEM_cert(der))
    if not seen:
        return None
    out = base / "windows_ca.pem"
    out.write_text("\n".join(pems), encoding="ascii")
    return out


def setup_ssl(cfg: dict, base: Path) -> str:
    """cfg['_verify'] değerini ayarlar, kullanılan yöntemi açıklayan kısa metni döndürür."""
    if cfg.get("ssl_verify") is False:
        cfg["_verify"] = False
        try:
            import urllib3
            urllib3.disable_warnings()
        except Exception:
            pass
        return "SSL doğrulaması KAPALI (config.json → ssl_verify: false)"
    if cfg.get("ca_bundle"):
        cfg["_verify"] = cfg["ca_bundle"]
        return f"sertifika dosyası: {cfg['ca_bundle']}"
    try:
        import truststore
        truststore.inject_into_ssl()
        cfg["_verify"] = True
        return "Windows sertifika deposu (truststore)"
    except Exception:
        pass
    if sys.platform == "win32":
        p = build_windows_bundle(base)
        if p:
            cfg["_verify"] = str(p)
            return f"Windows sertifika deposu → {p.name}"
    cfg["_verify"] = True
    return "varsayılan sertifikalar"
