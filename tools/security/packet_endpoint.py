"""Deterministic DNS/HTTPS peers for isolated firewall namespaces, not a service."""
import argparse
import signal
import socket
import ssl
import threading

QUERY = bytes.fromhex('123401000001000000000000046c61623104746573740000010001')
ANSWER = QUERY[:2] + bytes.fromhex('8180') + QUERY[4:]


def server(cert, key):
  context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
  context.load_cert_chain(cert, key)
  def serve(sock, tls):
    while True:
      if tls:
        conn, _ = sock.accept()
        conn.settimeout(3)
        try:
          with context.wrap_socket(conn, server_side=True) as secure:
            secure.recv(1024)
            secure.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
        except (OSError, ssl.SSLError):
          conn.close()
      else:
        data, peer = sock.recvfrom(512)
        if data == QUERY:
          sock.sendto(ANSWER, peer)
  for family, address in [(socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')]:
    for kind, port in [(socket.SOCK_STREAM, 443), (socket.SOCK_DGRAM, 53)]:
      sock = socket.socket(family, kind)
      if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
      sock.bind((address, port))
      if kind == socket.SOCK_STREAM:
        sock.listen(8)
      threading.Thread(target=serve, args=(sock, kind == socket.SOCK_STREAM), daemon=True).start()
  print('READY', flush=True)
  signal.pause()


def client(address, cert):
  family = socket.AF_INET6 if ':' in address else socket.AF_INET
  with socket.socket(family, socket.SOCK_DGRAM) as sock:
    sock.settimeout(3)
    sock.sendto(QUERY, (address, 53))
    assert sock.recv(512) == ANSWER
  context = ssl.create_default_context(cafile=cert)
  with socket.create_connection((address, 443), timeout=3) as raw:
    with context.wrap_socket(raw, server_hostname='firewall.test') as secure:
      secure.sendall(b'GET / HTTP/1.1\r\nHost: firewall.test\r\nConnection: close\r\n\r\n')
      data = bytearray()
      while chunk := secure.recv(1024):
        data.extend(chunk)
      assert data.endswith(b'\r\n\r\nOK')
  print('PASS DNS and certificate-verified HTTPS', address)


def blocked_tcp(address):
  try:
    with socket.create_connection((address, 443), timeout=3):
      raise AssertionError('Unsolicited TCP connection succeeded')
  except TimeoutError:
    print('PASS unsolicited TCP timed out', address)


def related(address):
  family = socket.AF_INET6 if ':' in address else socket.AF_INET
  with socket.socket(family, socket.SOCK_DGRAM) as sock:
    sock.settimeout(3)
    sock.connect((address, 9))  # Deliberately closed peer port generates RELATED ICMP.
    sock.send(b'closed-port-test')
    try:
      sock.recv(512)
    except ConnectionRefusedError:
      print('PASS RELATED ICMP port-unreachable', address)
    else:
      raise AssertionError('Missing expected ICMP port-unreachable')


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('mode', choices=('server', 'client', 'blocked', 'related'))
  parser.add_argument('cert')
  parser.add_argument('target', help='Key path for server, peer IP for client')
  args = parser.parse_args()
  if args.mode == 'server':
    server(args.cert, args.target)
  elif args.mode == 'client':
    client(args.target, args.cert)
  elif args.mode == 'blocked':
    blocked_tcp(args.target)
  else:
    related(args.target)
