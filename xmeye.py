import hashlib
import json
import socket
import struct

HEADER_FMT = "<BBBBIIBBHI"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 20 bytes

MSG_LOGIN = 1000
MSG_SEARCH = 1440  # OPFileQuery (1442 é OPLogQuery neste firmware)
MSG_PLAY_CLAIM = 1424  # OPPlayBack Claim
MSG_PLAY_START = 1420  # OPPlayBack DownloadStart
MSG_PLAY_DATA = 1426   # pacotes de mídia (resposta)
MSG_PLAY_CTRL = 1425   # acks / fim de stream


def _hash_password(password):
    if not password:
        return "tlJwpbo6"
    raw = hashlib.md5(password.encode()).digest()
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "".join(chars[(raw[i] + raw[i + 1]) % 62] for i in range(0, 16, 2))


def _pack(session_id, seq, msg_id, payload):
    if isinstance(payload, dict):
        payload = json.dumps(payload, separators=(",", ":")).encode() + b"\x0a\x00"
    header = struct.pack(
        HEADER_FMT, 0xFF, 0x00, 0x00, 0x00, session_id, seq, 0, 0, msg_id, len(payload)
    )
    return header + payload


def _recv_all(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed by camera")
        buf += chunk
    return buf


class XMEyeClient:
    def __init__(self, host, port=34567, timeout=10):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock = None
        self.session_id = 0
        self._seq = 0

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    def connect(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))

    def disconnect(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _send(self, msg_id, payload):
        self._sock.sendall(_pack(self.session_id, self._seq, msg_id, payload))
        self._seq += 1

    def _recv(self):
        raw = _recv_all(self._sock, HEADER_SIZE)
        _, _, _, _, session, _, _, _, msg_id, length = struct.unpack(HEADER_FMT, raw)
        self.session_id = session
        data = _recv_all(self._sock, length) if length else b""
        try:
            return msg_id, json.loads(data.rstrip(b"\x00\x0a"))
        except Exception:
            return msg_id, data

    def login(self, username, password):
        self._send(MSG_LOGIN, {
            "EncryptType": "MD5",
            "LoginType": "DVRIP-Web",
            "PassWord": _hash_password(password),
            "UserName": username,
        })
        _, resp = self._recv()
        return isinstance(resp, dict) and resp.get("Ret") in (100, 200)

    def _query_files(self, begin, end, channel, filetype):
        self._send(MSG_SEARCH, {
            "Name": "OPFileQuery",
            "OPFileQuery": {
                "BeginTime": begin,
                "EndTime": end,
                "Channel": channel,
                "DriverTypeMask": "0x0000FFFF",
                "Event": "*",
                "Type": filetype,
            },
        })
        _, resp = self._recv()
        if not isinstance(resp, dict):
            return []
        return list(resp.get("OPFileQuery") or [])

    def search_recordings(self, date, channel=0, filetype="h264", max_pages=50):
        """Lista todas as gravações do dia.

        A câmera limita cada OPFileQuery a ~64 resultados, então paginamos avançando
        o cursor para o EndTime do último arquivo até esgotar o dia (sem novidades).
        """
        day_end = date.strftime("%Y-%m-%d 23:59:59")
        cursor = date.strftime("%Y-%m-%d 00:00:00")
        files = []
        seen = set()
        for _ in range(max_pages):
            batch = self._query_files(cursor, day_end, channel, filetype)
            if not batch:
                break
            new = 0
            last_end = cursor
            for f in batch:
                key = f.get("FileName") or (f.get("BeginTime"), f.get("EndTime"))
                end_t = f.get("EndTime") or f.get("BeginTime") or ""
                if end_t > last_end:
                    last_end = end_t
                if key in seen:
                    continue
                seen.add(key)
                files.append(f)
                new += 1
            # Sem progresso (nenhum arquivo novo ou cursor não avançou) = fim
            if new == 0 or last_end <= cursor or last_end >= day_end:
                break
            cursor = last_end
        files.sort(key=lambda f: f.get("BeginTime") or "")
        return files

    def _recv_header(self):
        raw = _recv_all(self._sock, HEADER_SIZE)
        _, _, _, _, session, _, _, _, msg_id, length = struct.unpack(HEADER_FMT, raw)
        self.session_id = session
        return msg_id, length

    def download_recording(self, filename, start, end, on_data, idle_timeout=4):
        """Baixa um arquivo de gravação via OPPlayBack/DownloadStart.

        Os bytes brutos (com headers privados XM) são entregues em pedaços para
        ``on_data(chunk)``. Use ``demux_xm`` para extrair o stream H.264/H.265 puro.
        """
        param = {
            "PlayMode": "ByName",
            "FileName": filename,
            "StreamType": 0,
            "Value": 0,
            "TransMode": "TCP",
        }
        self._send(MSG_PLAY_CLAIM, {"Name": "OPPlayBack", "OPPlayBack": {
            "Action": "Claim", "StartTime": start, "EndTime": end, "Parameter": param}})
        _, resp = self._recv()
        if not (isinstance(resp, dict) and resp.get("Ret") in (100, 200)):
            raise RuntimeError(f"Playback claim failed: {resp}")

        self._send(MSG_PLAY_START, {"Name": "OPPlayBack", "OPPlayBack": {
            "Action": "DownloadStart", "StartTime": start, "EndTime": end, "Parameter": param}})

        self._sock.settimeout(idle_timeout)
        got_data = False
        try:
            while True:
                try:
                    msg_id, length = self._recv_header()
                except socket.timeout:
                    if got_data:
                        break  # câmera parou de enviar = fim do arquivo
                    raise
                body = _recv_all(self._sock, length) if length else b""
                if msg_id == MSG_PLAY_DATA:
                    got_data = True
                    on_data(body)
                elif msg_id == MSG_PLAY_CTRL:
                    if got_data:
                        break  # ack de fim de stream
        finally:
            self._sock.settimeout(self.timeout)


_XM_TYPES_VIDEO = {0xFC: (16, 12), 0xFD: (8, 4)}  # tipo -> (header_len, offset do len u32)


class StreamDemux:
    """Demuxer incremental dos headers privados XM → H.264/H.265 Annex-B puro.

    Alimente pedaços com ``feed(chunk)`` conforme chegam (frames podem cruzar a
    fronteira dos chunks); retorna os bytes de vídeo já completos. Permite
    sobrepor download e transcodificação.

    Frames de vídeo: 0xFC (I, header 16B, len u32 @ +12), 0xFD (P, header 8B, len u32 @ +4).
    Áudio (0xFA) e info (0xF8/0xF9/0xFE) usam header 8B com len u16 @ +6 e são descartados.
    """

    def __init__(self):
        self._buf = bytearray()

    def feed(self, chunk):
        buf = self._buf
        buf += chunk
        out = bytearray()
        n = len(buf)
        i = 0
        while i + 8 <= n:
            if buf[i] == 0 and buf[i + 1] == 0 and buf[i + 2] == 1 and 0xF0 <= buf[i + 3] <= 0xFF:
                t = buf[i + 3]
                if t in _XM_TYPES_VIDEO:
                    hlen, off = _XM_TYPES_VIDEO[t]
                    dlen = struct.unpack_from("<I", buf, i + off)[0]
                    if i + hlen + dlen > n:
                        break  # payload ainda incompleto; espera mais dados
                    out += buf[i + hlen:i + hlen + dlen]
                    i += hlen + dlen
                else:  # áudio / info
                    dlen = struct.unpack_from("<H", buf, i + 6)[0]
                    if i + 8 + dlen > n:
                        break
                    i += 8 + dlen
            else:
                i += 1
        del buf[:i]  # mantém a cauda incompleta para o próximo feed
        return bytes(out)


def demux_xm(raw):
    """Versão batch do demux: processa ``raw`` inteiro de uma vez."""
    return StreamDemux().feed(raw)
