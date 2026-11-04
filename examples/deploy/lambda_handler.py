"""AWS Lambda entry point: Mangum turns Lambda events into ASGI calls.

Install: pip install korvex mangum
Lambda handler setting: lambda_handler.handler
"""

from mangum import Mangum

from main import app

handler = Mangum(app)
