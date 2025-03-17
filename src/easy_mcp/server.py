"""
Model Context Protocol (MCP) Server Implementation

This module provides a server implementation for the Model Context Protocol,
which enables real-time communication between AI models and tools via SSE.
"""

import socket
import ssl
import os
import threading
import uuid
import json
import urllib.parse
import time
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List, Union

class MCPLogger:
    """Logger for MCP server events with consistent formatting."""
    
    @staticmethod
    def log(commentary: str, data: Any) -> None:
        """
        Log an event with timestamp and thread information.
        
        Args:
            commentary: Description of the event
            data: Data associated with the event
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        pid = os.getpid()
        tid = threading.get_ident()
        
        # Format data to show escape sequences
        formatted_data = str(data).replace('\n', '\\n').replace('\r', '\\r')
        
        print(f"{timestamp} [PID:{pid}|TID:{tid}] {commentary} {formatted_data}")

class MCPSession:
    """Represents an active SSE connection session."""
    
    def __init__(self, session_id: str, client_socket: socket.socket, client_address: tuple):
        """
        Initialize a new session.
        
        Args:
            session_id: Unique session identifier
            client_socket: Connected client socket
            client_address: Client's address information
        """
        self.id = session_id
        self.client_socket = client_socket
        self.client_address = client_address
        self.created_at = datetime.now()
        self.last_ping = datetime.now()
        self.is_active = True

    def send_message(self, event_type: str, data: Any) -> bool:
        """
        Send an SSE message to the client.
        
        Args:
            event_type: Type of event (e.g., "message", "ping")
            data: Data to send (will be JSON encoded if dict)
            
        Returns:
            bool: True if message was sent successfully
        """
        try:
            if event_type == "ping":
                message = f": ping - {datetime.now().isoformat()}+00:00\r\n\r\n"
            else:
                message = ""
                if event_type != "message":
                    message += f"event: {event_type}\r\n"
                
                if isinstance(data, dict):
                    data = json.dumps(data)
                
                message += f"data: {data}\r\n\r\n"
            
            self.client_socket.sendall(message.encode('utf-8'))
            MCPLogger.log(f"SSE Message to {self.client_address} >", message)
            return True
            
        except Exception as e:
            MCPLogger.log("Error", f"Failed to send SSE message to {self.client_address}: {e}")
            self.is_active = False
            return False

class MCPServer:
    """
    Model Context Protocol server implementation.
    Handles SSE connections, tool registration, and request processing.
    """
    
    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 9443,
        cert_path: Optional[str] = None,
        key_path: Optional[str] = None,
        public_hostname: Optional[str] = None,
        server_info: Optional[Dict[str, str]] = None
    ):
        """
        Initialize the MCP server.
        
        Args:
            host: Host to bind to
            port: Port to listen on
            cert_path: Path to SSL certificate for HTTPS
            key_path: Path to SSL private key for HTTPS
            public_hostname: Public hostname for SSE connections
            server_info: Server information to include in initialize response
        """
        self.host = host
        self.port = port
        self.cert_path = cert_path
        self.key_path = key_path
        self.public_hostname = public_hostname or host
        self.server_info = server_info or {"name": "mcp-server", "version": "1.0.0"}
        
        self.active_sessions: Dict[str, MCPSession] = {}
        self.tool_handlers: Dict[str, Dict[str, Any]] = {}
        self.running = False
        
        # Default capabilities
        self.capabilities = {
            "experimental": {},
            "prompts": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
            "tools": {"listChanged": False}
        }
    
    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[[Dict[str, Any]], Dict[str, Any]]
    ) -> None:
        """
        Register a tool with the server.
        
        Args:
            name: Tool name
            description: Tool description
            input_schema: JSON Schema for tool input
            handler: Function to handle tool invocation
        """
        self.tool_handlers[name] = {
            'description': description,
            'input_schema': input_schema,
            'handler': handler
        }
    
    def _create_session(self, client_socket: socket.socket, client_address: tuple) -> str:
        """Create a new session for a client connection."""
        session_id = str(uuid.uuid4()).replace('-', '')
        session = MCPSession(session_id, client_socket, client_address)
        self.active_sessions[session_id] = session
        return session_id
    
    def _remove_session(self, session_id: str) -> None:
        """Remove a session and clean up its resources."""
        if session_id in self.active_sessions:
            session = self.active_sessions[session_id]
            try:
                session.client_socket.close()
            except:
                pass
            del self.active_sessions[session_id]
    
    def _start_ping_thread(self, session_id: str) -> None:
        """Start a thread to send periodic pings to keep the connection alive."""
        def ping_loop():
            while (
                self.running and
                session_id in self.active_sessions and
                self.active_sessions[session_id].is_active
            ):
                time.sleep(15)
                session = self.active_sessions.get(session_id)
                if session and session.is_active:
                    if not session.send_message("ping", None):
                        break
        
        ping_thread = threading.Thread(target=ping_loop)
        ping_thread.daemon = True
        ping_thread.start()
    
    def _handle_jsonrpc_request(self, session_id: str, request_data: str) -> None:
        """Process a JSON-RPC request and send response through SSE."""
        try:
            request = json.loads(request_data)
            method = request.get("method", "")
            jsonrpc = request.get("jsonrpc", "2.0")
            request_id = request.get("id")
            params = request.get("params", {})
            
            MCPLogger.log("JSONRPC Request", f"session={session_id}, method={method}, id={request_id}")
            
            if method == "initialize":
                # Handle initialize request
                protocol_version = params.get("protocolVersion", "2024-11-05")
                response = {
                    "jsonrpc": jsonrpc,
                    "id": request_id,
                    "result": {
                        "protocolVersion": protocol_version,
                        "capabilities": self.capabilities,
                        "serverInfo": self.server_info
                    }
                }
                self._send_response(session_id, response)
                
            elif method == "notifications/initialized":
                # No response needed for notifications
                pass
                
            elif method == "tools/list":
                # Respond with registered tools
                tools = []
                for name, tool in self.tool_handlers.items():
                    tools.append({
                        "name": name,
                        "description": tool["description"],
                        "inputSchema": tool["input_schema"]
                    })
                
                response = {
                    "jsonrpc": jsonrpc,
                    "id": request_id,
                    "result": {"tools": tools}
                }
                self._send_response(session_id, response)
                
            elif method in ["tools/run", "tools/call"]:
                # Handle tool execution
                tool_name = params.get("name", "")
                tool_args = params.get("arguments", {})
                
                if tool_name in self.tool_handlers:
                    try:
                        # Call the tool handler
                        result = self.tool_handlers[tool_name]["handler"](tool_args)
                        
                        # Send the response
                        response = {
                            "jsonrpc": jsonrpc,
                            "id": request_id,
                            "result": result
                        }
                        self._send_response(session_id, response)
                    except Exception as e:
                        # Tool execution error
                        error_response = {
                            "jsonrpc": jsonrpc,
                            "id": request_id,
                            "error": {
                                "code": -32603,
                                "message": f"Tool execution failed: {str(e)}"
                            }
                        }
                        self._send_response(session_id, error_response)
                else:
                    # Unknown tool
                    error_response = {
                        "jsonrpc": jsonrpc,
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Tool not found: {tool_name}"
                        }
                    }
                    self._send_response(session_id, error_response)
            
            else:
                # Unknown method
                if request_id is not None:
                    error_response = {
                        "jsonrpc": jsonrpc,
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}"
                        }
                    }
                    self._send_response(session_id, error_response)
        
        except json.JSONDecodeError:
            MCPLogger.log("Error", "Invalid JSON in request")
        except Exception as e:
            MCPLogger.log("Error", f"Failed to process JSON-RPC request: {e}")
    
    def _send_response(self, session_id: str, response: Dict[str, Any]) -> None:
        """Send a JSON-RPC response through the SSE connection."""
        if session_id in self.active_sessions:
            self.active_sessions[session_id].send_message("message", response)
    
    def _handle_client(self, client_socket: socket.socket, client_address: tuple) -> None:
        """Handle a client connection."""
        try:
            # Receive the HTTP request
            request_data = b""
            while b"\r\n\r\n" not in request_data:
                chunk = client_socket.recv(4096)
                if not chunk:
                    break
                request_data += chunk
            
            if not request_data:
                return
            
            # Parse the request
            request_text = request_data.decode('utf-8', errors='replace')
            MCPLogger.log(f"Request from {client_address} <", request_text)
            
            # Parse request line and headers
            lines = request_text.split('\r\n')
            if not lines:
                return
            
            # Parse request line
            try:
                method, path, version = lines[0].split(' ', 2)
            except ValueError:
                method, path, version = "UNKNOWN", "/", "HTTP/1.1"
            
            # Parse headers
            headers = {}
            for line in lines[1:]:
                if not line:
                    break
                try:
                    key, value = line.split(':', 1)
                    headers[key.strip().lower()] = value.strip()
                except ValueError:
                    continue
            
            # Find the body
            body_start = request_text.find('\r\n\r\n')
            body = request_text[body_start + 4:] if body_start != -1 else ""
            
            # Handle different endpoints
            if method == "GET" and path == "/sse":
                self._handle_sse_request(client_socket, client_address)
            elif method == "POST" and path.startswith("/messages/"):
                self._handle_messages_request(client_socket, client_address, path, headers, body)
            else:
                self._handle_default_request(client_socket)
        
        except Exception as e:
            MCPLogger.log("Error", f"Error handling client: {e}")
            try:
                client_socket.close()
            except:
                pass
    
    def _handle_sse_request(self, client_socket: socket.socket, client_address: tuple) -> None:
        """Handle an SSE connection request."""
        # Create new session
        session_id = self._create_session(client_socket, client_address)
        MCPLogger.log("New SSE connection", f"from {client_address}, session_id={session_id}")
        
        # Send SSE response headers
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: keep-alive\r\n"
            "X-Accel-Buffering: no\r\n"
            "Content-Type: text/event-stream; charset=utf-8\r\n"
            "\r\n"
        )
        client_socket.sendall(response.encode('utf-8'))
        
        # Send initial endpoint event
        protocol = "https" if self.cert_path else "http"
        endpoint_url = f"/messages/?session_id={session_id}"
        endpoint_event = f"event: endpoint\r\ndata: {endpoint_url}\r\n\r\n"
        client_socket.sendall(endpoint_event.encode('utf-8'))
        
        # Start ping thread
        self._start_ping_thread(session_id)
    
    def _handle_messages_request(
        self,
        client_socket: socket.socket,
        client_address: tuple,
        path: str,
        headers: Dict[str, str],
        body: str
    ) -> None:
        """Handle a messages endpoint request."""
        # Parse query parameters
        query_start = path.find("?")
        if query_start != -1:
            query_string = path[query_start + 1:]
            query_params = urllib.parse.parse_qs(query_string)
            session_ids = query_params.get("session_id", [])
            
            if session_ids and session_ids[0] in self.active_sessions:
                session_id = session_ids[0]
                
                # Process JSON-RPC request if content type matches
                if headers.get("content-type") == "application/json":
                    self._handle_jsonrpc_request(session_id, body)
                
                # Send 202 Accepted
                response = (
                    "HTTP/1.1 202 Accepted\r\n"
                    "Content-Type: text/plain\r\n"
                    "Content-Length: 0\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                )
            else:
                # Session not found
                response = (
                    "HTTP/1.1 404 Not Found\r\n"
                    "Content-Type: text/plain\r\n"
                    "Content-Length: 13\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                    "Invalid session"
                )
        else:
            # Missing session ID
            response = (
                "HTTP/1.1 400 Bad Request\r\n"
                "Content-Type: text/plain\r\n"
                "Content-Length: 22\r\n"
                "Connection: close\r\n"
                "\r\n"
                "Missing session_id param"
            )
        
        client_socket.sendall(response.encode('utf-8'))
        client_socket.close()
    
    def _handle_default_request(self, client_socket: socket.socket) -> None:
        """Handle requests to other endpoints."""
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: 11\r\n"
            "Connection: close\r\n"
            "\r\n"
            "Hello World"
        )
        client_socket.sendall(response.encode('utf-8'))
        client_socket.close()
    
    def serve_forever(self) -> None:
        """Start the server and run until interrupted."""
        self.running = True
        
        try:
            # Create server socket
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind((self.host, self.port))
            server.listen(5)
            
            # Setup TLS if needed
            if self.cert_path and self.key_path:
                context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                try:
                    context.load_cert_chain(self.cert_path, self.key_path)
                except FileNotFoundError as e:
                    missing_file = e.filename or "unknown"
                    raise FileNotFoundError(
                        f"Certificate file not found: {missing_file}. "
                        f"Looking for cert_path='{self.cert_path}' and key_path='{self.key_path}'"
                    ) from e
                server_socket = context.wrap_socket(server, server_side=True)
                protocol = "HTTPS"
            else:
                server_socket = server
                protocol = "HTTP"
            
            # Set timeout for clean shutdown
            server_socket.settimeout(1.0)
            
            MCPLogger.log("Server started", f"{protocol} on {self.host}:{self.port}")
            
            while self.running:
                try:
                    client, addr = server_socket.accept()
                    MCPLogger.log("Connect", f"from {addr}")
                    
                    # Handle client in a new thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client, addr)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        MCPLogger.log("Error", f"Accept failed: {e}")
            
        except Exception as e:
            MCPLogger.log("Fatal error", f"Server failed: {e}")
        finally:
            self.shutdown()
    
    def shutdown(self) -> None:
        """Shutdown the server gracefully."""
        self.running = False
        
        # Close all active sessions
        for session_id in list(self.active_sessions.keys()):
            self._remove_session(session_id)
        
        MCPLogger.log("Server", "Shutdown complete")