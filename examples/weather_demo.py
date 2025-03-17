#!/usr/bin/env python3
"""
Weather MCP Server Demo
A demonstration of the easy_mcp module implementing a weather service.
This shows how to create a Model Context Protocol server with custom tools.
"""

import os
import json
import http.client
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

# We'll import our MCP server module (to be written)
from easy_mcp.server import MCPServer

class WeatherService:
    """
    Weather service implementation that handles the actual weather data fetching
    and processing. Separated from server logic for clean separation of concerns.
    """
    
    def __init__(self):
        """Initialize the weather service"""
        self.api_base = "api.open-meteo.com"
    
    def fetch_forecast(self, latitude: float, longitude: float) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Fetch weather forecast from Open-Meteo API.
        
        Args:
            latitude: Location latitude
            longitude: Location longitude
            
        Returns:
            Tuple of (success, message, data)
            - success: True if fetch was successful
            - message: Error message if not successful, or success message
            - data: Weather data if successful, None if failed
        """
        try:
            # Create connection to Open-Meteo API
            conn = http.client.HTTPSConnection(self.api_base)
            
            # Set up query parameters
            params = {
                "latitude": latitude,
                "longitude": longitude,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,windspeed_10m_max",
                "timezone": "auto",
                "forecast_days": 5
            }
            
            # Build query string
            query = "&".join([f"{k}={v}" for k, v in params.items()])
            
            # Make the request
            conn.request(
                "GET", 
                f"/v1/forecast?{query}",
                headers={"User-Agent": "WeatherMCPDemo/1.0"}
            )
            
            # Get the response
            response = conn.getresponse()
            
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                conn.close()
                return True, "Successfully fetched forecast", data
            else:
                error = f"API error: HTTP {response.status}"
                conn.close()
                return False, error, None
                
        except json.JSONDecodeError as e:
            return False, f"Failed to parse weather data: {e}", None
        except Exception as e:
            return False, f"Failed to fetch weather data: {e}", None
    
    def format_forecast(self, data: Dict[str, Any]) -> str:
        """
        Format weather data into a human-readable forecast.
        
        Args:
            data: Weather data from Open-Meteo API
            
        Returns:
            Formatted forecast text
        """
        try:
            daily = data.get('daily', {})
            if not daily:
                return "Error: No daily forecast data available"
            
            # Get the arrays of data
            dates = daily.get('time', [])
            temp_max = daily.get('temperature_2m_max', [])
            temp_min = daily.get('temperature_2m_min', [])
            precip_prob = daily.get('precipitation_probability_max', [])
            wind_speed = daily.get('windspeed_10m_max', [])
            
            # Build the forecast text
            forecast = []
            for i in range(len(dates)):
                day = datetime.fromisoformat(dates[i]).strftime('%A')
                forecast.append(f"""
{day}:
Temperature: {temp_min[i]}°C to {temp_max[i]}°C
Wind: up to {wind_speed[i]} km/h
Precipitation Probability: {precip_prob[i]}%
""".strip())
            
            return "\n\n---\n\n".join(forecast)
            
        except Exception as e:
            return f"Error formatting forecast: {e}"

class WeatherMCPDemo:
    """
    Weather demonstration using Model Context Protocol.
    Shows how to create a server with custom tools for weather data.
    """
    
    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 9443,
        cert_path: Optional[str] = None,
        key_path: Optional[str] = None,
        public_hostname: Optional[str] = None
    ):
        """
        Initialize the Weather MCP Demo server.
        
        Args:
            host: Host to bind to
            port: Port to listen on
            cert_path: Path to SSL certificate (for HTTPS)
            key_path: Path to SSL private key (for HTTPS)
            public_hostname: Public hostname for SSE connections (e.g. 127-0-0-1.statw.com)
                           If None, will use host for HTTP or 127-0-0-1.statw.com for HTTPS
        """
        # Determine the public hostname
        if public_hostname is None:
            # For HTTPS, use statw.com domain, otherwise use direct host
            if cert_path is not None:
                public_hostname = "127-0-0-1.statw.com"
            else:
                public_hostname = host
        
        # Create the MCP server
        self.server = MCPServer(
            host=host,
            port=port,
            cert_path=cert_path,
            key_path=key_path,
            public_hostname=public_hostname,
            server_info={
                "name": "weather",
                "version": "1.4.1"
            }
        )
        
        # Create our weather service
        self.weather = WeatherService()
        
        # Register our tools
        self._register_tools()
    
    def _register_tools(self):
        """Register weather-specific tools with the server"""
        
        # Register the forecast tool
        self.server.register_tool(
            name="x_get_forecast",
            description="Get weather forecast for a location.\n\n"
                      "Args:\n"
                      "    latitude: Latitude of the location\n"
                      "    longitude: Longitude of the location\n",
            input_schema={
                "properties": {
                    "latitude": {"title": "Latitude", "type": "number"},
                    "longitude": {"title": "Longitude", "type": "number"}
                },
                "required": ["latitude", "longitude"],
                "title": "get_forecastArguments",
                "type": "object"
            },
            handler=self._handle_get_forecast
        )
        
        # Register the alerts tool
        self.server.register_tool(
            name="x_get_alerts",
            description="Get weather alerts for a US state.\n\n"
                      "Args:\n"
                      "    state: Two-letter US state code (e.g. CA, NY)\n",
            input_schema={
                "properties": {
                    "state": {"title": "State", "type": "string"}
                },
                "required": ["state"],
                "title": "get_alertsArguments",
                "type": "object"
            },
            handler=self._handle_get_alerts
        )
    
    def _handle_get_forecast(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle forecast request.
        
        Args:
            params: Tool parameters including latitude and longitude
            
        Returns:
            Response data for the client
        """
        latitude = params.get('latitude')
        longitude = params.get('longitude')
        
        # Fetch the forecast
        success, message, data = self.weather.fetch_forecast(latitude, longitude)
        
        if success and data:
            # Format the forecast text
            forecast_text = self.weather.format_forecast(data)
            
            # Return formatted response
            return {
                "content": [
                    {
                        "type": "text",
                        "text": forecast_text
                    }
                ],
                "isError": False
            }
        else:
            # Return error response
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: {message}"
                    }
                ],
                "isError": True
            }
    
    def _handle_get_alerts(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle alerts request.
        
        Args:
            params: Tool parameters including state code
            
        Returns:
            Response data for the client
        """
        state = params.get('state', '').upper()
        
        # For now, we'll return a message that alerts are not yet implemented
        # This is better than returning mock data
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Weather alerts for {state} are not yet implemented. "
                           "This feature will be available in a future update."
                }
            ],
            "isError": False
        }
    
    def serve_forever(self):
        """Start the server and run until interrupted"""
        self.server.serve_forever()
    
    def shutdown(self):
        """Shutdown the server gracefully"""
        self.server.shutdown()

def main():
    """Run the weather demo server"""
    import argparse
    parser = argparse.ArgumentParser(description='Weather MCP Server Demo')
    parser.add_argument('--http', action='store_true', help='Run in HTTP mode (no TLS)')
    parser.add_argument('--port', type=int, default=9443, help='Port to listen on')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to')
    parser.add_argument('--hostname', help='Public hostname for SSE connections')
    args = parser.parse_args()

    # Set up certificate paths if using HTTPS
    if not args.http:
        # Update to use the correct path to certs in the easy_mcp package
        cert_dir = os.path.join(os.path.dirname(__file__), '..', 'src', 'easy_mcp', 'certs')
        cert_path = os.path.join(cert_dir, 'fullchain1.pem')
        key_path = os.path.join(cert_dir, 'privkey1.pem')
        # Default to statw.com domain for HTTPS if no hostname specified
        public_hostname = args.hostname or "127-0-0-1.statw.com"
    else:
        cert_path = None
        key_path = None
        # For HTTP, use provided hostname or fall back to host
        public_hostname = args.hostname

    # Create and start the server
    server = WeatherMCPDemo(
        host=args.host,
        port=args.port,
        cert_path=cert_path,
        key_path=key_path,
        public_hostname=public_hostname
    )
    
    try:
        protocol = "HTTPS" if not args.http else "HTTP"
        url_host = public_hostname or args.host
        print(f"Starting Weather MCP Demo server:")
        print(f"- Internal: {protocol}://{args.host}:{args.port}/")
        print(f"- Public:   {protocol}://{url_host}:{args.port}/")
        print("Press Ctrl+C to stop")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()