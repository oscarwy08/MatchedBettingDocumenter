"""Outbound path both apps use when inbound Wi‑Fi/UPnP cannot connect.

TLS MQTT to public brokers. Topics are hashes; bodies are already encrypted.
Used only as a fallback after a direct attempt fails.
"""

from __future__ import annotations

import hashlib
import secrets
import ssl
import threading
import time
from typing import Callable

BROKERS = (
    ("test.mosquitto.org", 8883),
    ("broker.emqx.io", 8883),
)

_put: Callable[[str, str], None] | None = None
_get: Callable[[str, float], str | None] | None = None


def topic_for(kind: str, secret: str) -> str:
    digest = hashlib.sha256(f"{kind}:{secret}".encode("utf-8")).hexdigest()[:32]
    return f"mbd/v1/{kind}/{digest}"


def put(kind: str, secret: str, blob: str) -> None:
    if not secret or not blob:
        return
    if _put is not None:
        _put(topic_for(kind, secret), blob)
        return
    _mqtt_put(topic_for(kind, secret), blob)


def get(kind: str, secret: str, timeout: float = 8.0) -> str | None:
    if not secret:
        return None
    if _get is not None:
        return _get(topic_for(kind, secret), timeout)
    return _mqtt_get(topic_for(kind, secret), timeout)


def _make_client():
    import paho.mqtt.client as mqtt

    cid = f"mbd-{secrets.token_hex(4)}"
    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=cid,
            protocol=mqtt.MQTTv311,
        )
    except AttributeError:
        client = mqtt.Client(client_id=cid, protocol=mqtt.MQTTv311)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    return client


def _mqtt_put(topic: str, blob: str) -> None:
    last: Exception | None = None
    for host, port in BROKERS:
        client = _make_client()
        done = threading.Event()
        error: list[Exception] = []

        def on_connect(client, _userdata, _flags, reason_code, *_extra):
            rc = int(getattr(reason_code, "value", reason_code))
            if rc != 0:
                error.append(RuntimeError(f"MQTT connect {rc}"))
                done.set()
                return
            client.publish(topic, blob, qos=1, retain=True)

        def on_publish(_client, _userdata, _mid, *_extra):
            done.set()

        client.on_connect = on_connect
        client.on_publish = on_publish
        try:
            client.connect(host, port, keepalive=20)
            client.loop_start()
            if not done.wait(timeout=8):
                raise TimeoutError(f"{host} publish timed out")
            if error:
                raise error[0]
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
        finally:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
    raise RuntimeError(f"Mailbox is not reachable ({last}).")


def _mqtt_get(topic: str, timeout: float) -> str | None:
    last: Exception | None = None
    for host, port in BROKERS:
        client = _make_client()
        got: list[str] = []
        done = threading.Event()

        def on_connect(client, _userdata, _flags, reason_code, *_extra):
            rc = int(getattr(reason_code, "value", reason_code))
            if rc != 0:
                done.set()
                return
            client.subscribe(topic, qos=1)

        def on_message(_client, _userdata, message):
            payload = message.payload.decode("utf-8", "ignore")
            if payload:
                got.append(payload)
            done.set()

        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(host, port, keepalive=20)
            client.loop_start()
            done.wait(timeout=timeout)
            if got:
                return got[0]
        except Exception as exc:  # noqa: BLE001
            last = exc
        finally:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.05)
    if last:
        raise RuntimeError(f"Mailbox is not reachable ({last}).")
    return None
