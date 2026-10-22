import socket, json
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(('10.200.0.1', 0))
s.settimeout(1)
s.sendto(json.dumps({"type": "ARM", "window": 30}).encode(), ('10.200.0.254', 6000))
try:
    print("Controller replied:", s.recvfrom(256)[0].decode())
except socket.timeout:
    print("No reply from controller")
