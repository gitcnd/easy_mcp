from setuptools import setup, find_packages
import os

# Include certificate files in the package
package_data = {
    'easy_mcp': ['certs/*.pem'],
}

setup(
    name="easy_mcp",
    version="0.1.0",
    package_dir={"": "src"},  # Tell setuptools packages are under src
    packages=find_packages(where="src"),  # Find packages in src directory
    package_data=package_data,
    include_package_data=True,
    install_requires=[
        # raw_sse_mcp_server.py only uses standard library modules
    ],
    description="Easy Model Context Protocol server with SSE support",
    author="MCP Team",
    python_requires=">=3.7",
) 