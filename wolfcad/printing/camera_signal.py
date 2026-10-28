# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Anmeldung an der Druckerkamera (ohne GStreamer, damit sie aus beiden Python-Umgebungen nutzbar ist): Video-Token
über den lokalen Websocket holen, Zertifikats-Fingerabdruck lesen, Angebot per HTTPS mit festgepinntem Zertifikat
oder per HTTP schicken, Antwort des Druckers bereinigen. Einzelheiten siehe camera_helper.py."""
import base64
import hashlib
import json
import socket
import ssl
import urllib.request

def video_token(host: str, seconds: float = 6.0) -> str:
    """Video-Token des Druckers: lesende Anfrage {"method": "get", "params": {"getToken": 1}} über seinen lokalen
    Websocket (Port 9999) – so holt es Creality Prints Geräteseite (GetVideoToken). Ohne Token antwortet der
    verschlüsselte Zugang mit „token error“."""
    import os
    import re
    import time
    with socket.create_connection((host, 9999), 5) as connection:
        key = base64.b64encode(os.urandom(16)).decode()
        connection.send((f"GET / HTTP/1.1\r\nHost: {host}:9999\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        data = b""
        while b"\r\n\r\n" not in data:
            data += connection.recv(4096)
        payload = json.dumps({"method": "get", "params": {"getToken": 1}}).encode()
        mask = os.urandom(4)        # Rahmen vom Client an den Server müssen maskiert sein
        connection.send(bytes([0x81, 0x80 | len(payload)]) + mask
                        + bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)))
        buffer = data.split(b"\r\n\r\n", 1)[1]
        connection.settimeout(1.0)
        end = time.time() + seconds
        while time.time() < end:
            found = re.search(rb'"videoToken"\s*:\s*"([^"]+)"', buffer)
            if found:
                return found.group(1).decode()
            try:
                buffer += connection.recv(65536)
            except socket.timeout:
                pass
    raise RuntimeError("der Drucker hat kein Video-Token geschickt")


def certificate_fingerprint(host: str, port: int = 443) -> str:
    """SHA-256-Fingerabdruck des Serverzertifikats (zum Anzeigen und Bestätigen durch den Nutzer)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE         # nur lesen, es werden keine Daten gesendet
    with socket.create_connection((host, port), 10) as raw, context.wrap_socket(raw) as tls:
        return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()


def post(host: str, body: bytes, secure: bool = True, pinned: str = "") -> bytes:
    """Schickt das Angebot an den Drucker und gibt die Antwort zurück (HTTPS mit festgepinntem Zertifikat oder HTTP)."""
    if not secure:
        request = urllib.request.Request(f"http://{host}:8000/call/webrtc_local", data=body, method="POST")
        return urllib.request.urlopen(request, timeout=10).read()
    if not pinned:
        raise RuntimeError("kein bestätigter Zertifikats-Fingerabdruck")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE         # geprüft wird stattdessen der Fingerabdruck (unten, vor dem Senden)
    with socket.create_connection((host, 443), 10) as raw, context.wrap_socket(raw) as tls:
        found = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
        if found != pinned.replace(":", "").lower():
            raise RuntimeError(f"Zertifikat des Druckers hat sich geändert ({found[:16]}…) – nicht verbunden")
        tls.sendall((f"POST /call/webrtc_local HTTP/1.1\r\nHost: {host}\r\nContent-Type: text/plain\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body)
        response = b""
        while True:
            chunk = tls.recv(65536)
            if not chunk:
                break
            response += chunk
    head, _, content = response.partition(b"\r\n\r\n")
    if b" 200 " not in head.split(b"\r\n")[0]:
        raise RuntimeError(head.split(b"\r\n")[0].decode(errors="replace"))
    if b"chunked" in head.lower():
        content = _dechunk(content)
    return content


def _dechunk(data: bytes) -> bytes:
    out = b""
    while data:
        size, _, rest = data.partition(b"\r\n")
        length = int(size.split(b";")[0] or b"0", 16)
        if length == 0:
            break
        out += rest[:length]
        data = rest[length + 2:]
    return out


def clean_answer(sdp: str, offered: set[str], mid: str = "") -> str:
    """Die Antwort des Druckers nennt eine Nutzlast (96), die nicht angeboten war, und eine zweite fmtp-Zeile mit
    Google-Werten – beides entfernen, sonst lehnt die Gegenseite die Antwort ab."""
    lines = []
    for line in sdp.split("\r\n"):
        if line.startswith("m=video"):
            parts = line.split()
            line = " ".join(parts[:3] + [p for p in parts[3:] if p in offered])
        elif line.startswith("a=fmtp:") and "x-google" in line:
            continue
        elif mid and line == "a=mid:0":
            line = f"a=mid:{mid}"                 # der Drucker antwortet immer mit mid 0
        elif mid and line == "a=group:BUNDLE 0":
            line = f"a=group:BUNDLE {mid}"
        elif line.startswith(("a=rtpmap:", "a=fmtp:", "a=rtcp-fb:")) and line.split(":")[1].split()[0] not in offered:
            continue
        lines.append(line)
    return "\r\n".join(lines)
