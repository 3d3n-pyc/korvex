"""AWS Lambda entry point: Mangum turns Lambda events into ASGI calls.

Install: pip install korvex mangum
Lambda handler setting: lambda_handler.handler
"""

from main import app
from mangum import Mangum

handler = Mangum(app)
