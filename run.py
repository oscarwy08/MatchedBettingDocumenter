from app import create_app

app = create_app()

if __name__ == "__main__":
    # Bound on the LAN so Devices pairing works. No debugger — the app is reachable
    # from other computers on the Wi‑Fi, and sharing PINs live in this process.
    app.run(host="0.0.0.0", port=5050, debug=False, use_reloader=False)
