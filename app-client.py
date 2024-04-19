#!/usr/bin/env python3

import sys
import socket
import selectors
import traceback

import libclient

sel = selectors.DefaultSelector()

print(f"Enter host to connect:")
host = input()
print(f"Enter port to connect:")
port = int(input())

addr = (host, port)
print(f"Starting connection to {addr}")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setblocking(False)
sock.connect_ex(addr)

events = selectors.EVENT_WRITE | selectors.EVENT_READ
message = libclient.Message(sel, sock, addr, None)
message.create_request()
sel.register(sock, events, data=message)

try:
    while True:
        events = sel.select(timeout=1)
        for key, mask in events:
            message = key.data
            try:
                message.process_events(mask)
            except Exception:
                print(
                    f"Main: Error: Exception for {message.addr}:\n"
                    f"{traceback.format_exc()}"
                )
                message.close()
        # Check for a socket being monitored to continue.
        if not sel.get_map():
            break
except KeyboardInterrupt:
    print("Caught keyboard interrupt, exiting")
finally:
    sel.close()
