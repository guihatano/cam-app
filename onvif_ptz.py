"""Controle PTZ via ONVIF (porta 8899 da câmera XM).

O comando XMEye OPPTZControl (porta 34567) é aceito (Ret=100) mas NÃO move esta
câmera; o movimento físico só funciona via ONVIF ContinuousMove/Stop.
"""
import base64
import datetime
import hashlib
import re
import secrets
import threading
import urllib.error
import urllib.request

_NS_MEDIA = "http://www.onvif.org/ver10/media/wsdl"
_NS_PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
_NS_SCHEMA = "http://www.onvif.org/ver10/schema"


class OnvifPTZ:
    def __init__(self, host, user, password, port=8899, timeout=6):
        self.base = f"http://{host}:{port}"
        self.user = user
        self.password = password or ""
        self.timeout = timeout
        self._token = None
        self._lock = threading.Lock()

    def _wsse(self):
        nonce = secrets.token_bytes(16)
        created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        digest = base64.b64encode(
            hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()
        ).decode()
        n64 = base64.b64encode(nonce).decode()
        return (
            '<wsse:Security s:mustUnderstand="1" '
            'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
            'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            "<wsse:UsernameToken>"
            f"<wsse:Username>{self.user}</wsse:Username>"
            '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f"{digest}</wsse:Password>"
            '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
            f"{n64}</wsse:Nonce>"
            f"<wsu:Created>{created}</wsu:Created>"
            "</wsse:UsernameToken></wsse:Security>"
        )

    def _call(self, path, body, action):
        env = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
            f"<s:Header>{self._wsse()}</s:Header><s:Body>{body}</s:Body></s:Envelope>"
        )
        req = urllib.request.Request(
            self.base + path,
            data=env.encode(),
            headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'},
        )
        try:
            return urllib.request.urlopen(req, timeout=self.timeout).read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"ONVIF {path} HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")

    def _profile_token(self):
        if self._token:
            return self._token
        with self._lock:
            if self._token:
                return self._token
            r = self._call(
                "/onvif/media_service",
                f'<GetProfiles xmlns="{_NS_MEDIA}"/>',
                f"{_NS_MEDIA}/GetProfiles",
            )
            m = re.findall(r'token="([^"]+)"', r)
            if not m:
                raise RuntimeError("ONVIF GetProfiles sem token: " + r[:200])
            self._token = m[0]
            return self._token

    def move(self, pan=0.0, tilt=0.0, zoom=0.0):
        """Inicia movimento contínuo. Velocidades em [-1, 1]. Lembre de chamar stop()."""
        tok = self._profile_token()
        zoom_xml = f'<Zoom x="{zoom}" xmlns="{_NS_SCHEMA}"/>' if zoom else ""
        body = (
            f'<ContinuousMove xmlns="{_NS_PTZ}"><ProfileToken>{tok}</ProfileToken>'
            f'<Velocity><PanTilt x="{pan}" y="{tilt}" xmlns="{_NS_SCHEMA}"/>{zoom_xml}</Velocity>'
            "</ContinuousMove>"
        )
        self._call("/onvif/ptz_service", body, f"{_NS_PTZ}/ContinuousMove")

    def stop(self):
        tok = self._profile_token()
        body = (
            f'<Stop xmlns="{_NS_PTZ}"><ProfileToken>{tok}</ProfileToken>'
            "<PanTilt>true</PanTilt><Zoom>true</Zoom></Stop>"
        )
        self._call("/onvif/ptz_service", body, f"{_NS_PTZ}/Stop")
