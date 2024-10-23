import http.server
import ssl
import sys

class MyHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        print(post_data.decode('utf-8'))

def run_server(address, port):
    server_address = (address, port)
    httpd = http.server.HTTPServer(server_address, MyHandler)
    print(f"Server running on {address}:{port}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile="cert.pem", keyfile="key.pem")
    
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    httpd.serve_forever()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python script.py <address> <port>")
        sys.exit(1)
    
    address = sys.argv[1]
    port = int(sys.argv[2])
    
    run_server(address, port)

