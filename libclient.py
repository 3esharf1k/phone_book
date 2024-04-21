import sys
import selectors
import json
import io
import struct

values = dict(surname="", name="", patronymic="", phone="", note="")


class Message:
    def __init__(self, selector, sock, addr, request):
        self.selector = selector
        self.sock = sock
        self.addr = addr
        self._recv_buffer = b""
        self._send_buffer = b""
        self.request = request
        self._jsonheader_len = None
        self.jsonheader = None
        self.response = None
        self._request_queued = False
        self.exit_response = False

    def _set_selector_events_mask(self, mode):
        if mode == "r":
            events = selectors.EVENT_READ
        elif mode == "w":
            events = selectors.EVENT_WRITE
        elif mode == "rw":
            events = selectors.EVENT_READ | selectors.EVENT_WRITE
        else:
            raise ValueError(f"Invalid events mask mode {mode!r}.")
        self.selector.modify(self.sock, events, data=self)

    def _read(self):
        try:
            data = self.sock.recv(4096)
        except BlockingIOError:
            pass
        else:
            if data:
                self._recv_buffer += data
            else:
                raise RuntimeError(f"Peer closed.")

    def _write(self):
        if self._send_buffer:
            try:
                sent = self.sock.send(self._send_buffer)
            except BlockingIOError:
                pass
            else:
                self._send_buffer = self._send_buffer[sent:]
                if sent and not self._send_buffer:
                    self._set_selector_events_mask("r")

    def _json_encode(self, obj, encoding):
        return json.dumps(obj, ensure_ascii=False).encode(encoding)

    def _json_decode(self, json_bytes, encoding):
        tiow = io.TextIOWrapper(
            io.BytesIO(json_bytes), encoding=encoding, newline=""
        )
        obj = json.load(tiow)
        tiow.close()
        return obj

    def _create_message(
            self, *, content_bytes, content_type, content_encoding
    ):
        jsonheader = {
            "byteorder": sys.byteorder,
            "content-type": content_type,
            "content-encoding": content_encoding,
            "content-length": len(content_bytes),
        }
        jsonheader_bytes = self._json_encode(jsonheader, "utf-8")
        message_hdr = struct.pack(">H", len(jsonheader_bytes))
        message = message_hdr + jsonheader_bytes + content_bytes
        return message

    def _process_response_json_content(self):
        content = self.response
        result = content.get("result")
        if result == f"Closing...":
            self.exit_response = True
        print(f"{result}")

    def process_events(self, mask):
        if mask & selectors.EVENT_READ:
            self.read()
        if mask & selectors.EVENT_WRITE:
            self.write()

    def read(self):
        self._read()

        if self._jsonheader_len is None:
            self.process_protoheader()

        if self._jsonheader_len is not None:
            if self.jsonheader is None:
                self.process_jsonheader()

        if self.jsonheader:
            if self.response is None:
                self.process_response()

        if self.exit_response:
            self.close()
        else:
            self.create_request()

    def write(self):
        if not self._request_queued:
            self.queue_request()

        self._write()

    def close(self):
        print(f"Closing connection to {self.addr}")
        try:
            self.selector.unregister(self.sock)
        except Exception as e:
            print(
                f"Error: selector.unregister() exception for "
                f"{self.addr}: {e!r}"
            )

        try:
            self.sock.close()
        except OSError as e:
            print(f"Error: socket.close() exception for {self.addr}: {e!r}")
        finally:
            self.sock = None

    def process_protoheader(self):
        hdrlen = 2
        if len(self._recv_buffer) >= hdrlen:
            self._jsonheader_len = struct.unpack(
                ">H", self._recv_buffer[:hdrlen]
            )[0]
            self._recv_buffer = self._recv_buffer[hdrlen:]

    def process_jsonheader(self):
        hdrlen = self._jsonheader_len
        if len(self._recv_buffer) >= hdrlen:
            self.jsonheader = self._json_decode(
                self._recv_buffer[:hdrlen], "utf-8"
            )
            self._recv_buffer = self._recv_buffer[hdrlen:]
            for reqhdr in (
                    "byteorder",
                    "content-length",
                    "content-type",
                    "content-encoding",
            ):
                if reqhdr not in self.jsonheader:
                    raise ValueError(f"Missing required header '{reqhdr}'.")

    def process_response(self):
        content_len = self.jsonheader["content-length"]
        if not len(self._recv_buffer) >= content_len:
            return
        data = self._recv_buffer[:content_len]
        self._recv_buffer = self._recv_buffer[content_len:]
        encoding = self.jsonheader["content-encoding"]
        self.response = self._json_decode(data, encoding)
        self._process_response_json_content()
        self._set_selector_events_mask("w")

    def queue_request(self):
        content = self.request["content"]
        content_type = self.request["type"]
        content_encoding = self.request["encoding"]
        req = {
            "content_bytes": self._json_encode(content, content_encoding),
            "content_type": content_type,
            "content_encoding": content_encoding,
        }
        message = self._create_message(**req)
        self._send_buffer += message
        self._request_queued = True

    def create_request(self):
        request = dict(type="text/json", encoding="utf-8")
        print(f"Choose action: search, add, delete, check or exit.")
        action = input()
        while (action != "search") \
                and (action != "add") \
                and (action != "delete") \
                and (action != "check") \
                and (action != "exit"):
            print(f"Unknown action. Enter search, add, delete, check or exit.")
            action = input()
        match action:
            case "search":
                print(f"Choose field where search: {', '.join([key for key in values])}.")
                field = input()
                while values.get(field) is None:
                    warning = f"Field wasn't found. Use this fields: "
                    warning += ', '.join([key for key in values])
                    print(warning)
                    field = input()
                print(f"Enter value for search:")
                value = input()
                request["content"] = dict(action=action, field=field, value=value)
            case "add":
                for key in values:
                    print(f"Enter {key}:")
                    values[key] = input()
                request["content"] = dict(action=action, values=values)
            case "delete":
                print(f"Enter value for delete line:")
                value = input()
                request["content"] = dict(action=action, value=value)
            case _:
                request["content"] = dict(action=action)
        self.request = request
        self._recv_buffer = b""
        self._send_buffer = b""
        self._jsonheader_len = None
        self.jsonheader = None
        self.response = None
        self._request_queued = False
