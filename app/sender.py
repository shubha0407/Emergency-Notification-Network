import socket, json, sys

server = (sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1", 7000)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 46 << 2)   # mark as urgent (DSCP 46)
sock.settimeout(1.0)

alerts = {
    "1": ("FIRE", "HIGH"),
    "2": ("MEDICAL", "HIGH"),
    "3": ("SECURITY THREAT", "CRITICAL"),
    "4": ("FLOOD", "HIGH"),
}

print("=== EMERGENCY NOTIFICATION SYSTEM ===")
print("1. Fire\n2. Medical Emergency\n3. Security Threat\n4. Flood\n5. Exit")

while True:
    choice = input("\nChoose emergency type: ").strip()
    if choice == "5":
        break
    if choice not in alerts:
        print("Invalid choice. Try again.")
        continue
    alert_type, priority = alerts[choice]
    msg = {
        "type": "ALERT",
        "alert_type": alert_type,
        "priority": priority,
        "location": input("Enter location: ").strip() or "Unknown",
        "message": input("Enter emergency details: ").strip() or "No details",
    }
    try:
        sock.sendto(json.dumps(msg).encode(), server)
        data, _ = sock.recvfrom(2048)
        r = json.loads(data.decode())
        if r.get("type") == "ALERT_ACCEPTED":
            print(f"Server accepted alert #{r['seq']}, delivering to {r['subscribers']} subscribers.")
    except socket.timeout:
        print("No confirmation from server. Check the server IP and that the server is running.")
    except (OSError, ValueError) as e:
        print("Could not send:", e)
sock.close()
