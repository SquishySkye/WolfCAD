# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Hilfsprogramm: Kamerabild eines Creality-K1/K2-Druckers per WebRTC empfangen und als JPEG-Bilder ausgeben.

Läuft mit dem System-Python (GStreamer über PyGObject: webrtcbin + avdec_h264), getrennt von WolfCAD – die
Python-Umgebung von WolfCAD kennt GStreamer nicht, und ein Absturz hier trifft das Programm nicht.

Ablauf (Protokoll wie in der Geräteseite von Creality Print): Angebot (nur Video empfangen, H.264) als Base64-JSON
an http://<drucker>:8000/call/webrtc_local schicken, Antwort ebenso zurück. Ausgabe auf stdout: je Bild 4 Byte Länge
(big endian) und die JPEG-Daten. Aufruf: python3 camera_helper.py <drucker> <fps> <https|http> [<sha256>]
bzw. python3 camera_helper.py <drucker> fingerabdruck (gibt den Zertifikats-Fingerabdruck aus).

Drucker mit Video-Verschlüsselung („videoInfo.videoEncryption“, z. B. K2 Plus) nehmen die Anfrage nur über HTTPS auf
Port 443 an. Ihr Zertifikat stammt von Crealitys eigener Zertifizierungsstelle und besteht keine normale Prüfung;
vertraut wird deshalb genau dem Zertifikat mit dem vom Nutzer bestätigten SHA-256-Fingerabdruck (Pinning) – vor dem
Senden der Daten verglichen; jedes andere Zertifikat bricht die Verbindung ab.
"""
import base64
import hashlib
import json
import socket
import ssl
import struct
import sys
import urllib.request

from camera_signal import certificate_fingerprint, clean_answer, post, video_token  # noqa: E402

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
from gi.repository import GLib, Gst, GstSdp, GstWebRTC  # noqa: E402

HOST = sys.argv[1]
FPS = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 10
SECURE = len(sys.argv) <= 3 or sys.argv[3] != "http"
PINNED = sys.argv[4].replace(":", "").lower() if len(sys.argv) > 4 else ""


def log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def main() -> None:
    Gst.init(None)
    pipeline = Gst.parse_launch(
        "webrtcbin name=rtc bundle-policy=max-bundle latency=100 "
        f"appsink name=out emit-signals=true max-buffers=1 drop=true sync=false caps=image/jpeg")
    rtc = pipeline.get_by_name("rtc")
    sink = pipeline.get_by_name("out")
    # H.264 wie im Angebot eines Browsers (Baseline 42e01f, packetization-mode 1) – ohne diese Angaben ergänzt der
    # Drucker eine zweite Variante (Nutzlast 96)
    caps = Gst.caps_from_string("application/x-rtp,media=video,encoding-name=H264,payload=101,clock-rate=90000,"
                                "packetization-mode=(string)1,profile-level-id=(string)42e01f,"
                                "level-asymmetry-allowed=(string)1")
    # wie die Geräteseite des Originals: Videokanal „senden und empfangen“ (gesendet wird nichts) – mit „nur
    # empfangen“ beantwortete der Drucker die Verschlüsselung nie
    rtc.emit("add-transceiver", GstWebRTC.WebRTCRTPTransceiverDirection.SENDRECV, caps)
    loop = GLib.MainLoop()

    def on_pad(_element, pad):
        # eingehendes Video: entpacken, dekodieren, auf die Bildrate drosseln, als JPEG an die Ausgabe
        decode = Gst.parse_bin_from_description(
            f"rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! videorate ! video/x-raw,framerate={FPS}/1 "
            "! jpegenc quality=80", True)
        pipeline.add(decode)
        decode.sync_state_with_parent()
        pad.link(decode.get_static_pad("sink"))
        decode.link(sink)

    def on_sample(appsink):
        sample = appsink.emit("pull-sample")
        data = sample.get_buffer().extract_dup(0, sample.get_buffer().get_size())
        try:
            sys.stdout.buffer.write(struct.pack(">I", len(data)) + data)
            sys.stdout.buffer.flush()
        except BrokenPipeError:
            loop.quit()
        return Gst.FlowReturn.OK

    def on_gathering(element, _param):
        if element.get_property("ice-gathering-state") != GstWebRTC.WebRTCICEGatheringState.COMPLETE:
            return
        offer = element.get_property("local-description").sdp.as_text()
        offered = {line.split()[0].split(":")[1] for line in offer.split("\r\n") if line.startswith("a=rtpmap")}
        # nur IPv4-Kandidaten aus dem eigenen Netz anbieten (die kleine WebRTC-Gegenstelle des Druckers kennt kein
        # IPv6 und nimmt nur wenige Kandidaten)
        offer = "\r\n".join(line for line in offer.split("\r\n") if not line.startswith("a=candidate")
                             or (" UDP " in line.upper() and line.split()[4].count(".") == 3))
        request = {"type": "offer", "sdp": offer}
        try:
            if SECURE:
                request["token"] = video_token(HOST)    # verschlüsselter Zugang: Token gehört ins Angebot
            body = base64.b64encode(json.dumps(request).encode())
            reply = post(HOST, body, SECURE, PINNED)
            answer = json.loads(base64.b64decode(reply.strip()))
        except Exception as error:
            log(f"Fehler: Drucker antwortet nicht ({error})")
            loop.quit()
            return
        if "--sdp" in sys.argv:
            log("ANGEBOT\n" + offer + "\nANTWORT\n" + answer["sdp"])
        mid = next((line[6:] for line in offer.split("\r\n") if line.startswith("a=mid:")), "")
        _result, message = GstSdp.SDPMessage.new_from_text(clean_answer(answer["sdp"], offered, mid))
        description = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, message)
        element.emit("set-remote-description", description, None)
        log("verbunden")

    def negotiate(element):
        # Angebot im Hauptthread erzeugen und übernehmen: die Antwort des Promise muss am Leben bleiben, solange
        # das Angebot benutzt wird – im Rückruf aus dem webrtcbin-Thread stürzte PyGObject sonst ab (2026-10-27)
        def create():
            promise = Gst.Promise.new()
            element.emit("create-offer", None, promise)
            promise.wait()
            reply = promise.get_reply()
            offer = reply.get_value("offer")
            element.emit("set-local-description", offer, None)
            return False
        GLib.idle_add(create)

    rtc.connect("pad-added", on_pad)
    rtc.connect("on-negotiation-needed", negotiate)
    rtc.connect("notify::ice-gathering-state", on_gathering)
    sink.connect("new-sample", on_sample)
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message::error", lambda _b, msg: (log(f"Fehler: {msg.parse_error()[0].message}"), loop.quit()))
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "fingerabdruck":
        print(certificate_fingerprint(HOST))
    else:
        main()
