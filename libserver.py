import sys
import selectors
import json
import io
import struct

class Message:
    def __init__(self, selector, sock, addr):
        self.selector = selector
        self.sock = sock
        self.addr = addr
        self._recv_buffer = b""
        self._send_buffer = b""
        self._jsonheader_len = None
        self.jsonheader = None
        self.request = None
        self.response_created = False
        self.exit_request = False

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
            print(f"Sending {self._send_buffer!r} to {self.addr}")
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

    def _create_response_json_content(self):
        action = self.request.get("action")
        match action:
            case "search":
                query = [self.request.get("field"), self.request.get("value")]
                answer = self.search_in_book(query)
                content = {"result": answer}
            case "add":
                query = self.request.get("values")
                answer = self.add_to_book(query)
                content = {"result": answer}
            case "delete":
                query = self.request.get("value")
                answer = self.delete_from_book(query)
                content = {"result": answer}
            case "check":
                lines = self.check_book()
                if not lines:
                    content = {"result": f"Phone book is empty.\n"}
                else:
                    answer = f""
                    for i in range(len(lines)):
                        answer += ' '.join([lines[i][key] for key in lines[i]])
                        answer += '\n'
                    content = {"result": answer}
            case "exit":
                self.exit_request = True
                content = {"result": f"Closing..."}
        content_encoding = "utf-8"
        response = {
            "content_bytes": self._json_encode(content, content_encoding),
            "content_type": "text/json",
            "content_encoding": content_encoding,
        }
        return response

    def clear(self):
        self._recv_buffer = b""
        self._send_buffer = b""
        self._jsonheader_len = None
        self.jsonheader = None
        self.request = None
        self.response_created = False

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
            if self.request is None:
                self.process_request()
        

    def write(self):
        if self.request:
            if not self.response_created:
                self.create_response()

        self._write()
        if self.exit_request:
            self.close()
        else:
            self.clear()

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

    def process_request(self):
        content_len = self.jsonheader["content-length"]
        if not len(self._recv_buffer) >= content_len:
            return
        data = self._recv_buffer[:content_len]
        self._recv_buffer = self._recv_buffer[content_len:]
        encoding = self.jsonheader["content-encoding"]
        self.request = self._json_decode(data, encoding)
        print(f"Received request {self.request!r} from {self.addr}")
        self._set_selector_events_mask("w")

    def create_response(self):
        response = self._create_response_json_content()
        message = self._create_message(**response)
        self.response_created = True
        self._send_buffer += message

    def search_in_book(self, query):
        lines = self.check_book()
        answer = f""
        for i in range(len(lines)):
            if lines[i].get(query[0]).find(query[1]) != -1:
                answer += ' '.join([lines[i][key] for key in lines[i]])
                answer += '\n'
        if len(answer) == 0:
            answer = f"There are no such lines in the phone book.\n"
        return answer

    def add_to_book(self, query):
        line = json.dumps(query)
        with open('database.txt', 'a') as file:
            file.write('\n')
            file.write(line)
        return f"Line was added"

    def delete_from_book(self, query):
        lines = self.check_book()
        line = 0
        flag = False
        while (not flag) and (line != len(lines)):
            for key in lines[line]:
                if lines[line][key] == query:
                    flag = True
                    break
            line += 1
        if not flag:
            return f"Line wasn't found"
        else:
            line -= 1
            lines.pop(line)
            if len(lines) != 0:
                lines = [json.dumps(lines[i])+'\n' for i in range(len(lines))]
                lines[len(lines)-1] = lines[len(lines)-1].rstrip('\n')
            with open('database.txt', 'w') as file:
                file.writelines(lines)
            return f"Line was deleted"

    def check_book(self):
        with open('database.txt', 'r') as file:
            answer = file.readlines()
        for i in range(len(answer)):
            answer[i] = json.loads(answer[i])
        return answer
