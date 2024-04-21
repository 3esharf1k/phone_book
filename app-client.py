import socket
import selectors
import sys
import traceback
import time

import libclient

sel = selectors.DefaultSelector()


def connect_to_server(sock, addr):
    start_time = time.time()
    print(f"Waiting connection to {addr}...")
    while True:
        if time.time() - start_time >= 120:
            print(f"There is no answer from {addr}")
            sys.exit()
        try:
            sock.connect(addr)
            print(f"Starting connection to {addr}")
            break
        except OSError:
            pass


print(f"Enter host to connect:")
host = input()
print(f"Enter port to connect:")
port = int(input())

addr = (host, port)
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
connect_to_server(sock, addr)

events = selectors.EVENT_WRITE | selectors.EVENT_READ
request = libclient.Message(sel, sock, addr, None)
request.create_request()
sel.register(sock, events, data=request)

try:
    while True:
        events = sel.select(timeout=None)
        for key, mask in events:
            message = key.data
            try:
                message.process_events(mask)
            except ConnectionResetError:
                sock.close()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                connect_to_server(sock, addr)
                request = libclient.Message(sel, sock, addr, None)
                print(f"Repeat your request")
                request.create_request()
                sel.modify(sock, selectors.EVENT_WRITE | selectors.EVENT_READ, data=request)
            except Exception:
                print(
                    f"Main: Error: Exception for {message.addr}:\n"
                    f"{traceback.format_exc()}"
                )
                message.close()
        if not sel.get_map():
            break
except KeyboardInterrupt:
    print(f"Caught keyboard interrupt, exiting")
finally:
    sel.close()
