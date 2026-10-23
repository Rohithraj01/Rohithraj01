"""Check which TLS versions a Geo SCADA server accepts.

Use it when a connection fails with Windows error 0x80090331 (SEC_E_ALGORITHM_MISMATCH,
"the client and server cannot communicate, because they do not possess a common
algorithm"). That means the client and server have no TLS version or cipher in common.

    python tls_probe.py 192.168.8.142              # tries ports 443 and 5481
    python tls_probe.py 192.168.8.142 443 8443     # tries the ports you give

This uses Python's own TLS library, not Windows, so it shows what the *server* can
do, whatever the TLS settings are on this PC.
"""
import socket
import ssl
import sys
import warnings

# Python warns that TLS 1.0/1.1 are deprecated; testing for them is the point here.
warnings.filterwarnings("ignore", category=DeprecationWarning)

VERSIONS = [
    ("TLS 1.0", "TLSv1"),
    ("TLS 1.1", "TLSv1_1"),
    ("TLS 1.2", "TLSv1_2"),
    ("TLS 1.3", "TLSv1_3"),
]


def probe(host, port, version_name):
    """Try one TLS handshake. Returns (True, cipher), (False, reason) or (None, why untested)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        version = getattr(ssl.TLSVersion, version_name)
        ctx.minimum_version = version
        ctx.maximum_version = version
        # Offer old ciphers too, so an old server is reported as "old", not "broken".
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except (AttributeError, ValueError, ssl.SSLError) as e:
        return None, f"this Python cannot test it ({e})"

    try:
        with socket.create_connection((host, port), timeout=5) as raw:
            with ctx.wrap_socket(raw) as tls:
                name, protocol, _ = tls.cipher()
                return True, f"{protocol}, cipher {name}"
    except ssl.SSLError as e:
        return False, e.reason or str(e)
    except OSError as e:
        return False, str(e)


def port_open(host, port):
    try:
        socket.create_connection((host, port), timeout=5).close()
        return True, ""
    except OSError as e:
        return False, str(e)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    host = argv[0]
    ports = [int(p) for p in argv[1:]] or [443, 5481]

    for port in ports:
        print(f"\n{host}:{port}")
        is_open, why = port_open(host, port)
        if not is_open:
            print(f"  cannot reach the port: {why}")
            continue

        accepted = []
        for label, name in VERSIONS:
            ok, detail = probe(host, port, name)
            mark = {True: "yes", False: "no ", None: "?  "}[ok]
            print(f"  {label}: {mark}  {detail}")
            if ok:
                accepted.append(label)

        if not accepted:
            print("  -> No TLS handshake worked. Either this port does not use TLS (plain HTTP or\n"
                  "     the Geo SCADA client protocol), or the server needs a client certificate.")
        elif set(accepted) <= {"TLS 1.0", "TLS 1.1"}:
            print("  -> The server only speaks TLS 1.0/1.1. Up-to-date Windows has those turned off,\n"
                  "     which causes 0x80090331. This is the original WebX HTTPS server (Geo SCADA\n"
                  "     2020 and older). See geoscada-client/README.md, 'Error 0x80090331'.")
        else:
            print(f"  -> The server accepts {', '.join(accepted)}. If Windows still gives 0x80090331,\n"
                  "     the cipher suites or TLS versions allowed on the client PC (Group Policy or\n"
                  "     SCHANNEL registry keys) do not overlap with the ones listed above.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
